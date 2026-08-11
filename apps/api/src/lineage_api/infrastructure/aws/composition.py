from __future__ import annotations

import json
import hashlib
import os
import re
import threading
from datetime import UTC, datetime
from typing import Any, Mapping

from lineage_api.application.sca_aggregation import BaselineScaAggregationUseCase
from lineage_api.application.stage_execution import (
    StageDispatcher,
    StageExecutionContext,
    StageExecutionResult,
    validate_artifact_reference,
)
from lineage_api.application.stage_handlers import production_stage_use_cases
from lineage_api.application.workflows.definitions import WORKFLOWS
from lineage_api.infrastructure.aws.config import AwsRuntimeConfig
from lineage_api.infrastructure.aws.dynamodb_control import DynamoDbControlAdapter
from lineage_api.infrastructure.aws.errors import AwsConflictError, AwsRetryableError, aws_call
from lineage_api.infrastructure.aws.kinesis_runtime import KinesisRuntimeAdapter
from lineage_api.infrastructure.aws.neptune_projection import NeptuneProjectionAdapter
from lineage_api.infrastructure.aws.s3_artifacts import S3ArtifactStore
from lineage_api.infrastructure.aws.s3_map_results import S3MapResultReader
from lineage_api.infrastructure.aws.s3_sources import S3SourceArchiveStore
from lineage_api.infrastructure.aws.sqs_broker import SqsLaneBroker


class StepFunctionsWorkflowStarter:
    def __init__(
        self,
        client: Any,
        alias_arns: Mapping[str, str],
        *,
        baseline_map_concurrency: int,
    ) -> None:
        if not 1 <= baseline_map_concurrency <= 10_000:
            raise RuntimeError("LINEAGE_BASELINE_MAP_CONCURRENCY must be between 1 and 10000")
        self.client = client
        self.alias_arns = dict(alias_arns)
        self.baseline_map_concurrency = baseline_map_concurrency

    def start(self, envelope: dict[str, Any]) -> str:
        kind = str(envelope.get("workflowKind", ""))
        try:
            alias_arn = self.alias_arns[kind]
        except KeyError as error:
            raise RuntimeError(f"no workflow alias configured for {kind}") from error
        workflow_input = {**envelope, "baselineMapConcurrency": self.baseline_map_concurrency}
        encoded = json.dumps(workflow_input, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode()).hexdigest()[:24]
        command = re.sub(r"[^A-Za-z0-9-_]", "-", str(envelope["commandId"]))[:48]
        name = f"{command}-{digest}"[:80]
        try:
            response = aws_call(
                "stepfunctions.start_execution",
                self.client.start_execution,
                stateMachineArn=alias_arn,
                name=name,
                input=encoded,
                traceHeader=str(envelope.get("correlationId", ""))[:256],
            )
        except AwsConflictError:
            return f"already-started:{name}"
        return str(response["executionArn"])


