from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from lineage_api.entrypoints.aws.common import (
    MAX_RESULT_BYTES,
    SCHEMA_VERSION,
    validate_reference,
)


_COMMAND_KEYS = frozenset(
    {
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
)


class ScaAggregationExecutor(Protocol):
    def execute(self, event: dict[str, Any]) -> dict[str, Any]: ...


def _production_executor() -> ScaAggregationExecutor:
    try:
        from lineage_api.infrastructure.aws.composition import (
            build_sca_aggregation_executor,
        )
    except ModuleNotFoundError as error:
        raise RuntimeError("AWS SCA aggregation composition is not installed") from error
    return build_sca_aggregation_executor()


executor_factory: Callable[[], ScaAggregationExecutor] = _production_executor


def _command(event: object) -> dict[str, Any]:
    if not isinstance(event, Mapping) or set(event) != _COMMAND_KEYS:
        raise ValueError("invalid Baseline SCA aggregation command")
    if (
        event.get("schemaVersion") != SCHEMA_VERSION
        or event.get("operation") != "BASELINE_SCA_AGGREGATE"
        or event.get("workflowKind") != "BASELINE"
        or event.get("workflowVersion") != "1.0.0"
    ):
        raise ValueError("invalid Baseline SCA aggregation command")
    for name in (
        "commandId",
        "correlationId",
        "causationId",
        "idempotencyKey",
        "determinantDigest",
    ):
        value = event.get(name)
        if not isinstance(value, str) or not value.strip() or len(value.encode()) > 2_048:
            raise ValueError("invalid Baseline SCA aggregation command")
    validate_reference(event.get("workInventory"))
    map_result = event.get("mapResult")
    if not isinstance(map_result, Mapping) or set(map_result) != {
        "MapRunArn",
        "ResultWriterDetails",
    }:
        raise ValueError("invalid Baseline SCA aggregation command")
    details = map_result.get("ResultWriterDetails")
    if not isinstance(details, Mapping) or set(details) != {"Bucket", "Key"}:
        raise ValueError("invalid Baseline SCA aggregation command")
    for value in (map_result.get("MapRunArn"), details.get("Bucket"), details.get("Key")):
        if not isinstance(value, str) or not value.strip() or len(value.encode()) > 2_048:
            raise ValueError("invalid Baseline SCA aggregation command")
    return dict(event)


def handler(event: object, _context: object) -> dict[str, Any]:
    command = _command(event)
    executed = executor_factory().execute(command)
    if (
        not isinstance(executed, Mapping)
        or set(executed) != {"outcome", "output"}
        or executed.get("outcome")
        not in {"SUCCEEDED", "SKIPPED", "REDRIVE_REQUIRED"}
    ):
        raise ValueError("invalid SCA aggregation outcome")
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "commandId": command["commandId"],
        "correlationId": command["correlationId"],
        "outcome": executed["outcome"],
        "output": validate_reference(executed.get("output")),
    }
    if len(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()) >= MAX_RESULT_BYTES:
        raise ValueError("SCA aggregation result exceeds bounded reference contract")
    return result


__all__ = ["executor_factory", "handler"]
