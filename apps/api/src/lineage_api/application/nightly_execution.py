from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Mapping

from lineage_api.application.consolidation import derive_consolidation, edge_key_for
from lineage_api.application.ports import (
    ArtifactStorePort,
    NightlyControlPort,
    StageProjectionPort,
)
from lineage_api.application.stage_execution import (
    MAX_STAGE_DOCUMENT_BYTES,
    StageExecutionContext,
    StageExecutionResult,
    validate_artifact_reference,
)
from lineage_api.domain.urns import LineageUrn


_STAGES = {
    "N1": ("sca-work-unit", "nightly-reconciliation-intent"),
    "N3": ("nightly-projection-verification", "sca-stage-result"),
    "N4": ("nightly-cache-drain", "nightly-projection-verification"),
    "N5": ("nightly-audit-decision", "nightly-cache-drain"),
    "N6": ("nightly-reconciliation-result", "nightly-audit-decision"),
}
_PRIOR_STAGES = {"N3": "N2", "N4": "N3", "N5": "N4", "N6": "N5"}
_REFERENCE_KEYS = {"bucket", "key", "versionId", "sha256", "sizeBytes"}
_PROJECTION_KEYS = {"edgeId", "source", "target", "type"}
_COVERAGE_FIELDS = (
    "completedScope",
    "reusedScope",
    "skippedScope",
    "unsupportedScope",
    "quarantinedScope",
    "failedScope",
)


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _text(name: str, value: object, *, limit: int = 2_048) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > limit:
        raise ValueError(f"invalid {name}")
    return value


def _integer(name: str, value: object, *, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"invalid {name}")
    return value


def _timestamp(name: str, value: object) -> str:
    text = _text(name, value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid {name}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"invalid {name}")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _path(value: object) -> str:
    text = _text("Nightly sample path", value, limit=1_024)
    parsed = PurePosixPath(text)
    if text.startswith("/") or "\\" in text or ".." in parsed.parts or text == ".":
        raise ValueError("Nightly sample paths must be normalized and relative")
    return parsed.as_posix()


def _paths(name: str, value: object, *, maximum: int = 10_000) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{name} must be a bounded non-empty array")
    paths = [_path(item) for item in value]
    if paths != sorted(set(paths)):
        raise ValueError(f"{name} must be sorted and unique")
    return paths


def _ids(name: str, value: object, *, maximum: int = 10_000) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{name} must be a bounded array")
    items = [_text(name, item, limit=512) for item in value]
    if items != sorted(set(items)):
        raise ValueError(f"{name} must be sorted and unique")
    return items


def _urns(name: str, value: object, *, maximum: int = 10_000) -> list[str]:
    items = _ids(name, value, maximum=maximum)
    if not items:
        raise ValueError(f"{name} must not be empty")
    for item in items:
        LineageUrn.parse(item)
    return items


