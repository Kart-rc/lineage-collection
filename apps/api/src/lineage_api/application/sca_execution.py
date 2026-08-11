from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any, Mapping

from lineage_api.application.ports import ArtifactStorePort, SourceArchivePort
from lineage_api.application.nightly_execution import validate_nightly_context
from lineage_api.application.stage_execution import (
    MAX_STAGE_DOCUMENT_BYTES,
    StageExecutionContext,
    StageExecutionResult,
    validate_artifact_reference,
)
from lineage_api.services.resolver import ResolveContext, Resolver
from lineage_api.services.sca import ScaAnalyzer


_COVERAGE_FIELDS = frozenset(
    {
        "expectedScope",
        "completedScope",
        "reusedScope",
        "skippedScope",
        "unsupportedScope",
        "quarantinedScope",
        "failedScope",
    }
)
_MAX_SOURCE_ARCHIVE_BYTES = 256 * 1024 * 1024
_MAX_CATALOG_BYTES = 50 * 1024 * 1024
_MAX_EVIDENCE_ITEMS = 10_000


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > 2_048:
        raise ValueError(f"invalid {name}")
    return value


def _path(value: object) -> str:
    text = _text("repository path", value)
    parsed = PurePosixPath(text)
    if text.startswith("/") or "\\" in text or ".." in parsed.parts or text == ".":
        raise ValueError("repository paths must be normalized and relative")
    return parsed.as_posix()