class AwsStageExecutor:
    """Idempotent AWS stage checkpoint executor used by thin Lambda/Fargate entry points."""

    def __init__(
        self,
        config: AwsRuntimeConfig,
        control: DynamoDbControlAdapter,
        artifacts: S3ArtifactStore,
        runtime: KinesisRuntimeAdapter,
        projection: NeptuneProjectionAdapter,
        packages: S3ArtifactStore | None = None,
        broker: SqsLaneBroker | None = None,
        workflow_starter: StepFunctionsWorkflowStarter | None = None,
        sources: S3SourceArchiveStore | None = None,
        dispatcher: StageDispatcher | None = None,
    ) -> None:
        self.config = config
        self.control = control
        self.artifacts = artifacts
        self.runtime = runtime
        self.projection = projection
        self.packages = packages or artifacts
        self.broker = broker
        self.workflow_starter = workflow_starter
        self.sources = sources
        self.dispatcher = dispatcher or StageDispatcher(
            production_stage_use_cases(
                artifacts,
                control,
                packages=self.packages,
                publication_control=control,
                projection=projection,
                sources=sources,
                pr_gate_control=control,
                impact_projection=projection,
            )
        )

    def execute(self, target: str, envelope: dict[str, Any]) -> dict[str, Any]:
        stage_id = str(envelope.get("stageId") or target)
        command_id = str(envelope["commandId"])
        idempotency_key = str(envelope["idempotencyKey"])
        workflow_kind = str(envelope.get("workflowKind", ""))
        if target != "intake":
            # This duplicate boundary guard is intentional: entry points reject
            # misrouting, and the executor remains safe when called directly by
            # tests, callback workers or future event-source integrations.
            StageExecutionContext(
                target=target,
                workflow_kind=str(envelope.get("workflowKind", "")),
                workflow_version=str(envelope.get("workflowVersion", "")),
                stage_id=stage_id,
                stage_name=str(envelope.get("stageName", "")),
                command_id=command_id,
                correlation_id=str(envelope.get("correlationId", "")),
                input_reference=envelope.get("input", {}),
            )
            if not self.dispatcher.has_use_case(workflow_kind, stage_id):
                raise RuntimeError(
                    f"no production use case for {workflow_kind}/{stage_id}"
                )
        owner = os.environ.get("AWS_LAMBDA_LOG_STREAM_NAME", f"{target}-worker")
        now = datetime.now(UTC)
        try:
            lease = self.control.claim_stage(
                command_id,
                stage_id,
                idempotency_key,
                owner,
                300,
                now,
            )
            if lease.get("status") == "COMPLETED":
                self._acknowledge_outbox(target, envelope, now)
                return self._response(
                    "SKIPPED",
                    lease["output"],
                    str(envelope.get("workflowKind", "")),
                    stage_id,
                )
            input_body = self.artifacts.get(envelope["input"])
            if target != "intake":
                execution = self.dispatcher.execute(target, envelope, input_body)
                artifact_kind = execution.artifact_kind
                artifact_version = execution.schema_version
                persisted_body: object = execution.document
            else:
                output_body = {
                    "schemaVersion": "1.0.0",
                    "commandId": command_id,
                    "correlationId": envelope["correlationId"],
                    "workflowKind": envelope.get("workflowKind"),
                    "workflowVersion": envelope.get("workflowVersion"),
                    "stageId": stage_id,
                    "stageName": envelope.get("stageName", target),
                    "target": target,
                    "input": envelope["input"],
                    "inputDocument": input_body,
                }
                persisted_body = output_body
                artifact_kind = "stage-result"
                artifact_version = "1.0.0"
            reference = self.artifacts.put(
                artifact_kind,
                f"commands/{command_id}/stages/{stage_id}/{idempotency_key}.json",
                persisted_body,
                artifact_version,
            )
            if target == "sca" and self.artifacts.get(reference) != persisted_body:
                raise RuntimeError("SCA result failed immutable read-back verification")
            if target == "intake":
                if self.workflow_starter is None:
                    raise RuntimeError("intake workflow aliases are not configured")
                self.workflow_starter.start(envelope)
            self.control.record_stage(
                command_id=command_id,
                stage_id=stage_id,
                idempotency_key=idempotency_key,
                lease_owner=owner,
                lease_epoch=int(lease["leaseEpoch"]),
                output=reference,
                completed_at=now,
                run_projection=(
                    None
                    if target == "intake"
                    else self._run_projection(envelope, persisted_body, now)
                ),
            )
            self._acknowledge_outbox(target, envelope, now)
            return self._response(
                "SUCCEEDED",
                reference,
                str(envelope.get("workflowKind", "")),
                stage_id,
                persisted_body,
            )
        except AwsRetryableError:
            return {"outcome": "REDRIVE_REQUIRED", "output": dict(envelope["input"])}
        except AwsConflictError:
            existing = self.control.get_stage(command_id, stage_id, idempotency_key)
            if existing and existing.get("status") == "COMPLETED":
                return self._response(
                    "SKIPPED",
                    existing["output"],
                    str(envelope.get("workflowKind", "")),
                    stage_id,
                )
            return {"outcome": "REDRIVE_REQUIRED", "output": dict(envelope["input"])}

    @staticmethod
    def _run_projection(
        envelope: Mapping[str, Any], document: object, completed_at: datetime
    ) -> dict[str, Any]:
        workflow_kind = str(envelope["workflowKind"])
        stage_id = str(envelope["stageId"])
        workflow = WORKFLOWS.get(workflow_kind)  # type: ignore[arg-type]
        if workflow is None or stage_id not in workflow.stage_ids:
            raise RuntimeError("run projection received an unknown workflow stage")
        context = document.get("context") if isinstance(document, Mapping) else None
        values = context if isinstance(context, Mapping) else {}
        terminal = document.get("terminalOutcome") if isinstance(document, Mapping) else None
        fallback_created_at = (
            completed_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        )
        created_at = values.get("acceptedAt", fallback_created_at)
        environment = values.get("environment", "UNKNOWN")
        system = values.get("system", "UNKNOWN")
        projection = {
            "workflowKind": workflow_kind,
            "workflowVersion": str(envelope["workflowVersion"]),
            "stageName": str(envelope["stageName"]),
            "stageOrdinal": workflow.stage_ids.index(stage_id) + 1,
            "correlationId": str(envelope["correlationId"]),
            "environment": str(environment),
            "system": str(system),
            "createdAt": str(created_at),
            "status": str(terminal) if isinstance(terminal, str) else "RUNNING",
        }
        if isinstance(terminal, str):
            projection["terminalOutcome"] = terminal
        return projection

    def _acknowledge_outbox(
        self, target: str, envelope: Mapping[str, Any], delivered_at: datetime
    ) -> None:
        outbox_id = envelope.get("outboxId")
        if outbox_id is None:
            return
        if target != "publication" or not isinstance(outbox_id, str) or not outbox_id:
            raise ValueError("outbox acknowledgement target is invalid")
        try:
            self.control.mark_outbox_delivered(
                outbox_id,
                str(envelope["correlationId"]),
                delivered_at=delivered_at,
            )
        except AwsConflictError as error:
            # This conflict is about the outbox identity, not the stage lease.
            # Keep it out of the generic completed-stage replay path so a
            # missing or mismatched event is retried and ultimately dead-lettered.
            raise RuntimeError("outbox acknowledgement conflict") from error

    def _response(
        self,
        outcome: str,
        reference: dict[str, Any],
        workflow_kind: str,
        stage_id: str,
        document: object | None = None,
    ) -> dict[str, Any]:
        response: dict[str, Any] = {"outcome": outcome, "output": reference}
        final_stage = {
            ("BASELINE", "B10"),
            ("INCREMENTAL", "I10"),
            ("PR_GATE", "P8"),
            ("NIGHTLY", "N6"),
        }
        if (workflow_kind, stage_id) in final_stage:
            stored = self.artifacts.get(reference) if document is None else document
            if isinstance(stored, Mapping) and isinstance(
                stored.get("terminalOutcome"), str
            ):
                response["terminalOutcome"] = stored["terminalOutcome"]
        return response


