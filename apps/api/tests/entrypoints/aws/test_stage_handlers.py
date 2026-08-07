from __future__ import annotations

import importlib
import json
import logging
from typing import Any

import pytest


def _event() -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "commandId": "cmd-1",
        "correlationId": "corr-1",
        "causationId": "cause-1",
        "idempotencyKey": "idem-1",
        "workflowKind": "INCREMENTAL",
        "workflowVersion": "1.0.0",
        "stageId": "I1",
        "stageName": "DEDUPLICATE_AND_PIN_ACTIVE_BASE",
        "determinantDigest": "sha256:determinants",
        "input": {"bucket": "evidence", "key": "input.json", "versionId": "v1", "sha256": "a" * 64, "sizeBytes": 10},
    }


@pytest.mark.parametrize(
    "module_name",
    ("intake", "control_stage", "runtime_validation", "consolidation", "coverage", "proposal", "publication"),
)
def test_stage_handlers_pass_workflow_identity_to_aws_composition(module_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    common = importlib.import_module("lineage_api.entrypoints.aws.common")
    module = importlib.import_module(f"lineage_api.entrypoints.aws.{module_name}")
    calls: list[tuple[str, dict[str, Any]]] = []

    class Executor:
        def execute(self, stage: str, envelope: dict[str, Any]) -> dict[str, Any]:
            calls.append((stage, envelope))
            return {"outcome": "REDRIVE_REQUIRED", "output": envelope["input"]}

    monkeypatch.setattr(common, "executor_factory", lambda: Executor())
    result = module.handler(_event(), object())

    assert calls == [(module.STAGE, _event())]
    assert result["outcome"] == "REDRIVE_REQUIRED"
    assert result["output"]["versionId"] == "v1"


def test_deployment_handler_checkpoints_d1_through_d6_without_a_fifth_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    common = importlib.import_module("lineage_api.entrypoints.aws.common")
    module = importlib.import_module("lineage_api.entrypoints.aws.deployment")
    calls: list[dict[str, Any]] = []

    class Executor:
        def execute(self, stage: str, envelope: dict[str, Any]) -> dict[str, Any]:
            assert stage == "deployment"
            calls.append(envelope)
            return {"outcome": "SUCCEEDED", "output": {**envelope["input"], "key": f"{envelope['stageId']}.json"}}

    monkeypatch.setattr(common, "executor_factory", lambda: Executor())
    event = _event()
    event.pop("stageId")
    event.pop("stageName")
    result = module.handler(event, object())

    assert [call["stageId"] for call in calls] == ["D1", "D2", "D3", "D4", "D5", "D6"]
    assert calls[0]["workflowKind"] == "DEPLOYMENT"
    assert calls[-1]["input"]["key"] == "D5.json"
    assert result["completedStages"] == ["D1", "D2", "D3", "D4", "D5", "D6"]
    assert result["output"]["key"] == "D6.json"


def test_default_handler_composition_fails_closed_without_aws_config(monkeypatch: pytest.MonkeyPatch) -> None:
    common = importlib.import_module("lineage_api.entrypoints.aws.common")
    module = importlib.import_module("lineage_api.entrypoints.aws.intake")
    monkeypatch.setattr(common, "executor_factory", common._production_executor)
    for key in list(importlib.import_module("os").environ):
        if key.startswith("LINEAGE_"):
            monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="LINEAGE_CONTROL_TABLE"):
        module.handler(_event(), object())


def test_intake_sqs_batch_reports_only_failed_message_identifiers(monkeypatch: pytest.MonkeyPatch) -> None:
    common = importlib.import_module("lineage_api.entrypoints.aws.common")
    module = importlib.import_module("lineage_api.entrypoints.aws.intake")
    calls: list[dict[str, Any]] = []

    class Executor:
        def execute(self, stage: str, envelope: dict[str, Any]) -> dict[str, Any]:
            assert stage == "intake"
            calls.append(envelope)
            return {"outcome": "SUCCEEDED", "output": envelope["input"]}

    monkeypatch.setattr(common, "executor_factory", lambda: Executor())
    result = module.handler(
        {
            "Records": [
                {"messageId": "ok-1", "body": json.dumps(_event())},
                {"messageId": "bad-1", "body": "not-json"},
            ]
        },
        object(),
    )

    assert calls == [_event()]
    assert result == {"batchItemFailures": [{"itemIdentifier": "bad-1"}]}


def test_intake_sqs_batch_retains_explicit_redrive_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    common = importlib.import_module("lineage_api.entrypoints.aws.common")
    module = importlib.import_module("lineage_api.entrypoints.aws.intake")

    class Executor:
        def execute(self, _stage: str, envelope: dict[str, Any]) -> dict[str, Any]:
            return {"outcome": "REDRIVE_REQUIRED", "output": envelope["input"]}

    monkeypatch.setattr(common, "executor_factory", lambda: Executor())

    result = module.handler(
        {"Records": [{"messageId": "retry-1", "body": json.dumps(_event())}]},
        object(),
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "retry-1"}]}


def test_stage_handler_emits_bounded_structured_correlation_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    common = importlib.import_module("lineage_api.entrypoints.aws.common")
    module = importlib.import_module("lineage_api.entrypoints.aws.control_stage")

    class Executor:
        def execute(self, _stage: str, envelope: dict[str, Any]) -> dict[str, Any]:
            return {"outcome": "SUCCEEDED", "output": envelope["input"]}

    monkeypatch.setattr(common, "executor_factory", lambda: Executor())
    with caplog.at_level(logging.INFO, logger="lineage.aws.stage"):
        module.handler(_event(), object())

    records = [json.loads(record.message) for record in caplog.records]
    assert records[-1] == {
        "commandId": "cmd-1",
        "correlationId": "corr-1",
        "event": "lineage.stage.completed",
        "outcome": "SUCCEEDED",
        "stage": "control-stage",
    }
