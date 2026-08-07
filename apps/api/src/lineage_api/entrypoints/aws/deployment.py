from __future__ import annotations

import json
from typing import Any

from lineage_api.application.workflows.definitions import DEPLOYMENT
from lineage_api.entrypoints.aws import common


STAGE = "deployment"


def handler(event: object, _context: object) -> dict[str, Any]:
    envelope = common.validate_envelope(event)
    executor = common.executor_factory()
    current_input = envelope["input"]
    completed: list[str] = []
    outcome = "SUCCEEDED"
    for stage in DEPLOYMENT.stages:
        stage_envelope = {
            **envelope,
            "workflowKind": DEPLOYMENT.kind,
            "workflowVersion": DEPLOYMENT.version,
            "stageId": stage.stage_id,
            "stageName": stage.name,
            "idempotencyKey": f"{envelope['idempotencyKey']}:{stage.stage_id}",
            "input": current_input,
        }
        executed = executor.execute(STAGE, stage_envelope)
        outcome = executed.get("outcome")
        if outcome not in {"SUCCEEDED", "SKIPPED", "REDRIVE_REQUIRED"}:
            raise ValueError("invalid stage outcome")
        current_input = common.validate_reference(executed.get("output"))
        if outcome == "REDRIVE_REQUIRED":
            break
        completed.append(stage.stage_id)
    result = {
        "schemaVersion": common.SCHEMA_VERSION,
        "commandId": envelope["commandId"],
        "correlationId": envelope["correlationId"],
        "outcome": outcome,
        "completedStages": completed,
        "output": current_input,
    }
    if len(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()) >= common.MAX_RESULT_BYTES:
        raise ValueError("stage result exceeds bounded reference contract")
    return result