class AwsScaAggregationExecutor:
    """Lease, persist and replay the Baseline Distributed Map aggregate."""

    def __init__(self, control: Any, artifacts: Any, use_case: Any) -> None:
        self.control = control
        self.artifacts = artifacts
        self.use_case = use_case

    def execute(self, event: dict[str, Any]) -> dict[str, Any]:
        command_id = str(event["commandId"])
        idempotency_key = str(event["idempotencyKey"])
        stage_id = "B5A"
        owner = os.environ.get("AWS_LAMBDA_LOG_STREAM_NAME", "sca-aggregate-worker")
        now = datetime.now(UTC)
        try:
            lease = self.control.claim_stage(
                command_id,
                stage_id,
                idempotency_key,
                owner,
                300,
                now,
            )
            if lease.get("status") == "COMPLETED":
                return {"outcome": "SKIPPED", "output": lease["output"]}
            execution = self.use_case.execute(event)
            if not isinstance(execution, StageExecutionResult):
                raise RuntimeError("SCA aggregation use case returned an invalid result")
            if (
                execution.artifact_kind != "sca-batch-result"
                or execution.schema_version != "1.0.0"
            ):
                raise RuntimeError("SCA aggregation artifact contract is invalid")
            reference = self.artifacts.put(
                execution.artifact_kind,
                f"commands/{command_id}/stages/{stage_id}/{idempotency_key}.json",
                execution.document,
                execution.schema_version,
            )
            if self.artifacts.get(reference) != execution.document:
                raise RuntimeError(
                    "SCA aggregation failed immutable read-back verification"
                )
            self.control.record_stage(
                command_id=command_id,
                stage_id=stage_id,
                idempotency_key=idempotency_key,
                lease_owner=owner,
                lease_epoch=int(lease["leaseEpoch"]),
                output=reference,
                completed_at=now,
            )
            return {"outcome": "SUCCEEDED", "output": reference}
        except AwsRetryableError:
            return {"outcome": "REDRIVE_REQUIRED", "output": dict(event["workInventory"])}
        except AwsConflictError:
            existing = self.control.get_stage(command_id, stage_id, idempotency_key)
            if existing and existing.get("status") == "COMPLETED":
                return {"outcome": "SKIPPED", "output": existing["output"]}
            return {"outcome": "REDRIVE_REQUIRED", "output": dict(event["workInventory"])}


