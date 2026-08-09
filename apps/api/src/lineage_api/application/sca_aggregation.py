from __future__ import annotations

import json
from collections import Counter
from pathlib import PurePosixPath
from typing import Any, Mapping

from lineage_api.application.ports import ArtifactStorePort, MapResultReaderPort
from lineage_api.application.stage_execution import (
    StageExecutionResult,
    validate_artifact_reference,
)


_REFERENCE_KEYS = ("bucket", "key", "versionId", "sha256", "sizeBytes")
_COVERAGE_FIELDS = (
    "completedScope",
    "reusedScope",
    "skippedScope",
    "unsupportedScope",
    "quarantinedScope",
    "failedScope",
)
_COMMON_TEXT = (
    "repository",
    "artifactDigest",
    "environment",
    "platform",
    "system",
    "catalogSnapshotId",
    "resolverVersion",
    "rulesetVersion",
    "activeBaseVersion",
    "acceptedAt",
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _text(name: str, value: object, *, limit: int = 2_048) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > limit:
        raise ValueError(f"invalid {name}")
    return value


def _path(value: object) -> str:
    text = _text("aggregation path", value, limit=1_024)
    parsed = PurePosixPath(text)
    if text.startswith("/") or "\\" in text or ".." in parsed.parts or text == ".":
        raise ValueError("aggregation paths must be normalized and relative")
    return parsed.as_posix()


def _paths(name: str, value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 10_000:
        raise ValueError(f"{name} must be a bounded array")
    paths = [_path(item) for item in value]
    if paths != sorted(set(paths)):
        raise ValueError(f"{name} must be sorted and unique")
    return paths


def _references(name: str, value: object, *, limit: int = 10_000) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{name} must be a bounded reference array")
    references = [validate_artifact_reference(item) for item in value]
    identities = [tuple(item[key] for key in _REFERENCE_KEYS) for item in references]
    if len(identities) != len(set(identities)):
        raise ValueError(f"{name} contains duplicate references")
    return references


def _require_bucket(
    name: str, references: list[dict[str, Any]], bucket: str
) -> None:
    if any(reference["bucket"] != bucket for reference in references):
        raise ValueError(f"{name} must remain in the command evidence bucket")


def _common(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("SCA result context is invalid")
    common = {name: _text(name, value.get(name)) for name in _COMMON_TEXT}
    common["repositorySource"] = validate_artifact_reference(
        value.get("repositorySource")
    )
    common["catalogSnapshotRef"] = validate_artifact_reference(
        value.get("catalogSnapshotRef")
    )
    fence = value.get("activeBaseFence")
    if not isinstance(fence, int) or isinstance(fence, bool) or fence < 0:
        raise ValueError("SCA result active base fence is invalid")
    common["activeBaseFence"] = fence
    common["runtimeManifestRefs"] = _references(
        "runtime manifest references", value.get("runtimeManifestRefs", []), limit=100
    )
    return common


def _fragment(value: object) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        raise ValueError("SCA result coverage is invalid")
    required = {"expectedScope", *_COVERAGE_FIELDS}
    if set(value) != required:
        raise ValueError("SCA result coverage schema is invalid")
    return {name: _paths(f"coverage {name}", value[name]) for name in required}


def _plan(value: object, command_id: str, correlation_id: str) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or value.get("schemaVersion") != "1.0.0"
        or value.get("artifactType") != "coverage-plan"
        or value.get("commandId") != command_id
        or value.get("correlationId") != correlation_id
        or not isinstance(value.get("coverage"), Mapping)
    ):
        raise ValueError("Baseline coverage plan is invalid")
    coverage = value["coverage"]
    required = {
        "expectedScope",
        "recomputedScope",
        "skippedScope",
        "unsupportedScope",
    }
    if set(coverage) != required:
        raise ValueError("Baseline coverage plan schema is invalid")
    normalized = {name: _paths(f"plan {name}", coverage[name]) for name in required}
    if normalized["expectedScope"] != sorted(
        set(
            normalized["recomputedScope"]
            + normalized["skippedScope"]
            + normalized["unsupportedScope"]
        )
    ):
        raise ValueError("Baseline coverage plan does not account for its scope")
    return normalized


class BaselineScaAggregationUseCase:
    def __init__(
        self,
        artifacts: ArtifactStorePort,
        map_results: MapResultReaderPort,
        *,
        max_work_units: int = 10_000,
    ) -> None:
        if not 1 <= max_work_units <= 10_000:
            raise ValueError("aggregation work-unit limit is invalid")
        self._artifacts = artifacts
        self._map_results = map_results
        self._max_work_units = max_work_units

    def execute(self, value: object) -> StageExecutionResult:
        required = {
            "schemaVersion",
            "operation",
            "workflowKind",
            "workflowVersion",
            "commandId",
            "correlationId",
            "causationId",
            "idempotencyKey",
            "determinantDigest",
            "workInventory",
            "mapResult",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("Baseline SCA aggregation command schema is invalid")
        if (
            value.get("schemaVersion") != "1.0.0"
            or value.get("operation") != "BASELINE_SCA_AGGREGATE"
            or value.get("workflowKind") != "BASELINE"
            or value.get("workflowVersion") != "1.0.0"
        ):
            raise ValueError("Baseline SCA aggregation command is unsupported")
        command_id = _text("aggregation command ID", value["commandId"], limit=128)
        correlation_id = _text(
            "aggregation correlation ID", value["correlationId"], limit=128
        )
        for field in ("causationId", "idempotencyKey", "determinantDigest"):
            _text(f"aggregation {field}", value[field])
        inventory_ref = validate_artifact_reference(value["workInventory"])
        evidence_bucket = inventory_ref["bucket"]
        inventory = self._artifacts.get(inventory_ref)
        if (
            not isinstance(inventory, Mapping)
            or inventory.get("schemaVersion") != "1.0.0"
            or inventory.get("artifactType") != "work-inventory"
            or inventory.get("workflowKind") != "BASELINE"
            or inventory.get("workflowVersion") != "1.0.0"
            or inventory.get("stageId") != "B4"
            or inventory.get("commandId") != command_id
            or inventory.get("correlationId") != correlation_id
        ):
            raise ValueError("Baseline work inventory is invalid")
        work_refs = _references(
            "Baseline work units",
            inventory.get("workUnitRefs"),
            limit=self._max_work_units,
        )
        _require_bucket("Baseline work units", work_refs, evidence_bucket)
        plan_ref = validate_artifact_reference(inventory.get("coveragePlanRef"))
        _require_bucket("Baseline coverage plan", [plan_ref], evidence_bucket)
        plan = _plan(self._artifacts.get(plan_ref), command_id, correlation_id)
        map_result = value["mapResult"]
        if not isinstance(map_result, Mapping) or set(map_result) != {
            "MapRunArn",
            "ResultWriterDetails",
        }:
            raise ValueError("Baseline map result schema is invalid")
        details = map_result["ResultWriterDetails"]
        if not isinstance(details, Mapping) or set(details) != {"Bucket", "Key"}:
            raise ValueError("Baseline result-writer details are invalid")
        batch = self._map_results.read_succeeded(
            _text("result bucket", details["Bucket"]),
            _text("result manifest key", details["Key"]),
            _text("map run ARN", map_result["MapRunArn"]),
            command_id,
        )
        if not isinstance(batch, Mapping) or set(batch) != {
            "manifestRef",
            "shardRefs",
            "childResults",
        }:
            raise ValueError("Baseline map result reader returned an invalid batch")
        manifest_ref = validate_artifact_reference(batch["manifestRef"])
        shard_refs = _references("Baseline result shards", batch["shardRefs"], limit=100)
        _require_bucket("Baseline map export", [manifest_ref, *shard_refs], evidence_bucket)
        children = batch["childResults"]
        if not isinstance(children, list) or len(children) != len(work_refs):
            raise ValueError("Baseline requires exactly one successful result per work unit")
        result_refs: list[dict[str, Any]] = []
        for child in children:
            if (
                not isinstance(child, Mapping)
                or set(child) != {"outcome", "output"}
                or child.get("outcome") not in {"SUCCEEDED", "SKIPPED"}
            ):
                raise ValueError("Baseline child SCA result is invalid")
            result_refs.append(validate_artifact_reference(child["output"]))
        result_identities = {
            tuple(reference[key] for key in _REFERENCE_KEYS) for reference in result_refs
        }
        if len(result_identities) != len(result_refs):
            raise ValueError("Baseline child SCA results contain duplicate references")
        _require_bucket("Baseline child SCA results", result_refs, evidence_bucket)

        work_by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        work_scope: list[str] = []
        for work_ref in work_refs:
            work = self._artifacts.get(work_ref)
            if (
                not isinstance(work, Mapping)
                or work.get("schemaVersion") != "1.0.0"
                or work.get("artifactType") != "sca-work-unit"
                or work.get("correlationId") != correlation_id
                or work.get("coveragePlan") != plan_ref
            ):
                raise ValueError("Baseline SCA work unit is invalid")
            work_id = _text("work-unit ID", work.get("workUnitId"))
            if work_id in work_by_id:
                raise ValueError("Baseline work-unit identity is duplicated")
            work_by_id[work_id] = (work_ref, dict(work))
            work_scope.extend(_paths("work paths", work.get("paths")))
        if work_scope != sorted(set(work_scope)) or work_scope != plan["recomputedScope"]:
            raise ValueError("Baseline work inventory does not match recomputed scope")

        common: dict[str, Any] | None = None
        fragments: list[dict[str, list[str]]] = []
        assertion_refs: list[dict[str, Any]] = []
        residue_refs: list[dict[str, Any]] = []
        seen_work_ids: set[str] = set()
        for result_ref in result_refs:
            result = self._artifacts.get(result_ref)
            if (
                not isinstance(result, Mapping)
                or result.get("schemaVersion") != "1.0.0"
                or result.get("artifactType") != "sca-stage-result"
                or result.get("workflowKind") != "BASELINE"
                or result.get("workflowVersion") != "1.0.0"
                or result.get("stageId") != "B5"
                or result.get("commandId") != command_id
                or result.get("correlationId") != correlation_id
            ):
                raise ValueError("Baseline SCA stage result is invalid")
            work_id = _text("SCA result work-unit ID", result.get("workUnitId"))
            if work_id not in work_by_id or work_id in seen_work_ids:
                raise ValueError("Baseline SCA result work-unit identity is invalid")
            work_ref, work = work_by_id[work_id]
            if result.get("source") != work_ref:
                raise ValueError("Baseline SCA result is not bound to its work unit")
            result_context = result.get("context")
            normalized_common = _common(result_context)
            if common is None:
                common = normalized_common
            elif _canonical(common) != _canonical(normalized_common):
                raise ValueError("Baseline SCA result determinants conflict")
            if not isinstance(result_context, Mapping):
                raise ValueError("Baseline SCA result context is invalid")
            fragment = _fragment(result_context.get("coverage"))
            if fragment["expectedScope"] != _paths(
                "work paths", work.get("paths")
            ):
                raise ValueError("Baseline SCA result coverage differs from its work unit")
            fragments.append(fragment)
            assertion_refs.extend(
                _references("assertion references", result_context.get("assertionRefs"))
            )
            residue_refs.extend(
                _references("residue references", result_context.get("residueRefs"))
            )
            seen_work_ids.add(work_id)
        if seen_work_ids != set(work_by_id):
            raise ValueError("Baseline requires exactly one successful result per work unit")
        if common is None:
            common = _common(inventory.get("context"))
        elif _canonical(common) != _canonical(_common(inventory.get("context"))):
            raise ValueError("Baseline SCA result determinants differ from work inventory")

        aggregate_coverage: dict[str, Any] = {"expectedScope": plan["expectedScope"]}
        for field in _COVERAGE_FIELDS:
            if field == "skippedScope":
                values = plan["skippedScope"]
            elif field == "unsupportedScope":
                values = plan["unsupportedScope"]
            else:
                values = sorted(
                    item for fragment in fragments for item in fragment[field]
                )
            if values != sorted(set(values)):
                raise ValueError("Baseline aggregate coverage contains duplicates")
            aggregate_coverage[field] = values
        accounted = [
            item
            for field in _COVERAGE_FIELDS
            for item in aggregate_coverage[field]
        ]
        exact = Counter(aggregate_coverage["expectedScope"]) == Counter(accounted)
        aggregate_coverage["state"] = (
            "COMPLETE"
            if exact
            and not any(
                aggregate_coverage[field]
                for field in ("unsupportedScope", "quarantinedScope", "failedScope")
            )
            else "INCOMPLETE"
        )
        if not exact:
            aggregate_coverage["reason"] = "SCOPE_ACCOUNTING_MISMATCH"
        common.update(
            assertionRefs=_references("aggregate assertions", assertion_refs),
            residueRefs=_references("aggregate residue", residue_refs),
            coverage=aggregate_coverage,
        )
        return StageExecutionResult(
            "sca-batch-result",
            "1.0.0",
            {
                "schemaVersion": "1.0.0",
                "artifactType": "sca-batch-result",
                "workflowKind": "BASELINE",
                "workflowVersion": "1.0.0",
                "stageId": "B5A",
                "stageName": "AGGREGATE_DISTRIBUTED_SCA_RESULTS",
                "commandId": command_id,
                "correlationId": correlation_id,
                "source": inventory_ref,
                "context": common,
                "workUnitIds": sorted(seen_work_ids),
                "resultRefs": sorted(
                    result_refs,
                    key=lambda item: (item["bucket"], item["key"], item["versionId"]),
                ),
                "mapExport": {
                    "manifestRef": manifest_ref,
                    "shardRefs": shard_refs,
                },
            },
        )


__all__ = ["BaselineScaAggregationUseCase"]