def _paths(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 10_000:
        raise ValueError("SCA paths must be a bounded array")
    normalized = [_path(item) for item in value]
    if normalized != sorted(set(normalized)):
        raise ValueError("SCA paths must be sorted and unique")
    return normalized


class ScaStageUseCase:
    def __init__(
        self, artifacts: ArtifactStorePort, sources: SourceArchivePort
    ) -> None:
        self._artifacts = artifacts
        self._sources = sources

    def execute(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if (context.workflow_kind, context.stage_id) not in {
            ("BASELINE", "B5"),
            ("INCREMENTAL", "I5"),
            ("NIGHTLY", "N2"),
        }:
            raise ValueError("SCA use case received an unsupported stage")
        if (
            not isinstance(input_document, Mapping)
            or input_document.get("schemaVersion") != "1.0.0"
            or input_document.get("artifactType") != "sca-work-unit"
        ):
            raise ValueError("SCA requires a versioned work unit")
        if input_document.get("pack") != "python-ast":
            raise ValueError("unsupported SCA analyzer pack")
        work_unit_id = _text("SCA work unit ID", input_document.get("workUnitId"))
        repository = _text("SCA repository", input_document.get("repository"))
        artifact_digest = _text(
            "SCA artifact digest", input_document.get("artifactDigest")
        )
        environment = _text("SCA environment", input_document.get("environment"))
        platform = _text("SCA platform", input_document.get("platform"))
        system = _text("SCA system", input_document.get("system"))
        ruleset_version = _text(
            "SCA ruleset version", input_document.get("rulesetVersion")
        )
        expected_resolver = _text(
            "SCA resolver version", input_document.get("resolverVersion")
        )
        expected_snapshot = _text(
            "SCA catalog snapshot", input_document.get("catalogSnapshotId")
        )
        if input_document.get("correlationId") != context.correlation_id:
            raise ValueError("SCA work unit correlation does not match its command")
        paths = _paths(input_document.get("paths"))
        source_ref = validate_artifact_reference(input_document.get("repositorySource"))
        catalog_ref = validate_artifact_reference(
            input_document.get("catalogSnapshotRef")
        )
        if source_ref["sizeBytes"] > _MAX_SOURCE_ARCHIVE_BYTES:
            raise ValueError("SCA source archive exceeds its size bound")
        if catalog_ref["sizeBytes"] > _MAX_CATALOG_BYTES:
            raise ValueError("SCA catalog snapshot exceeds its size bound")
        active_base_version = _text(
            "SCA active base", input_document.get("activeBaseVersion")
        )
        active_base_fence = input_document.get("activeBaseFence")
        if (
            not isinstance(active_base_fence, int)
            or isinstance(active_base_fence, bool)
            or active_base_fence < 0
        ):
            raise ValueError("SCA active base fence is invalid")
        accepted_at = _text("SCA accepted time", input_document.get("acceptedAt"))
        runtime_references = input_document.get("runtimeManifestRefs", [])
        if not isinstance(runtime_references, list) or len(runtime_references) > 100:
            raise ValueError("SCA runtime manifest references must be a bounded array")
        runtime_manifest_refs = [
            validate_artifact_reference(reference) for reference in runtime_references
        ]
        coverage = input_document.get("coverage")
        if (
            not isinstance(coverage, Mapping)
            or "expectedScope" not in coverage
            or set(coverage) - _COVERAGE_FIELDS
        ):
            raise ValueError("SCA work unit requires bounded coverage accounting")
        normalized_coverage = dict(coverage)
        for name, value in normalized_coverage.items():
            _text("SCA coverage field", name)
            if not isinstance(value, list) or len(value) > 10_000:
                raise ValueError("SCA coverage fields must be bounded arrays")
            normalized_coverage[name] = _paths(value)
        expected_scope = normalized_coverage.get("expectedScope")
        if not isinstance(expected_scope, list) or not set(paths) <= set(expected_scope):
            raise ValueError("SCA work paths must be contained in expected coverage")
        prior_completed = normalized_coverage.get("completedScope", [])
        if prior_completed not in ([], paths):
            raise ValueError("SCA completed coverage does not match its work paths")
        other_accounted = {
            item
            for name in (
                "reusedScope",
                "skippedScope",
                "unsupportedScope",
                "quarantinedScope",
                "failedScope",
            )
            for item in normalized_coverage.get(name, [])
        }
        if set(paths) & other_accounted:
            raise ValueError("SCA work paths overlap non-executed coverage")
        normalized_coverage["completedScope"] = paths
        for name in (
            "reusedScope",
            "skippedScope",
            "unsupportedScope",
            "quarantinedScope",
            "failedScope",
        ):
            normalized_coverage.setdefault(name, [])
        nightly_context: dict[str, Any] | None = None
        if context.workflow_kind == "NIGHTLY":
            nightly_context = validate_nightly_context(input_document.get("nightly"))
        elif "nightly" in input_document:
            raise ValueError("only Nightly SCA may carry reconciliation context")
        catalog = self._artifacts.get(catalog_ref)
        if not isinstance(catalog, dict):
            raise ValueError("SCA catalog snapshot must be an object")
        resolver = Resolver(catalog)
        if (
            resolver.snapshot_id != expected_snapshot
            or resolver.resolver_version != expected_resolver
        ):
            raise ValueError("SCA catalog identity does not match its determinant pins")
        raw_config = input_document.get("resolverConfig", {})
        if not isinstance(raw_config, Mapping) or len(raw_config) > 100:
            raise ValueError("SCA resolver configuration must be a bounded object")
        resolver_config = {
            _text("resolver configuration key", key): _text(
                "resolver configuration value", value
            )
            for key, value in raw_config.items()
        }
        analyzer = ScaAnalyzer(resolver, ruleset_version)
        with self._sources.materialize(source_ref) as repository_root:
            evidence = analyzer.analyze(
                repository_root=repository_root,
                repo=repository,
                digest=artifact_digest,
                scope_paths=tuple(paths),
                resolver_context=ResolveContext(
                    env=environment,
                    platform=platform,
                    system=system,
                    repo=repository,
                    digest=artifact_digest,
                    config=resolver_config,
                    snapshot_id=expected_snapshot,
                ),
                run_id=work_unit_id,
                correlation_id=context.correlation_id,
            )
        evidence_document = evidence.as_dict()
        if (
            len(evidence_document["edges"]) > _MAX_EVIDENCE_ITEMS
            or len(evidence_document["residue"]) > _MAX_EVIDENCE_ITEMS
            or len(
                json.dumps(
                    evidence_document,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            )
            > MAX_STAGE_DOCUMENT_BYTES
        ):
            raise ValueError("SCA evidence exceeds its bounded output contract")
        evidence_ref = validate_artifact_reference(
            self._artifacts.put(
                "sca-evidence",
                f"commands/{context.command_id}/sca/{work_unit_id}/evidence.json",
                evidence_document,
                "1.0.0",
            )
        )
        assertions = []
        for edge in evidence_document["edges"]:
            assertions.append(
                {
                    "provenanceId": edge["provenanceId"],
                    "from": edge["from"],
                    "to": edge["to"],
                    "edgeType": edge["edgeType"],
                    "transform": edge["transform"],
                    "mechanism": "SCA",
                    "exact": bool(edge["exact"]),
                    "evidenceRef": evidence_ref,
                    "repo": edge["repo"],
                    "runId": edge["runId"],
                    "correlationId": edge["correlationId"],
                    "sessionComplete": True,
                    "citation": edge["evidence"],
                }
            )
        assertion_document = {
            "schemaVersion": "1.0.0",
            "artifactType": "assertion-set",
            "workUnitId": work_unit_id,
            "assertions": assertions,
        }
        if len(
            json.dumps(
                assertion_document,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ) > MAX_STAGE_DOCUMENT_BYTES:
            raise ValueError("SCA assertion set exceeds its bounded output contract")
        assertion_ref = validate_artifact_reference(
            self._artifacts.put(
                "assertion-set",
                f"commands/{context.command_id}/sca/{work_unit_id}/assertions.json",
                assertion_document,
                "1.0.0",
            )
        )
        residue_ref = validate_artifact_reference(
            self._artifacts.put(
                "sca-residue",
                f"commands/{context.command_id}/sca/{work_unit_id}/residue.json",
                {
                    "schemaVersion": "1.0.0",
                    "artifactType": "sca-residue",
                    "workUnitId": work_unit_id,
                    "residue": evidence_document["residue"],
                },
                "1.0.0",
            )
        )
        common: dict[str, Any] = {
            "repository": repository,
            "artifactDigest": artifact_digest,
            "environment": environment,
            "platform": platform,
            "system": system,
            "repositorySource": source_ref,
            "catalogSnapshotRef": catalog_ref,
            "catalogSnapshotId": expected_snapshot,
            "resolverVersion": expected_resolver,
            "rulesetVersion": ruleset_version,
            "activeBaseVersion": active_base_version,
            "activeBaseFence": active_base_fence,
            "acceptedAt": accepted_at,
            "runtimeManifestRefs": runtime_manifest_refs,
            "assertionRefs": [assertion_ref],
            "residueRefs": [residue_ref],
            "coverage": normalized_coverage,
        }
        if nightly_context is not None:
            common["nightly"] = nightly_context
        return StageExecutionResult(
            artifact_kind="sca-stage-result",
            schema_version="1.0.0",
            document={
                "schemaVersion": "1.0.0",
                "artifactType": "sca-stage-result",
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "context": common,
                "workUnitId": work_unit_id,
                "evidenceRef": evidence_ref,
                "assertionRef": assertion_ref,
                "residueRef": residue_ref,
                "stats": evidence_document["stats"],
            },
        )


__all__ = ["ScaStageUseCase"]