def _clients(config: AwsRuntimeConfig) -> dict[str, Any]:
    try:
        import boto3
    except ModuleNotFoundError as error:
        raise RuntimeError("boto3 AWS runtime extra is required") from error
    session = boto3.session.Session(region_name=config.region)
    return {
        "dynamodb": session.client("dynamodb"),
        "s3": session.client("s3"),
        "sqs": session.client("sqs"),
        "kinesis": session.client("kinesis"),
        "neptunedata": session.client(
            "neptunedata", endpoint_url=f"https://{config.neptune_endpoint}:8182"
        ),
        "stepfunctions": session.client("stepfunctions"),
    }


def build_stage_executor(
    *,
    env: Mapping[str, str] | None = None,
    clients: Mapping[str, Any] | None = None,
) -> AwsStageExecutor:
    values = os.environ if env is None else env
    config = AwsRuntimeConfig.from_env(values)
    sdk = dict(_clients(config) if clients is None else clients)
    control = DynamoDbControlAdapter(
        sdk["dynamodb"],
        config.control_table,
        config.ledger_table,
        config.pointer_table,
        proposal_table=config.proposal_table,
    )
    queues = {
        lane: values[key]
        for lane, key in {
            "interactive": "LINEAGE_INTERACTIVE_QUEUE_URL",
            "events": "LINEAGE_EVENTS_QUEUE_URL",
            "batch": "LINEAGE_BATCH_QUEUE_URL",
        }.items()
        if values.get(key)
    }
    aliases = {
        kind: values[key]
        for kind, key in {
            "BASELINE": "LINEAGE_BASELINE_WORKFLOW_ALIAS_ARN",
            "INCREMENTAL": "LINEAGE_INCREMENTAL_WORKFLOW_ALIAS_ARN",
            "PR_GATE": "LINEAGE_PR_GATE_WORKFLOW_ALIAS_ARN",
            "NIGHTLY": "LINEAGE_NIGHTLY_WORKFLOW_ALIAS_ARN",
        }.items()
        if values.get(key)
    }
    if aliases and len(aliases) != 4:
        raise RuntimeError("intake requires all four workflow aliases or none")
    raw_concurrency = values.get("LINEAGE_BASELINE_MAP_CONCURRENCY", "1")
    try:
        baseline_concurrency = int(raw_concurrency)
    except ValueError as error:
        raise RuntimeError("LINEAGE_BASELINE_MAP_CONCURRENCY must be an integer") from error
    return AwsStageExecutor(
        config,
        control,
        S3ArtifactStore(sdk["s3"], config.evidence_bucket),
        KinesisRuntimeAdapter(sdk["kinesis"], config.runtime_stream),
        NeptuneProjectionAdapter(sdk["neptunedata"]),
        S3ArtifactStore(sdk["s3"], config.package_bucket),
        SqsLaneBroker(sdk["sqs"], queues) if queues else None,
        StepFunctionsWorkflowStarter(
            sdk["stepfunctions"], aliases, baseline_map_concurrency=baseline_concurrency
        )
        if aliases
        else None,
        S3SourceArchiveStore(sdk["s3"]),
    )


def build_sca_aggregation_executor(
    *,
    env: Mapping[str, str] | None = None,
    clients: Mapping[str, Any] | None = None,
) -> AwsScaAggregationExecutor:
    values = os.environ if env is None else env
    config = AwsRuntimeConfig.from_env(values)
    sdk = dict(_clients(config) if clients is None else clients)
    control = DynamoDbControlAdapter(
        sdk["dynamodb"],
        config.control_table,
        config.ledger_table,
        config.pointer_table,
        proposal_table=config.proposal_table,
    )
    artifacts = S3ArtifactStore(sdk["s3"], config.evidence_bucket)
    return AwsScaAggregationExecutor(
        control,
        artifacts,
        BaselineScaAggregationUseCase(
            artifacts,
            S3MapResultReader(sdk["s3"], config.evidence_bucket),
        ),
    )


