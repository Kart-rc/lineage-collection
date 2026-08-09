from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from lineage_api.application.stage_execution import (
    StageDispatcher,
    StageExecutionResult,
    StageTargetMismatchError,
)
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
    assert executor.packages.default_bucket == "packages"
    assert executor.sources.client is clients["s3"]
    assert executor.dispatcher.has_use_case("BASELINE", "B5")


def test_intake_composition_rejects_a_partial_workflow_alias_set() -> None:
    clients = {name: FakeClient() for name in ("dynamodb", "s3", "sqs", "kinesis", "neptunedata", "stepfunctions")}
    env = dict(REQUIRED_ENV, LINEAGE_INCREMENTAL_WORKFLOW_ALIAS_ARN="arn:incremental:live")

    with pytest.raises(RuntimeError, match="all four workflow aliases"):
        build_stage_executor(env=env, clients=clients)


def test_sca_worker_requires_a_task_token_and_returns_only_a_bounded_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    from lineage_api.infrastructure.aws import composition

    stage_input = {
        "bucket": "evidence",
        "key": "work-units/b5.json",
        "versionId": "work-v1",
        "sha256": "b" * 64,
        "sizeBytes": 100,
    }

    class FakeExecutor:
        def execute(self, stage: str, envelope: dict[str, Any]) -> dict[str, Any]:
            assert stage == "sca"
            assert envelope["stageId"] == "B5"
            assert envelope["stageName"] == "RUN_BOUNDED_STATIC_ANALYSIS"
            assert envelope["workflowKind"] == "BASELINE"
            assert envelope["workflowVersion"] == "1.0.0"
            assert envelope["idempotencyKey"] == "idem-root:B5:7"
            assert envelope["input"] == stage_input
            return {
                "outcome": "SUCCEEDED",
                "output": {"bucket": "evidence", "key": "b5.json", "versionId": "v1", "sha256": "a" * 64, "sizeBytes": 10},
            }

    class StepFunctions:
        def __init__(self) -> None:
            self.success: dict[str, str] | None = None
            self.heartbeats: list[dict[str, str]] = []

        def send_task_heartbeat(self, **kwargs: str) -> None:
            self.heartbeats.append(kwargs)

        def send_task_success(self, **kwargs: str) -> None:
            self.success = kwargs

    stepfunctions = StepFunctions()
    monkeypatch.setattr(composition, "build_stage_executor", lambda: FakeExecutor())
    monkeypatch.setenv("LINEAGE_TASK_TOKEN", "token-1")
    monkeypatch.setenv("LINEAGE_STAGE_INPUT", json.dumps(stage_input))
    monkeypatch.setenv("LINEAGE_STAGE_IDEMPOTENCY_KEY", "idem-root:B5:7")
    monkeypatch.setenv("LINEAGE_STAGE_SCHEMA_VERSION", "1.0.0")
    monkeypatch.setenv("LINEAGE_STAGE_COMMAND_ID", "cmd-root")
    monkeypatch.setenv("LINEAGE_STAGE_CORRELATION_ID", "corr-root")
    monkeypatch.setenv("LINEAGE_STAGE_CAUSATION_ID", "cause-root")
    monkeypatch.setenv("LINEAGE_STAGE_DETERMINANT_DIGEST", "d" * 64)
    monkeypatch.setenv("LINEAGE_STAGE_ID", "B5")
    monkeypatch.setenv("LINEAGE_STAGE_NAME", "RUN_BOUNDED_STATIC_ANALYSIS")
    monkeypatch.setenv("LINEAGE_WORKFLOW_KIND", "BASELINE")
    monkeypatch.setenv("LINEAGE_WORKFLOW_VERSION", "1.0.0")
    composition.run_sca_worker(stepfunctions_client=stepfunctions)

    assert stepfunctions.success is not None
    assert stepfunctions.success["taskToken"] == "token-1"
    assert len(stepfunctions.success["output"].encode()) < 8_192
    assert set(json.loads(stepfunctions.success["output"])) == {"outcome", "output"}
    assert stepfunctions.heartbeats == [{"taskToken": "token-1"}]


