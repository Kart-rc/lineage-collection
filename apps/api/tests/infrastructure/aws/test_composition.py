from __future__ import annotations

import json
from typing import Any

import pytest

from lineage_api.application.stage_execution import StageDispatcher, StageTargetMismatchError
from lineage_api.application.stage_handlers import production_stage_use_cases
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
            self.writes: list[tuple[str, str, object, str]] = []

        def get(self, _reference: object) -> dict[str, object]:
            return {
                "schemaVersion": "1.0.0",
                "artifactType": "classification-decision",
                "context": {
                    "repository": "example/repo",
                    "artifactDigest": "sha256:source-v1",
                    "environment": "staging",
                    "system": "example",
                    "repositorySource": {
                        "bucket": "evidence",
                        "key": "source/repo.zip",
                        "versionId": "source-v1",
                        "sha256": "d" * 64,
                        "sizeBytes": 100,
                    },
                    "repositoryInventory": ["pipeline.py"],
                },
                "decision": {
                    "status": "EVALUATED",
                    "repositoryClass": "DATA_PIPELINE",
                },
            }

        def put(self, kind: str, key: str, body: object, version: str) -> dict[str, object]:
            self.writes.append((kind, key, body, version))
            return {
                "bucket": "evidence",
                "key": key,
                "versionId": f"v{len(self.writes)}",
                "sha256": f"{len(self.writes):064x}",
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
        "stageName": "BUILD_COVERAGE_PLAN",
        "input": {
            "bucket": "evidence",
            "key": "input.json",
            "versionId": "v1",
            "sha256": "b" * 64,
            "sizeBytes": 10,
        },
    }

    result = executor.execute("coverage", envelope)

    assert result["outcome"] == "SUCCEEDED"
    assert [write[0] for write in artifacts.writes] == [
        "coverage-plan",
        "sca-work-unit",
        "work-inventory",
    ]
    inventory = artifacts.writes[-1][2]
    assert isinstance(inventory, list)
    assert inventory == [
        {
            "bucket": "evidence",
            "key": "commands/cmd-1/work-units/00000.json",
            "versionId": "v2",
            "sha256": f"{2:064x}",
            "sizeBytes": 10,
        }
    ]


def test_classification_target_executes_the_domain_use_case_and_persists_its_schema() -> None:
    class Control:
        def __init__(self) -> None:
            self.recorded: dict[str, Any] | None = None

        def claim_stage(self, *_args: Any) -> dict[str, Any]:
            return {"status": "RUNNING", "leaseEpoch": 7}

        def record_stage(self, **kwargs: Any) -> None:
            self.recorded = kwargs

    class Artifacts:
        def __init__(self) -> None:
            self.put_call: tuple[str, str, object, str] | None = None

        def get(self, _reference: object) -> dict[str, object]:
            return {
                "schemaVersion": "1.0.0",
                "repo": "payments-pipeline",
                "digest": "sha256:source-v1",
                "env": "staging",
                "system": "payments",
                "evidence": [
                    {
                        "level": 1,
                        "source": "catalog",
                        "class": "DATA_PIPELINE",
                        "ref": "catalog://payments-pipeline@42",
                    }
                ],
            }

        def put(self, kind: str, key: str, body: object, version: str) -> dict[str, object]:
            self.put_call = (kind, key, body, version)
            return {
                "bucket": "evidence",
                "key": key,
                "versionId": "v2",
                "sha256": "c" * 64,
                "sizeBytes": 800,
            }

    control = Control()
    artifacts = Artifacts()
    executor = AwsStageExecutor(
        AwsRuntimeConfig.from_env(REQUIRED_ENV),
        control,
        artifacts,
        FakeClient(),
        FakeClient(),
        dispatcher=StageDispatcher(production_stage_use_cases()),
    )
    envelope = {
        "schemaVersion": "1.0.0",
        "commandId": "cmd-1",
        "correlationId": "corr-1",
        "causationId": "cause-1",
        "idempotencyKey": "idem-1:B3",
        "workflowKind": "BASELINE",
        "workflowVersion": "1.0.0",
        "stageId": "B3",
        "stageName": "CLASSIFY_REPOSITORY_AND_PATHS",
        "input": {
            "bucket": "evidence",
            "key": "input.json",
            "versionId": "v1",
            "sha256": "b" * 64,
            "sizeBytes": 10,
        },
    }

    result = executor.execute("classification", envelope)

    assert result["outcome"] == "SUCCEEDED"
    assert artifacts.put_call is not None
    kind, key, body, version = artifacts.put_call
    assert kind == "classification-decision"
    assert key == "commands/cmd-1/stages/B3/idem-1:B3.json"
    assert version == "1.0.0"
    assert body["artifactType"] == "classification-decision"
    assert body["decision"]["repositoryClass"] == "DATA_PIPELINE"
    assert "target" not in body
    assert "inputDocument" not in body
    assert control.recorded is not None
    assert control.recorded["lease_epoch"] == 7


def test_direct_executor_target_mismatch_fails_before_claim_or_artifact_io() -> None:
    class NoIo:
        def __getattr__(self, name: str):
            raise AssertionError(f"unexpected I/O through {name}")

    executor = AwsStageExecutor(
        AwsRuntimeConfig.from_env(REQUIRED_ENV),
        NoIo(),
        NoIo(),
        NoIo(),
        NoIo(),
        dispatcher=StageDispatcher(production_stage_use_cases()),
    )
    envelope = {
        "commandId": "cmd-1",
        "correlationId": "corr-1",
        "idempotencyKey": "idem-1:B3",
        "workflowKind": "BASELINE",
        "workflowVersion": "1.0.0",
        "stageId": "B3",
        "stageName": "CLASSIFY_REPOSITORY_AND_PATHS",
        "input": {
            "bucket": "evidence",
            "key": "input.json",
            "versionId": "v1",
            "sha256": "b" * 64,
            "sizeBytes": 10,
        },
    }

    with pytest.raises(StageTargetMismatchError):
        executor.execute("control-stage", envelope)