def _references(
    name: str, value: object, *, maximum: int = 1_000
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{name} must be a bounded reference array")
    references = [validate_artifact_reference(item) for item in value]
    identities = [
        tuple(reference[key] for key in sorted(_REFERENCE_KEYS))
        for reference in references
    ]
    if identities != sorted(set(identities)):
        raise ValueError(f"{name} must be sorted and unique")
    return references


def _coverage(value: object) -> dict[str, Any]:
    required = {"expectedScope", *_COVERAGE_FIELDS}
    if not isinstance(value, Mapping) or not required <= set(value):
        raise ValueError("Nightly coverage schema is invalid")
    expected = _paths("Nightly expected scope", value["expectedScope"])
    result: dict[str, Any] = {"expectedScope": expected}
    accounted: list[str] = []
    for field in _COVERAGE_FIELDS:
        raw = value[field]
        if not isinstance(raw, list):
            raise ValueError("Nightly coverage fields must be arrays")
        normalized = [_path(item) for item in raw]
        if normalized != sorted(set(normalized)):
            raise ValueError("Nightly coverage fields must be sorted and unique")
        result[field] = normalized
        accounted.extend(normalized)
    exact = expected == sorted(accounted)
    result["state"] = (
        "COMPLETE"
        if exact
        and not any(
            result[field]
            for field in ("unsupportedScope", "quarantinedScope", "failedScope")
        )
        else "INCOMPLETE"
    )
    return result


def validate_nightly_context(value: object) -> dict[str, Any]:
    required = {
        "runId",
        "sampleScopeUrns",
        "eventReconciliation",
        "staleCacheRef",
        "auditSampleRef",
        "pinnedGraphChecksum",
        "cacheDrainLimit",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("Nightly reconciliation context schema is invalid")
    reconciliation = value["eventReconciliation"]
    if not isinstance(reconciliation, Mapping) or set(reconciliation) != {
        "status",
        "acceptedCount",
        "receiptCount",
        "archiveCount",
        "missingReceiptEventIds",
        "missingArchiveEventIds",
        "orphanReceiptEventIds",
    }:
        raise ValueError("Nightly event reconciliation result is invalid")
    normalized_reconciliation = {
        "status": _text("Nightly reconciliation status", reconciliation["status"], limit=32),
        "acceptedCount": _integer(
            "Nightly accepted count", reconciliation["acceptedCount"], minimum=0, maximum=10_000
        ),
        "receiptCount": _integer(
            "Nightly receipt count", reconciliation["receiptCount"], minimum=0, maximum=10_000
        ),
        "archiveCount": _integer(
            "Nightly archive count", reconciliation["archiveCount"], minimum=0, maximum=10_000
        ),
        "missingReceiptEventIds": _ids(
            "Nightly missing receipt IDs", reconciliation["missingReceiptEventIds"]
        ),
        "missingArchiveEventIds": _ids(
            "Nightly missing archive IDs", reconciliation["missingArchiveEventIds"]
        ),
        "orphanReceiptEventIds": _ids(
            "Nightly orphan receipt IDs", reconciliation["orphanReceiptEventIds"]
        ),
    }
    expected_status = (
        "RECONCILED"
        if not any(
            normalized_reconciliation[name]
            for name in (
                "missingReceiptEventIds",
                "missingArchiveEventIds",
                "orphanReceiptEventIds",
            )
        )
        else "DRIFT"
    )
    if normalized_reconciliation["status"] != expected_status:
        raise ValueError("Nightly reconciliation status does not match its evidence")
    checksum = _text("Nightly pinned graph checksum", value["pinnedGraphChecksum"], limit=64)
    if len(checksum) != 64 or any(character not in "0123456789abcdef" for character in checksum):
        raise ValueError("Nightly pinned graph checksum is invalid")
    return {
        "runId": _text("Nightly run ID", value["runId"]),
        "sampleScopeUrns": _urns("Nightly sample scope URNs", value["sampleScopeUrns"]),
        "eventReconciliation": normalized_reconciliation,
        "staleCacheRef": validate_artifact_reference(value["staleCacheRef"]),
        "auditSampleRef": validate_artifact_reference(value["auditSampleRef"]),
        "pinnedGraphChecksum": checksum,
        "cacheDrainLimit": _integer(
            "Nightly cache drain limit", value["cacheDrainLimit"], minimum=1, maximum=50
        ),
    }


def _common(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Nightly stage is missing its context")
    common = {
        name: _text(f"Nightly {name}", value.get(name))
        for name in (
            "repository",
            "artifactDigest",
            "environment",
            "platform",
            "system",
            "catalogSnapshotId",
            "resolverVersion",
            "rulesetVersion",
            "activeBaseVersion",
        )
    }
    common["repositorySource"] = validate_artifact_reference(value.get("repositorySource"))
    common["catalogSnapshotRef"] = validate_artifact_reference(value.get("catalogSnapshotRef"))
    common["activeBaseFence"] = _integer(
        "Nightly active base fence", value.get("activeBaseFence"), minimum=0, maximum=2**63 - 1
    )
    common["acceptedAt"] = _timestamp("Nightly acceptedAt", value.get("acceptedAt"))
    common["coverage"] = _coverage(value.get("coverage"))
    common["assertionRefs"] = _references(
        "Nightly assertion refs", value.get("assertionRefs", [])
    )
    common["nightly"] = validate_nightly_context(value.get("nightly"))
    return common


def _projection_rows(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 100_000:
        raise ValueError("Nightly projection rows must be bounded")
    rows: list[dict[str, str]] = []
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != _PROJECTION_KEYS:
            raise ValueError("Nightly projection row schema is invalid")
        row = {
            key: _text(f"Nightly projection {key}", raw[key])
            for key in sorted(_PROJECTION_KEYS)
        }
        LineageUrn.parse(row["source"])
        LineageUrn.parse(row["target"])
        rows.append(row)
    rows.sort(
        key=lambda row: (row["edgeId"], row["source"], row["target"], row["type"])
    )
    identities = [_canonical(row) for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError("Nightly projection rows contain duplicates")
    return rows


def _assertions(
    artifacts: ArtifactStorePort,
    common: Mapping[str, Any],
    correlation_id: str,
) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    for reference in common["assertionRefs"]:
        body = artifacts.get(reference)
        if (
            not isinstance(body, Mapping)
            or body.get("schemaVersion") != "1.0.0"
            or body.get("artifactType") != "assertion-set"
            or not isinstance(body.get("assertions"), list)
            or len(assertions) + len(body["assertions"]) > 10_000
        ):
            raise ValueError("Nightly assertion set is invalid")
        for raw in body["assertions"]:
            required = {
                "provenanceId",
                "from",
                "to",
                "edgeType",
                "mechanism",
                "exact",
                "evidenceRef",
                "repo",
                "runId",
                "correlationId",
                "sessionComplete",
            }
            allowed = required | {"transform", "citation"}
            if (
                not isinstance(raw, Mapping)
                or not required <= set(raw)
                or set(raw) - allowed
            ):
                raise ValueError("Nightly assertion schema is invalid")
            sources = _urns("Nightly assertion sources", raw["from"], maximum=64)
            target = _text("Nightly assertion target", raw["to"])
            urns = [LineageUrn.parse(item) for item in (*sources, target)]
            edge_type = _text("Nightly assertion edge type", raw["edgeType"], limit=32)
            if edge_type not in {"DERIVES", "READS", "WRITES", "SAME_AS"}:
                raise ValueError("Nightly assertion edge type is unsupported")
            if (
                raw.get("mechanism") != "SCA"
                or raw.get("correlationId") != correlation_id
                or raw.get("repo") != common["repository"]
                or raw.get("runId") != common["nightly"]["runId"]
                or raw.get("sessionComplete") is not True
                or not isinstance(raw.get("exact"), bool)
            ):
                raise ValueError("Nightly clean assertion is unbound or incomplete")
            if any(urn.env != common["environment"] for urn in urns) or urns[-1].system != common[
                "system"
            ]:
                raise ValueError("Nightly clean assertion crosses its pinned scope")
            assertion = dict(raw)
            assertion["from"] = sources
            assertion["to"] = target
            assertion["edgeType"] = edge_type
            assertion["provenanceId"] = _text("Nightly provenance ID", raw["provenanceId"])
            assertion["evidenceRef"] = validate_artifact_reference(raw["evidenceRef"])
            assertions.append(assertion)
    by_id: dict[str, str] = {}
    normalized: list[dict[str, Any]] = []
    for assertion in sorted(assertions, key=lambda item: str(item["provenanceId"])):
        identity = str(assertion["provenanceId"])
        encoded = _canonical(assertion)
        if identity in by_id and by_id[identity] != encoded:
            raise ValueError("Nightly assertion provenance conflicts")
        if identity not in by_id:
            by_id[identity] = encoded
            normalized.append(assertion)
    return normalized


def _clean_edges(assertions: list[dict[str, Any]], system: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for assertion in assertions:
        edge_id = edge_key_for(assertion["from"], assertion["to"], assertion["edgeType"])
        grouped.setdefault(edge_id, []).append(assertion)
    edges: list[dict[str, Any]] = []
    for edge_id, provenance in sorted(grouped.items()):
        ordered = sorted(provenance, key=lambda item: item["provenanceId"])
        decision = derive_consolidation(ordered)
        first = ordered[0]
        if LineageUrn.parse(first["to"]).system != system:
            raise ValueError("Nightly clean edge crosses system ownership")
        edge = {
            "schemaVersion": "1.0.0",
            "edgeKey": edge_id,
            "version": 1,
            "from": first["from"],
            "to": first["to"],
            "edgeType": first["edgeType"],
            "band": decision.band,
            "corroboration": decision.corroboration,
            "status": decision.status,
            "provenance": ordered,
            "autoPublishable": decision.auto_publishable,
            "system": system,
        }
        if decision.transform is not None:
            edge["transform"] = decision.transform
        edges.append(edge)
    return edges


class NightlyStageUseCase:
    def __init__(
        self,
        artifacts: ArtifactStorePort,
        control: NightlyControlPort,
        projection: StageProjectionPort,
    ) -> None:
        self._artifacts = artifacts
        self._control = control
        self._projection = projection

    def execute(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if context.workflow_kind != "NIGHTLY" or context.stage_id not in _STAGES:
            raise ValueError("Nightly use case received an unsupported stage")
        if context.stage_id == "N1":
            return self._reconcile(input_document, context)
        expected = _STAGES[context.stage_id][1]
        if (
            not isinstance(input_document, Mapping)
            or input_document.get("schemaVersion") != "1.0.0"
            or input_document.get("artifactType") != expected
        ):
            raise ValueError(f"{context.stage_id} requires the exact prior Nightly artifact")
        if (
            input_document.get("workflowKind") != context.workflow_kind
            or input_document.get("workflowVersion") != context.workflow_version
            or input_document.get("stageId") != _PRIOR_STAGES[context.stage_id]
            or input_document.get("commandId") != context.command_id
            or input_document.get("correlationId") != context.correlation_id
        ):
            raise ValueError("Nightly prior artifact is not bound to this command")
        validate_artifact_reference(input_document.get("source"))
        common = _common(input_document.get("context"))
        if context.stage_id == "N3":
            return self._verify_projection(input_document, common, context)
        if context.stage_id == "N4":
            return self._drain_cache(input_document, common, context)
        if context.stage_id == "N5":
            return self._audit(input_document, common, context)
        return self._complete(input_document, common, context)

    def _reconcile(
        self, value: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        required = {
            "schemaVersion",
            "artifactType",
            "runId",
            "repository",
            "artifactDigest",
            "environment",
            "platform",
            "system",
            "repositorySource",
            "catalogSnapshotRef",
            "catalogSnapshotId",
            "resolverVersion",
            "rulesetVersion",
            "acceptedAt",
            "samplePaths",
            "sampleScopeUrns",
            "eventReconciliationRef",
            "staleCacheRef",
            "auditSampleRef",
            "cacheDrainLimit",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("N1 requires a closed Nightly reconciliation intent")
        if (
            value["schemaVersion"] != "1.0.0"
            or value["artifactType"] != "nightly-reconciliation-intent"
        ):
            raise ValueError("N1 Nightly intent version or type is invalid")
        run_id = _text("Nightly run ID", value["runId"])
        accepted_at = _timestamp("Nightly acceptedAt", value["acceptedAt"])
        environment = _text("Nightly environment", value["environment"])
        pointer = self._control.active_pointer(environment)
        graph_version = _text("Nightly graph version", pointer.get("graphVersion"))
        graph_checksum = _text(
            "Nightly graph checksum", pointer.get("graphChecksum"), limit=64
        )
        if graph_version == "NONE" or len(graph_checksum) != 64:
            raise ValueError("Nightly requires a published graph pointer")
        fence = _integer(
            "Nightly pointer fence", pointer.get("fence"), minimum=0, maximum=2**63 - 1
        )
        reconciliation_ref = validate_artifact_reference(
            value["eventReconciliationRef"]
        )
        snapshot = self._artifacts.get(reconciliation_ref)
        if (
            not isinstance(snapshot, Mapping)
            or set(snapshot) != {
                "schemaVersion",
                "artifactType",
                "runId",
                "correlationId",
                "acceptedEventIds",
                "receiptEventIds",
                "archivedEventIds",
            }
            or snapshot.get("schemaVersion") != "1.0.0"
            or snapshot.get("artifactType") != "nightly-event-reconciliation-snapshot"
            or snapshot.get("runId") != run_id
            or snapshot.get("correlationId") != context.correlation_id
        ):
            raise ValueError("Nightly event reconciliation snapshot is invalid")
        accepted = _ids("Nightly accepted event IDs", snapshot["acceptedEventIds"])
        receipts = _ids("Nightly receipt event IDs", snapshot["receiptEventIds"])
        archived = _ids("Nightly archived event IDs", snapshot["archivedEventIds"])
        missing_receipts = sorted(set(accepted) - set(receipts))
        missing_archives = sorted(set(accepted) - set(archived))
        orphan_receipts = sorted(set(receipts) - set(accepted))
        reconciliation = {
            "status": (
                "DRIFT"
                if missing_receipts or missing_archives or orphan_receipts
                else "RECONCILED"
            ),
            "acceptedCount": len(accepted),
            "receiptCount": len(receipts),
            "archiveCount": len(archived),
            "missingReceiptEventIds": missing_receipts,
            "missingArchiveEventIds": missing_archives,
            "orphanReceiptEventIds": orphan_receipts,
        }
        paths = _paths("Nightly sample paths", value["samplePaths"])
        system = _text("Nightly system", value["system"])
        scope_urns = _urns("Nightly sample scope URNs", value["sampleScopeUrns"])
        if any(
            urn.env != environment or urn.system != system
            for urn in (LineageUrn.parse(item) for item in scope_urns)
        ):
            raise ValueError("Nightly sample scope crosses its pinned environment or system")
        nightly = validate_nightly_context(
            {
                "runId": run_id,
                "sampleScopeUrns": scope_urns,
                "eventReconciliation": reconciliation,
                "staleCacheRef": validate_artifact_reference(value["staleCacheRef"]),
                "auditSampleRef": validate_artifact_reference(value["auditSampleRef"]),
                "pinnedGraphChecksum": graph_checksum,
                "cacheDrainLimit": value["cacheDrainLimit"],
            }
        )
        work_unit = {
            "schemaVersion": "1.0.0",
            "artifactType": "sca-work-unit",
            "workUnitId": run_id,
            "pack": "python-ast",
            "repository": _text("Nightly repository", value["repository"]),
            "artifactDigest": _text("Nightly artifact digest", value["artifactDigest"]),
            "environment": environment,
            "platform": _text("Nightly platform", value["platform"]),
            "system": system,
            "paths": paths,
            "repositorySource": validate_artifact_reference(value["repositorySource"]),
            "catalogSnapshotRef": validate_artifact_reference(value["catalogSnapshotRef"]),
            "catalogSnapshotId": _text("Nightly catalog snapshot", value["catalogSnapshotId"]),
            "resolverVersion": _text("Nightly resolver version", value["resolverVersion"]),
            "rulesetVersion": _text("Nightly ruleset version", value["rulesetVersion"]),
            "resolverConfig": {},
            "activeBaseVersion": graph_version,
            "activeBaseFence": fence,
            "acceptedAt": accepted_at,
            "runtimeManifestRefs": [],
            "coverage": {
                "expectedScope": paths,
                "completedScope": [],
                "reusedScope": [],
                "skippedScope": [],
                "unsupportedScope": [],
                "quarantinedScope": [],
                "failedScope": [],
            },
            "correlationId": context.correlation_id,
            "nightly": nightly,
        }
        return StageExecutionResult("sca-work-unit", "1.0.0", work_unit)

    def _verify_projection(
        self,
        input_document: Mapping[str, Any],
        common: dict[str, Any],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        assertions = _assertions(self._artifacts, common, context.correlation_id)
        edges = _clean_edges(assertions, common["system"])
        expected_rows = sorted(
            [
                {
                    "edgeId": edge["edgeKey"],
                    "source": source,
                    "target": edge["to"],
                    "type": edge["edgeType"],
                }
                for edge in edges
                for source in edge["from"]
            ],
            key=lambda row: (row["edgeId"], row["source"], row["target"], row["type"]),
        )
        scope = set(common["nightly"]["sampleScopeUrns"])
        if any(
            row["source"] not in scope and row["target"] not in scope
            for row in expected_rows
        ):
            raise ValueError("Nightly clean analysis escaped its pinned sample scope")
        actual_rows = _projection_rows(
            self._projection.namespace_checksum(common["activeBaseVersion"])
        )
        sample_rows = [
            row for row in actual_rows if row["source"] in scope or row["target"] in scope
        ]
        expected_by_row = {_canonical(row): row for row in expected_rows}
        actual_by_row = {_canonical(row): row for row in sample_rows}
        missing = [
            expected_by_row[key]
            for key in sorted(set(expected_by_row) - set(actual_by_row))
        ]
        unexpected = [
            actual_by_row[key]
            for key in sorted(set(actual_by_row) - set(expected_by_row))
        ]
        actual_checksum = _digest(actual_rows)
        pointer = self._control.active_pointer(common["environment"])
        reasons: list[str] = []
        if (
            pointer.get("graphVersion") != common["activeBaseVersion"]
            or pointer.get("fence") != common["activeBaseFence"]
        ):
            reasons.append("ENVIRONMENT_CHANGED")
        if actual_checksum != common["nightly"]["pinnedGraphChecksum"]:
            reasons.append("PROJECTION_CHECKSUM_DRIFT")
        if missing or unexpected:
            reasons.append("PUBLISHED_SAMPLE_DRIFT")
        return self._result(
            context,
            common,
            {
                "projection": {
                    "status": "DRIFT" if reasons else "VERIFIED",
                    "graphVersion": common["activeBaseVersion"],
                    "expectedChecksum": common["nightly"]["pinnedGraphChecksum"],
                    "actualChecksum": actual_checksum,
                    "missingRows": missing,
                    "unexpectedRows": unexpected,
                    "reasons": reasons,
                }
            },
        )

    def _drain_cache(
        self,
        input_document: Mapping[str, Any],
        common: dict[str, Any],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        manifest = self._artifacts.get(common["nightly"]["staleCacheRef"])
        if (
            not isinstance(manifest, Mapping)
            or set(manifest) != {"schemaVersion", "artifactType", "runId", "entries"}
            or manifest.get("schemaVersion") != "1.0.0"
            or manifest.get("artifactType") != "stale-llm-cache-manifest"
            or manifest.get("runId") != common["nightly"]["runId"]
            or not isinstance(manifest.get("entries"), list)
            or len(manifest["entries"]) > 10_000
        ):
            raise ValueError("Nightly stale cache manifest is invalid")
        entries: list[dict[str, str]] = []
        for raw in manifest["entries"]:
            if not isinstance(raw, Mapping) or set(raw) != {
                "cacheKey",
                "determinantDigest",
            }:
                raise ValueError("Nightly stale cache entry schema is invalid")
            digest = _text(
                "Nightly cache determinant", raw["determinantDigest"], limit=64
            )
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError("Nightly cache determinant digest is invalid")
            entries.append(
                {
                    "cacheKey": _text("Nightly cache key", raw["cacheKey"]),
                    "determinantDigest": digest,
                }
            )
        if entries != sorted(entries, key=lambda item: item["cacheKey"]) or len(
            {item["cacheKey"] for item in entries}
        ) != len(entries):
            raise ValueError("Nightly stale cache entries must be sorted and unique")
        limit = common["nightly"]["cacheDrainLimit"]
        results = [
            self._control.invalidate_llm_cache(
                entry["cacheKey"],
                entry["determinantDigest"],
                common["nightly"]["runId"],
            )
            for entry in entries[:limit]
        ]
        for entry, result in zip(entries[:limit], results, strict=True):
            if (
                not isinstance(result, Mapping)
                or set(result)
                != {"cacheKey", "determinantDigest", "runId", "disposition"}
                or result.get("cacheKey") != entry["cacheKey"]
                or result.get("determinantDigest") != entry["determinantDigest"]
                or result.get("runId") != common["nightly"]["runId"]
                or result.get("disposition")
                not in {"INVALIDATED", "DUPLICATE", "SUPERSEDED", "MISSING"}
            ):
                raise ValueError("Nightly cache invalidation result is invalid")
        return self._result(
            context,
            common,
            {
                "projection": input_document["projection"],
                "cacheDrain": {
                    "requested": len(entries),
                    "processed": len(results),
                    "truncated": len(entries) > limit,
                    "results": [dict(result) for result in results],
                },
            },
        )

    def _audit(
        self,
        input_document: Mapping[str, Any],
        common: dict[str, Any],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        manifest = self._artifacts.get(common["nightly"]["auditSampleRef"])
        if (
            not isinstance(manifest, Mapping)
            or set(manifest) != {"schemaVersion", "artifactType", "runId", "samples"}
            or manifest.get("schemaVersion") != "1.0.0"
            or manifest.get("artifactType") != "autopublish-audit-sample"
            or manifest.get("runId") != common["nightly"]["runId"]
            or not isinstance(manifest.get("samples"), list)
            or len(manifest["samples"]) > 1_000
        ):
            raise ValueError("Nightly auto-publish audit manifest is invalid")
        samples: list[dict[str, Any]] = []
        anomalies: list[dict[str, str]] = []
        for raw in manifest["samples"]:
            if not isinstance(raw, Mapping) or set(raw) != {
                "proposalId",
                "decision",
                "coverageState",
                "mechanisms",
                "staticExact",
            }:
                raise ValueError("Nightly auto-publish audit sample schema is invalid")
            mechanisms = _ids("Nightly audit mechanisms", raw["mechanisms"], maximum=10)
            if set(mechanisms) - {"SCA", "NATIVE", "MANIFEST", "CACHE", "LLM", "RUNTIME"}:
                raise ValueError("Nightly audit contains an unknown mechanism")
            sample = {
                "proposalId": _text("Nightly audit proposal ID", raw["proposalId"]),
                "decision": _text("Nightly audit decision", raw["decision"], limit=32),
                "coverageState": _text("Nightly audit coverage", raw["coverageState"], limit=16),
                "mechanisms": mechanisms,
                "staticExact": raw["staticExact"],
            }
            if sample["decision"] not in {"AUTO_PUBLISHED", "REVIEW_REQUIRED", "REJECTED"}:
                raise ValueError("Nightly audit decision is unsupported")
            if sample["coverageState"] not in {"COMPLETE", "INCOMPLETE"} or not isinstance(
                sample["staticExact"], bool
            ):
                raise ValueError("Nightly audit policy inputs are invalid")
            eligible = (
                sample["coverageState"] == "COMPLETE"
                and sample["staticExact"] is True
                and "SCA" in mechanisms
                and set(mechanisms) != {"LLM"}
            )
            if sample["decision"] == "AUTO_PUBLISHED" and not eligible:
                anomalies.append(
                    {"proposalId": sample["proposalId"], "reason": "UNSAFE_AUTOPUBLISH"}
                )
            samples.append(sample)
        if samples != sorted(samples, key=lambda item: item["proposalId"]) or len(
            {item["proposalId"] for item in samples}
        ) != len(samples):
            raise ValueError("Nightly audit samples must be sorted and unique")
        return self._result(
            context,
            common,
            {
                "projection": input_document["projection"],
                "cacheDrain": input_document["cacheDrain"],
                "audit": {
                    "status": "DRIFT" if anomalies else "VERIFIED",
                    "sampleCount": len(samples),
                    "anomalies": anomalies,
                    "policyVersion": "nightly-autopublish-audit-v1",
                },
            },
        )

    def _complete(
        self,
        input_document: Mapping[str, Any],
        common: dict[str, Any],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        projection = input_document.get("projection")
        cache_drain = input_document.get("cacheDrain")
        audit = input_document.get("audit")
        if not all(isinstance(item, Mapping) for item in (projection, cache_drain, audit)):
            raise ValueError("Nightly final input is incomplete")
        alerts: list[str] = []
        if common["nightly"]["eventReconciliation"]["status"] != "RECONCILED":
            alerts.append("ARCHIVE_RECONCILIATION_DRIFT")
        projection_reasons = projection.get("reasons")
        allowed_reasons = {
            "ENVIRONMENT_CHANGED",
            "PROJECTION_CHECKSUM_DRIFT",
            "PUBLISHED_SAMPLE_DRIFT",
        }
        if (
            not isinstance(projection_reasons, list)
            or len(projection_reasons) != len(set(projection_reasons))
            or set(projection_reasons) - allowed_reasons
        ):
            raise ValueError("Nightly projection reasons are invalid")
        if "PUBLISHED_SAMPLE_DRIFT" in projection_reasons:
            alerts.append("PUBLISHED_SAMPLE_DRIFT")
        if "PROJECTION_CHECKSUM_DRIFT" in projection_reasons:
            alerts.append("PROJECTION_CHECKSUM_DRIFT")
        if "ENVIRONMENT_CHANGED" in projection_reasons:
            alerts.append("ENVIRONMENT_CHANGED")
        results = cache_drain.get("results")
        if (
            cache_drain.get("truncated") is True
            or not isinstance(results, list)
            or any(
                not isinstance(result, Mapping)
                or result.get("disposition") not in {"INVALIDATED", "DUPLICATE"}
                for result in results
            )
        ):
            alerts.append("CACHE_DRAIN_INCOMPLETE")
        if audit.get("status") != "VERIFIED":
            alerts.append("AUTOPUBLISH_AUDIT_DRIFT")
        missing_rows = _projection_rows(projection.get("missingRows"))
        unexpected_rows = _projection_rows(projection.get("unexpectedRows"))
        proposal = self._proposal(common, context, missing_rows, unexpected_rows)
        terminal = "PROPOSALS_RAISED" if proposal is not None else "RECONCILED"
        report = {
            "schemaVersion": "1.0.0",
            "artifactType": "nightly-reconciliation-result",
            "workflowKind": context.workflow_kind,
            "workflowVersion": context.workflow_version,
            "stageId": context.stage_id,
            "stageName": context.stage_name,
            "commandId": context.command_id,
            "correlationId": context.correlation_id,
            "source": dict(context.input_reference),
            "context": common,
            "eventReconciliation": common["nightly"]["eventReconciliation"],
            "projection": dict(projection),
            "cacheDrain": dict(cache_drain),
            "audit": dict(audit),
            "alerts": sorted(set(alerts)),
            "proposal": proposal,
            "terminalOutcome": terminal,
        }
        if len(_canonical(report).encode()) > min(MAX_STAGE_DOCUMENT_BYTES, 300_000):
            raise ValueError("Nightly reconciliation report exceeds its size bound")
        persisted = self._control.complete_nightly(report, proposal)
        if persisted != report:
            raise ValueError("Nightly stored report differs from its deterministic result")
        return StageExecutionResult("nightly-reconciliation-result", "1.0.0", report)

    def _proposal(
        self,
        common: dict[str, Any],
        context: StageExecutionContext,
        missing_rows: list[object],
        unexpected_rows: list[object],
    ) -> dict[str, Any] | None:
        missing_ids = sorted(
            {_text("Nightly missing edge ID", row["edgeId"]) for row in missing_rows}
        )
        removed_ids = sorted(
            {_text("Nightly unexpected edge ID", row["edgeId"]) for row in unexpected_rows}
        )
        if not missing_ids and not removed_ids:
            return None
        assertions = _assertions(self._artifacts, common, context.correlation_id)
        clean_edges = _clean_edges(assertions, common["system"])
        by_id = {edge["edgeKey"]: edge for edge in clean_edges}
        if any(edge_id not in by_id for edge_id in missing_ids):
            raise ValueError("Nightly missing edge is not reproduced by clean analysis")
        selected = [by_id[edge_id] for edge_id in missing_ids]
        edge_set = {
            "schemaVersion": "1.0.0",
            "artifactType": "consolidated-edge-set",
            "commandId": context.command_id,
            "correlationId": context.correlation_id,
            "context": {
                key: value
                for key, value in common.items()
                if key not in {"coverage", "assertionRefs", "nightly"}
            },
            "edges": selected,
        }
        edge_set_ref = validate_artifact_reference(
            self._artifacts.put(
                "consolidated-edge-set",
                f"commands/{context.command_id}/edge-sets/N6-reconciliation.json",
                edge_set,
                "1.0.0",
            )
        )
        identity = {
            "proposalType": "RECONCILIATION",
            "runId": common["nightly"]["runId"],
            "system": common["system"],
            "environment": common["environment"],
            "expectedBaseVersion": common["activeBaseVersion"],
            "edgeSetRef": edge_set_ref,
            "addedEdgeIds": missing_ids,
            "removedEdgeIds": removed_ids,
            "correlationId": context.correlation_id,
        }
        return {
            "schemaVersion": "1.0.0",
            "proposalId": f"proposal-{_digest(identity)}",
            "version": 1,
            "proposalType": "RECONCILIATION",
            "system": common["system"],
            "environment": common["environment"],
            "state": "IN_REVIEW",
            "expectedBaseVersion": common["activeBaseVersion"],
            "diff": {
                "edgeSetRef": edge_set_ref,
                "addedEdgeIds": missing_ids,
                "removedEdgeIds": removed_ids,
                "bandChangedEdgeIds": [],
            },
            "correlationId": context.correlation_id,
            "createdAt": common["acceptedAt"],
            "lockVersion": 1,
        }

    @staticmethod
    def _result(
        context: StageExecutionContext,
        common: dict[str, Any],
        additions: Mapping[str, Any],
    ) -> StageExecutionResult:
        artifact_type, _ = _STAGES[context.stage_id]
        return StageExecutionResult(
            artifact_type,
            "1.0.0",
            {
                "schemaVersion": "1.0.0",
                "artifactType": artifact_type,
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "context": common,
                **dict(additions),
            },
        )


__all__ = ["NightlyStageUseCase", "validate_nightly_context"]