def test_sca_worker_heartbeats_periodically_during_bounded_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lineage_api.infrastructure.aws import composition

    second_heartbeat = threading.Event()

    class FakeExecutor:
        def execute(self, _stage: str, _envelope: dict[str, Any]) -> dict[str, Any]:
            if not second_heartbeat.wait(1):
                raise TimeoutError("periodic heartbeat was not sent")
            return {
                "outcome": "SUCCEEDED",
                "output": {
                    "bucket": "evidence",
                    "key": "b5.json",
                    "versionId": "v1",
                    "sha256": "a" * 64,
                    "sizeBytes": 10,
                },
            }

    class StepFunctions:
        def __init__(self) -> None:
            self.heartbeats = 0

        def send_task_heartbeat(self, **_kwargs: str) -> None:
            self.heartbeats += 1
            if self.heartbeats >= 2:
                second_heartbeat.set()

        def send_task_success(self, **_kwargs: str) -> None:
            return None

        def send_task_failure(self, **_kwargs: str) -> None:
            return None

    client = StepFunctions()
    monkeypatch.setattr(composition, "build_stage_executor", lambda: FakeExecutor())
    monkeypatch.setenv("LINEAGE_TASK_TOKEN", "token-periodic")
    monkeypatch.setenv("LINEAGE_STAGE_ENVELOPE", json.dumps({"stageId": "B5"}))

    composition.run_sca_worker(
        stepfunctions_client=client, heartbeat_interval_seconds=0.01
    )

    assert client.heartbeats >= 2


