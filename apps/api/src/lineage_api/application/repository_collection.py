from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from lineage_api.application.repository_sources import (
    RepositorySnapshot,
    validate_repository_identity,
)
from lineage_api.services.analyzer_registry import (
    AnalyzerRegistry,
    AnalyzerSelection,
    AnalyzerSelectionError,
    canonical_source_metadata,
    deterministic_checkout_event_id,
)


_EXACT_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_STATUS_REASON = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,127})")
_MAX_SIGNED_DELIVERY_BYTES = 64 * 1024 * 1024


class RepositoryCollectionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message[:160])
        self.code = code


def _bounded_text(value: str, *, field: str, limit: int) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode()) > limit
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{field} must be a bounded non-empty determinant")


def _detached_status_reasons(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 128:
        raise ValueError("analysis status reasons must be a bounded list")
    if any(
        not isinstance(reason, str) or _STATUS_REASON.fullmatch(reason) is None
        for reason in value
    ):
        raise ValueError("analysis status reason must be a bounded code")
    return list(value)


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    origin: str
    repository: str
    revision: str
    environment: str
    platform: str
    system: str

    def __post_init__(self) -> None:
        _bounded_text(self.origin, field="origin", limit=2_048)
        validate_repository_identity(self.origin, self.repository)
        _bounded_text(self.revision, field="revision", limit=64)
        if _EXACT_REVISION.fullmatch(self.revision) is None:
            raise ValueError("revision must be an exact lowercase digest")
        for field in ("environment", "platform", "system"):
            _bounded_text(getattr(self, field), field=field, limit=128)


@dataclass(frozen=True, slots=True)
class AnalyzerIdentity:
    analyzer_pack: str
    ruleset: str
    source_kind: str
    framework: str
    schema_profile: str

    def __post_init__(self) -> None:
        for field in (
            "analyzer_pack",
            "ruleset",
            "source_kind",
            "framework",
            "schema_profile",
        ):
            _bounded_text(getattr(self, field), field=field, limit=128)

    def selection(self) -> AnalyzerSelection:
        return AnalyzerSelection(
            analyzer_pack=self.analyzer_pack,
            ruleset=self.ruleset,
            source_kind=self.source_kind,
            framework=self.framework,
            schema_profile=self.schema_profile,
        )


@dataclass(frozen=True, slots=True)
class RepositoryCollectionDescriptor:
    repository: RepositoryIdentity
    analyzer: AnalyzerIdentity
    snapshot: RepositorySnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.repository, RepositoryIdentity):
            raise TypeError("repository identity is required")
        if not isinstance(self.analyzer, AnalyzerIdentity):
            raise TypeError("analyzer identity is required")
        if not isinstance(self.snapshot, RepositorySnapshot):
            raise TypeError("materialized repository snapshot is required")