def run_sca_worker(
    *,
    stepfunctions_client: Any | None = None,
    heartbeat_interval_seconds: float | None = None,
) -> None:
    token = os.environ.get("LINEAGE_TASK_TOKEN", "").strip()
    raw_envelope = os.environ.get("LINEAGE_STAGE_ENVELOPE", "").strip()
    if not token:
        raise RuntimeError("LINEAGE_TASK_TOKEN is required")
    if raw_envelope:
        envelope = json.loads(raw_envelope)
        if not isinstance(envelope, dict):
            raise RuntimeError("LINEAGE_STAGE_ENVELOPE must be a JSON object")
    else:
        envelope = {}
    for key, environment_name in {
        "schemaVersion": "LINEAGE_STAGE_SCHEMA_VERSION",
        "commandId": "LINEAGE_STAGE_COMMAND_ID",
        "correlationId": "LINEAGE_STAGE_CORRELATION_ID",
        "causationId": "LINEAGE_STAGE_CAUSATION_ID",
        "determinantDigest": "LINEAGE_STAGE_DETERMINANT_DIGEST",
        "stageId": "LINEAGE_STAGE_ID",
        "stageName": "LINEAGE_STAGE_NAME",
        "workflowKind": "LINEAGE_WORKFLOW_KIND",
        "workflowVersion": "LINEAGE_WORKFLOW_VERSION",
    }.items():
        authoritative = os.environ.get(environment_name, "").strip()
        if authoritative:
            envelope[key] = authoritative
    raw_stage_input = os.environ.get("LINEAGE_STAGE_INPUT", "").strip()
    if raw_stage_input:
        try:
            envelope["input"] = json.loads(raw_stage_input)
        except json.JSONDecodeError as error:
            raise RuntimeError("LINEAGE_STAGE_INPUT must be valid JSON") from error
    stage_idempotency_key = os.environ.get(
        "LINEAGE_STAGE_IDEMPOTENCY_KEY", ""
    ).strip()
    if stage_idempotency_key:
        envelope["idempotencyKey"] = stage_idempotency_key
    if not raw_envelope and not all(
        envelope.get(name)
        for name in (
            "schemaVersion",
            "commandId",
            "correlationId",
            "causationId",
            "determinantDigest",
            "idempotencyKey",
            "input",
            "stageId",
            "stageName",
            "workflowKind",
            "workflowVersion",
        )
    ):
        raise RuntimeError("exact SCA stage environment is incomplete")
    executor = build_stage_executor()
    client = stepfunctions_client
    if client is None:
        client = _clients(executor.config)["stepfunctions"]
    if heartbeat_interval_seconds is None:
        try:
            heartbeat_interval_seconds = float(
                os.environ.get("LINEAGE_SCA_HEARTBEAT_SECONDS", "60")
            )
        except ValueError as error:
            raise RuntimeError(
                "LINEAGE_SCA_HEARTBEAT_SECONDS must be numeric"
            ) from error
        if not 1 <= heartbeat_interval_seconds <= 240:
            raise RuntimeError(
                "LINEAGE_SCA_HEARTBEAT_SECONDS must be between 1 and 240"
            )
    elif heartbeat_interval_seconds <= 0:
        raise ValueError("SCA heartbeat interval must be positive")

    stopped = threading.Event()
    heartbeat_failed = threading.Event()

    def heartbeat_loop() -> None:
        while not stopped.wait(heartbeat_interval_seconds):
            try:
                client.send_task_heartbeat(taskToken=token)
            except Exception:
                heartbeat_failed.set()
                stopped.set()
                return

    heartbeat_thread: threading.Thread | None = None
    try:
        client.send_task_heartbeat(taskToken=token)
        heartbeat_thread = threading.Thread(
            target=heartbeat_loop,
            name="lineage-sca-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
        result = executor.execute("sca", envelope)
        stopped.set()
        heartbeat_thread.join(timeout=5)
        if heartbeat_thread.is_alive() or heartbeat_failed.is_set():
            raise RuntimeError("SCA callback heartbeat failed")
        if (
            not isinstance(result, dict)
            or set(result) != {"outcome", "output"}
            or result.get("outcome")
            not in {"SUCCEEDED", "SKIPPED", "REDRIVE_REQUIRED"}
        ):
            raise RuntimeError("SCA callback result contract is invalid")
        result = {
            "outcome": result["outcome"],
            "output": validate_artifact_reference(result["output"]),
        }
        output = json.dumps(result, sort_keys=True, separators=(",", ":"))
        if len(output.encode()) >= 8_192:
            raise RuntimeError("SCA callback exceeds bounded result contract")
        client.send_task_success(taskToken=token, output=output)
    except Exception:
        stopped.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=5)
        raw_correlation = envelope.get("correlationId")
        correlation_id = (
            raw_correlation
            if isinstance(raw_correlation, str)
            and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", raw_correlation)
            else "unavailable"
        )
        try:
            client.send_task_failure(
                taskToken=token,
                error="SCAExecutionFailed",
                cause=f"SCA stage failed; correlationId={correlation_id}",
            )
        except Exception:
            pass
        raise RuntimeError("SCA worker failed") from None