def test_sca_worker_redacts_failure_cause_and_raises_a_sanitized_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lineage_api.infrastructure.aws import composition

    class FakeExecutor:
        def execute(self, _stage: str, _envelope: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("password=do-not-expose")

    class StepFunctions:
        def __init__(self) -> None:
            self.failure: dict[str, str] | None = None

        def send_task_heartbeat(self, **_kwargs: str) -> None:
            return None

        def send_task_failure(self, **kwargs: str) -> None:
            self.failure = kwargs

    client = StepFunctions()
    monkeypatch.setattr(composition, "build_stage_executor", lambda: FakeExecutor())
    monkeypatch.setenv("LINEAGE_TASK_TOKEN", "token-secret")
    monkeypatch.setenv(
        "LINEAGE_STAGE_ENVELOPE",
        json.dumps({"stageId": "B5", "correlationId": "corr-safe"}),
    )

    with pytest.raises(RuntimeError, match="SCA worker failed") as failure:
        composition.run_sca_worker(
            stepfunctions_client=client, heartbeat_interval_seconds=0.01
        )

    assert "do-not-expose" not in str(failure.value)
    assert client.failure is not None
    assert "do-not-expose" not in json.dumps(client.failure)
    assert client.failure["cause"] == "SCA stage failed; correlationId=corr-safe"


def test_sca_worker_rejects_callback_fields_outside_the_reference_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lineage_api.infrastructure.aws import composition

    class FakeExecutor:
        def execute(self, _stage: str, _envelope: dict[str, Any]) -> dict[str, Any]:
            return {
                "outcome": "SUCCEEDED",
                "output": {
                    "bucket": "evidence",
                    "key": "b5.json",
                    "versionId": "v1",
                    "sha256": "a" * 64,
                    "sizeBytes": 10,
                },
                "secret": "do-not-callback",
            }

    class StepFunctions:
        def __init__(self) -> None:
            self.success = False
            self.failure: dict[str, str] | None = None

        def send_task_heartbeat(self, **_kwargs: str) -> None:
            return None

        def send_task_success(self, **_kwargs: str) -> None:
            self.success = True

        def send_task_failure(self, **kwargs: str) -> None:
            self.failure = kwargs

    client = StepFunctions()
    monkeypatch.setattr(composition, "build_stage_executor", lambda: FakeExecutor())
    monkeypatch.setenv("LINEAGE_TASK_TOKEN", "token-contract")
    monkeypatch.setenv("LINEAGE_STAGE_ENVELOPE", json.dumps({"stageId": "B5"}))

    with pytest.raises(RuntimeError, match="SCA worker failed"):
        composition.run_sca_worker(
            stepfunctions_client=client, heartbeat_interval_seconds=0.01
        )

    assert client.success is False
    assert client.failure is not None
    assert "do-not-callback" not in json.dumps(client.failure)


def test_sca_executor_verifies_the_persisted_result_before_checkpointing() -> None:
    class Control:
        recorded = False

        def claim_stage(self, *_args: Any) -> dict[str, Any]:
            return {"status": "CLAIMED", "leaseEpoch": 1}

        def record_stage(self, **_kwargs: Any) -> None:
            self.recorded = True

    class Artifacts:
        reads = 0

        def get(self, _reference: object) -> object:
            self.reads += 1
            if self.reads == 1:
                return {"schemaVersion": "1.0.0", "artifactType": "sca-work-unit"}
            return {"schemaVersion": "1.0.0", "artifactType": "corrupt-result"}

        def put(
            self, _kind: str, key: str, _body: object, _version: str
        ) -> dict[str, object]:
            return {
                "bucket": "evidence",
                "key": key,
                "versionId": "result-v1",
                "sha256": "a" * 64,
                "sizeBytes": 100,
            }

    class UseCase:
        def execute(self, _document: object, _context: object) -> StageExecutionResult:
            return StageExecutionResult(
                "sca-stage-result",
                "1.0.0",
                {"schemaVersion": "1.0.0", "artifactType": "sca-stage-result"},
            )

    control = Control()
    executor = AwsStageExecutor(
        AwsRuntimeConfig.from_env(REQUIRED_ENV),
        control,
        Artifacts(),
        FakeClient(),
        FakeClient(),
        dispatcher=StageDispatcher({("BASELINE", "B5"): UseCase()}),
    )
    envelope = {
        "commandId": "cmd-sca",
        "correlationId": "corr-sca",
        "idempotencyKey": "idem-sca:B5",
        "workflowKind": "BASELINE",
        "workflowVersion": "1.0.0",
        "stageId": "B5",
        "stageName": "RUN_BOUNDED_STATIC_ANALYSIS",
        "input": {
            "bucket": "evidence",
            "key": "work-unit.json",
            "versionId": "work-v1",
            "sha256": "b" * 64,
            "sizeBytes": 100,
        },
    }

    with pytest.raises(RuntimeError, match="read-back verification"):
        executor.execute("sca", envelope)

    assert control.recorded is False


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
                    "platform": "snowflake",
                    "system": "example",
                    "acceptedAt": "2026-08-08T15:00:00Z",
                    "activeBaseVersion": "graph-v1",
                    "activeBaseFence": 7,
                    "catalogSnapshotRef": {
                        "bucket": "evidence",
                        "key": "catalog/example.json",
                        "versionId": "catalog-v1",
                        "sha256": "c" * 64,
                        "sizeBytes": 100,
                    },
                    "catalogSnapshotId": "catalog-v1",
                    "resolverVersion": "1.0.0",
                    "rulesetVersion": "python-v1",
                    "classificationPolicyVersion": "1.0.0",
                    "runtimeManifestRefs": [],
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


def test_final_stage_preserves_terminal_outcome_on_success_and_checkpoint_replay() -> None:
    class Control:
        def __init__(self) -> None:
            self.completed: dict[str, object] | None = None

        def claim_stage(self, *_args: Any) -> dict[str, Any]:
            if self.completed is not None:
                return {"status": "COMPLETED", "output": self.completed}
            return {"status": "RUNNING", "leaseEpoch": 1}

        def record_stage(self, **kwargs: Any) -> None:
            self.completed = kwargs["output"]

    class Artifacts:
        def __init__(self) -> None:
            self.documents: dict[str, object] = {
                "input.json": {
                    "schemaVersion": "1.0.0",
                    "artifactType": "pr-freshness-decision",
                }
            }

        def get(self, reference: object) -> object:
            assert isinstance(reference, dict)
            return self.documents[str(reference["key"])]

        def put(
            self, _kind: str, key: str, body: object, _version: str
        ) -> dict[str, object]:
            self.documents[key] = body
            return {
                "bucket": "evidence",
                "key": key,
                "versionId": "v1",
                "sha256": "a" * 64,
                "sizeBytes": 100,
            }

    class FinalUseCase:
        def execute(self, _document: object, _context: object) -> StageExecutionResult:
            return StageExecutionResult(
                "pr-gate-check",
                "1.0.0",
                {
                    "schemaVersion": "1.0.0",
                    "artifactType": "pr-gate-check",
                    "terminalOutcome": "WARN",
                },
            )

    control = Control()
    artifacts = Artifacts()
    executor = AwsStageExecutor(
        AwsRuntimeConfig.from_env(REQUIRED_ENV),
        control,
        artifacts,
        FakeClient(),
        FakeClient(),
        dispatcher=StageDispatcher({("PR_GATE", "P8"): FinalUseCase()}),
    )
    envelope = {
        "commandId": "cmd-pr",
        "correlationId": "corr-pr",
        "idempotencyKey": "idem-pr:P8",
        "workflowKind": "PR_GATE",
        "workflowVersion": "1.0.0",
        "stageId": "P8",
        "stageName": "UPSERT_STABLE_GITHUB_CHECK",
        "input": {
            "bucket": "evidence",
            "key": "input.json",
            "versionId": "input-v1",
            "sha256": "b" * 64,
            "sizeBytes": 10,
        },
    }

    first = executor.execute("control-stage", envelope)
    replay = executor.execute("control-stage", envelope)

    assert first["outcome"] == "SUCCEEDED"
    assert replay["outcome"] == "SKIPPED"
    assert first["terminalOutcome"] == replay["terminalOutcome"] == "WARN"


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


def test_executor_rejects_an_unmapped_workflow_state_before_claim_or_artifact_io() -> None:
    class NoIo:
        def __getattr__(self, name: str):
            raise AssertionError(f"unexpected I/O through {name}")

    executor = AwsStageExecutor(
        AwsRuntimeConfig.from_env(REQUIRED_ENV),
        NoIo(),
        NoIo(),
        NoIo(),
        NoIo(),
        dispatcher=StageDispatcher({}),
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

    with pytest.raises(RuntimeError, match="no production use case"):
        executor.execute("classification", envelope)