@dataclass(frozen=True, slots=True)
class RepositoryPushDelivery:
    canonical_body: bytes
    signature: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.canonical_body, bytes)
            or not self.canonical_body
            or len(self.canonical_body) > _MAX_SIGNED_DELIVERY_BYTES
        ):
            raise ValueError("signed repository delivery body is outside bounds")
        try:
            parsed = json.loads(self.canonical_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("signed repository delivery body is invalid") from None
        if (
            not isinstance(parsed, dict)
            or json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode()
            != self.canonical_body
        ):
            raise ValueError("signed repository delivery body is not canonical")

    @property
    def payload(self) -> dict[str, Any]:
        payload = json.loads(self.canonical_body)
        if not isinstance(payload, dict):
            raise ValueError("signed repository delivery body is invalid")
        return payload


RepositoryPushProcessor = Callable[
    [RepositorySnapshot, RepositoryPushDelivery], dict[str, Any]
]


class RepositoryCollectionService:
    def __init__(
        self,
        *,
        webhook_secret: str,
        process_push: RepositoryPushProcessor,
        analyzer_registry: AnalyzerRegistry | None = None,
    ) -> None:
        if not isinstance(webhook_secret, str) or not webhook_secret:
            raise ValueError("webhook signing secret is required")
        self._secret = webhook_secret.encode()
        self._process_push = process_push
        self._analyzers = analyzer_registry or AnalyzerRegistry.default()

    def collect(
        self, descriptor: RepositoryCollectionDescriptor, *, runtime_execution: bool = False
    ) -> dict[str, Any]:
        self._validate(descriptor)
        snapshot = descriptor.snapshot
        metadata = canonical_source_metadata(
            snapshot, schema_profile=descriptor.analyzer.schema_profile
        )
        payload = {
            "eventId": deterministic_checkout_event_id(
                metadata,
                descriptor.repository.repository,
                descriptor.repository.environment,
                descriptor.repository.system,
            ),
            "eventType": "repo.push",
            "repo": descriptor.repository.repository,
            "digest": descriptor.repository.revision,
            "env": descriptor.repository.environment,
            "system": descriptor.repository.system,
            "changedFiles": list(snapshot.paths),
            "repositorySource": metadata,
            "receivedAt": "1970-01-01T00:00:00Z",
        }
        if runtime_execution:
            payload["runtimeExecution"] = True
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self._secret, canonical, hashlib.sha256).hexdigest()
        try:
            result = self._process_push(
                snapshot, RepositoryPushDelivery(canonical, f"sha256={signature}")
            )
            return self._summarize(result, snapshot.scope_digest, snapshot.revision)
        except Exception:
            raise RepositoryCollectionError(
                "PIPELINE_FAILED", "repository collection failed"
            ) from None

    def _validate(self, descriptor: RepositoryCollectionDescriptor) -> None:
        if not isinstance(descriptor, RepositoryCollectionDescriptor):
            raise TypeError("repository collection descriptor is required")
        repository = descriptor.repository
        analyzer = descriptor.analyzer
        snapshot = descriptor.snapshot
        repository_values = (
            repository.origin,
            repository.repository,
            repository.revision,
            repository.environment,
            repository.platform,
            repository.system,
        )
        snapshot_values = (
            snapshot.origin,
            snapshot.repository,
            snapshot.revision,
            snapshot.environment,
            snapshot.platform,
            snapshot.system,
        )
        if repository_values != snapshot_values:
            raise ValueError("repository identity does not match materialized snapshot")
        if (analyzer.analyzer_pack, analyzer.ruleset) != (
            snapshot.analyzer_pack,
            snapshot.ruleset,
        ):
            raise AnalyzerSelectionError(
                "SOURCE_ANALYZER_MISMATCH",
                "analyzer identity does not match materialized snapshot",
            )
        self._analyzers.resolve(analyzer.selection())
        if repository.platform != analyzer.schema_profile:
            raise AnalyzerSelectionError(
                "PROFILE_PLATFORM_MISMATCH",
                "platform and schema profile must match for exact checkout collection",
            )

    @staticmethod
    def _summarize(
        result: dict[str, Any], scope_digest: str, revision: str
    ) -> dict[str, Any]:
        run = result.get("run") if isinstance(result.get("run"), dict) else {}
        proposal = (
            result.get("proposal")
            if isinstance(result.get("proposal"), dict)
            else {}
        )
        command = (
            result.get("command") if isinstance(result.get("command"), dict) else {}
        )
        analysis = (
            result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
        )
        coverage = (
            result.get("coverageManifest")
            if isinstance(result.get("coverageManifest"), dict)
            else None
        )
        coverage_summary = (
            {
                "manifestId": coverage.get("manifestId"),
                "state": coverage.get("state"),
                "determinantDigest": coverage.get("determinantDigest"),
                "sourceScopeDispositionDigest": coverage.get(
                    "sourceScopeDispositionDigest"
                ),
                "counts": {
                    "expected": len(coverage.get("expectedScope", [])),
                    "completed": len(coverage.get("completedScope", [])),
                    "skipped": len(coverage.get("skippedScope", [])),
                    "unsupported": len(coverage.get("unsupportedScope", [])),
                    "failed": len(coverage.get("failedScope", [])),
                },
            }
            if coverage is not None
            else None
        )
        command_id = command.get("commandId")
        return {
            "collectionId": command_id,
            "commandId": command_id,
            "statusUrl": (
                f"/api/collections/{command_id}"
                if isinstance(command_id, str) and command_id
                else None
            ),
            "outcome": result.get("outcome"),
            "reasonCode": result.get("reason"),
            "commandStatus": command.get("status"),
            "determinantDigest": command.get("determinantDigest"),
            "revision": revision,
            "scopeDigest": scope_digest,
            "runId": run.get("runId"),
            "runStatus": run.get("state"),
            "stages": [
                stage.get("stage")
                for stage in run.get("stages", [])
                if isinstance(stage, dict)
            ],
            "proposalId": proposal.get("proposalId"),
            "proposalStatus": proposal.get("state"),
            "runtimeStatus": result.get("runtimeStatus", "NOT_PROVIDED"),
            "runtimeReasons": (
                list(result.get("runtimeReasons"))
                if isinstance(result.get("runtimeReasons"), list)
                and result.get("runtimeReasons")
                else ["not-requested"]
            ),
            "analysisStatus": analysis.get("status"),
            "statusReasons": _detached_status_reasons(
                analysis.get("statusReasons", [])
            ),
            "coverageManifest": coverage_summary,
            "counts": {
                "edges": analysis.get("edgeCount", 0),
                "reads": analysis.get("readCount", 0),
                "writes": analysis.get("writeCount", 0),
                "residue": analysis.get("residueCount", 0),
                "unresolved": analysis.get("unresolvedCount", 0),
            },
        }
