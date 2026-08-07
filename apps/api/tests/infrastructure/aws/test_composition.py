from __future__ import annotations

import json
from typing import Any

import pytest

from lineage_api.infrastructure.aws.composition import (
    AwsStageExecutor,
    StepFunctionsWorkflowStarter,
    build_stage_executor,
)
from lineage_api.infrastructure.aws.config import AwsRuntimeConfig


REQUIRED_ENV = {
    "AWS_REGION": "us-east-1",
    "LINEAGE_CONTROL_TABLE": "control",
    "LINEAGE_LEDGER_TABLE": "ledger",
    "LINEAGE_PROPOSAL_TABLE": "proposal",
    "LINEAGE_POINTER_TABLE": "pointer",
    "LINEAGE_EVIDENCE_BUCKET": "evidence",
    "LINEAGE_PACKAGE_BUCKET": "packages",
    "LINEAGE_RUNTIME_STREAM": "runtime",
    "LINEAGE_NEPTUNE_ENDPOINT": "graph.cluster.us-east-1.neptune.amazonaws.com",
    "LINEAGE_ENTERPRISE_ENDPOINT": "https://catalog.example.internal",
}


class FakeClient:
    def __getattr__(self, _name: str):
        return lambda **_kwargs: {}


def test_runtime_config_fails_closed_on_missing_or_fixture_values() -> None:
    with pytest.raises(RuntimeError, match="LINEAGE_CONTROL_TABLE"):
        AwsRuntimeConfig.from_env({})
    invalid = dict(REQUIRED_ENV, LINEAGE_CONTROL_TABLE="NotConfigured")
    with pytest.raises(RuntimeError, match="placeholder"):
        AwsRuntimeConfig.from_env(invalid)


def test_composition_injects_sdk_clients_without_local_adapters() -> None:
    clients = {name: FakeClient() for name in ("dynamodb", "s3", "sqs", "kinesis", "neptunedata", "stepfunctions")}
    executor = build_stage_executor(env=REQUIRED_ENV, clients=clients)

    assert isinstance(executor, AwsStageExecutor)
    assert executor.config.control_table == "control"
    assert executor.control.client is clients["dynamodb"]
    assert executor.artifacts.client is clients["s3"]


def test_intake_composition_rejects_a_partial_workflow_alias_set() -> None:
    clients = {name: FakeClient() for name in ("dynamodb", "s3", "sqs", "kinesis", "neptunedata", "stepfunctions")}
    env = dict(REQUIRED_ENV, LINEAGE_INCREMENTAL_WORKFLOW_ALIAS_ARN="arn:incremental:live")

    with pytest.raises(RuntimeError, match="all four workflow aliases"):
        build_stage_executor(env=env, clients=clients)


def test_sca_worker_requires_a_task_token_and_returns_only_a_bounded_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    from lineage_api.infrastructure.aws import composition

    class FakeExecutor:
        def execute(self, stage: str, envelope: dict[str, Any]) -> dict[str, Any]:
            assert stage == "sca"
            assert envelope["stageId"] == "B5"
            return {
                "outcome": "SUCCEEDED",
                "output": {"bucket": "evidence", "key": "b5.json", "versionId": "v1", "sha256": "a" * 64, "sizeBytes": 10},
            }

    class StepFunctions:
        def __init__(self) -> None:
            self.success: dict[str, str] | None = None

        def send_task_success(self, **kwargs: str) -> None:
            self.success = kwargs

    stepfunctions = StepFunctions()
    monkeypatch.setattr(composition, "build_stage_executor", lambda: FakeExecutor())
    monkeypatch.setenv("LINEAGE_TASK_TOKEN", "token-1")
    monkeypatch.setenv(
        "LINEAGE_STAGE_ENVELOPE",
        json.dumps({"stageId": "B5", "schemaVersion": "1.0.0"}),
    )
    composition.run_sca_worker(stepfunctions_client=stepfunctions)

    assert stepfunctions.success is not None
    assert stepfunctions.success["taskToken"] == "token-1"
    assert len(stepfunctions.success["output"].encode()) < 8_192


def test_workflow_starter_uses_exact_alias_and_deterministic_execution_identity() -> None:
    class StepFunctions:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def start_execution(self, **kwargs: str) -> dict[str, str]:
            self.calls.append(kwargs)
            return {"executionArn": "arn:execution:1"}

    client = StepFunctions()
    starter = StepFunctionsWorkflowStarter(
        client,
        {"INCREMENTAL": "arn:stateMachine:incremental:live"},
        baseline_map_concurrency=17,
    )
    envelope = {
        "commandId": "cmd-1",
        "workflowKind": "INCREMENTAL",
        "workflowVersion": "1.0.0",
        "correlationId": "corr-1",
    }

    assert starter.start(envelope) == "arn:execution:1"
    request = client.calls[0]
    assert request["stateMachineArn"] == "arn:stateMachine:incremental:live"
    assert request["name"].startswith("cmd-1-")
    assert json.loads(request["input"])["baselineMapConcurrency"] == 17


def test_baseline_inventory_stage_materializes_an_s3_map_item_array() -> None:
    class Control:
        def claim_stage(self, *_args: Any) -> dict[str, Any]:
            return {"status": "CLAIMED", "leaseEpoch": 1}

        def record_stage(self, **_kwargs: Any) -> None:
            return None

    class Artifacts:
        def __init__(self) -> None:
            self.body: object | None = None

        def get(self, _reference: object) -> dict[str, str]:
            return {"repository": "example/repo"}

        def put(self, _kind: str, _key: str, body: object, _version: str) -> dict[str, object]:
            self.body = body
            return {
                "bucket": "evidence",
                "key": "inventory.json",
                "versionId": "v1",
                "sha256": "a" * 64,
                "sizeBytes": 10,
            }

    artifacts = Artifacts()
    executor = AwsStageExecutor(
        AwsRuntimeConfig.from_env(REQUIRED_ENV),
        Control(),
        artifacts,
        FakeClient(),
        FakeClient(),
    )
    envelope = {
        "commandId": "cmd-1",
        "correlationId": "corr-1",
        "idempotencyKey": "idem-1:B4",
        "workflowKind": "BASELINE",
        "workflowVersion": "1.0.0",
        "stageId": "B4",
        "stageName": "DISCOVER_WORK_UNITS",
        "input": {
            "bucket": "evidence",
            "key": "input.json",
            "versionId": "v1",
            "sha256": "b" * 64,
            "sizeBytes": 10,
        },
    }

    executor.execute("coverage", envelope)

    assert artifacts.body == [envelope["input"]]
