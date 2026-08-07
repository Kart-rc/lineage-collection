from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from typing import Any, Protocol


SCHEMA_VERSION = "1.0.0"
MAX_RESULT_BYTES = 8_192
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REFERENCE_KEYS = frozenset({"bucket", "key", "versionId", "sha256", "sizeBytes"})
_LOG = logging.getLogger("lineage.aws.stage")


class StageExecutor(Protocol):
    """Application port implemented by the production AWS composition root."""

    def execute(self, stage: str, envelope: dict[str, Any]) -> dict[str, Any]: ...


def _production_executor() -> StageExecutor:
    try:
        from lineage_api.infrastructure.aws.composition import build_stage_executor
    except ModuleNotFoundError as error:
        raise RuntimeError("AWS stage composition is not installed") from error
    return build_stage_executor()


executor_factory: Callable[[], StageExecutor] = _production_executor


def _required_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_reference(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _REFERENCE_KEYS:
        raise ValueError("invalid stage envelope reference")
    if not all(_required_string(value[key]) for key in ("bucket", "key", "versionId")):
        raise ValueError("invalid stage envelope reference")
    digest = value["sha256"]
    size = value["sizeBytes"]
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise ValueError("invalid stage envelope reference")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= 5_000_000_000_000:
        raise ValueError("invalid stage envelope reference")
    return dict(value)


def validate_envelope(event: object) -> dict[str, Any]:
    if not isinstance(event, Mapping) or event.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("invalid stage envelope")
    for field in ("commandId", "correlationId", "causationId", "idempotencyKey"):
        if not _required_string(event.get(field)):
            raise ValueError("invalid stage envelope")
    validated = dict(event)
    validated["input"] = validate_reference(event.get("input"))
    return validated


def create_handler(stage: str):
    def handler(event: object, _context: object) -> dict[str, Any]:
        envelope = validate_envelope(event)
        executed = executor_factory().execute(stage, envelope)
        outcome = executed.get("outcome")
        if outcome not in {"SUCCEEDED", "SKIPPED", "REDRIVE_REQUIRED"}:
            raise ValueError("invalid stage outcome")
        result = {
            "schemaVersion": SCHEMA_VERSION,
            "commandId": envelope["commandId"],
            "correlationId": envelope["correlationId"],
            "outcome": outcome,
            "output": validate_reference(executed.get("output")),
        }
        _LOG.info(
            json.dumps(
                {
                    "commandId": envelope["commandId"],
                    "correlationId": envelope["correlationId"],
                    "event": "lineage.stage.completed",
                    "outcome": outcome,
                    "stage": stage,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        if len(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()) >= MAX_RESULT_BYTES:
            raise ValueError("stage result exceeds bounded reference contract")
        return result

    return handler
