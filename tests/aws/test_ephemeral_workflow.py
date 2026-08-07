from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest


AWS_REQUIRED = os.environ.get("ALLOW_LINEAGE_EPHEMERAL_AWS_TEST") != "1"
pytestmark = pytest.mark.skipif(
    AWS_REQUIRED,
    reason="AWS_REQUIRED: approved ephemeral account, deployment and explicit opt-in are absent",
)


def _outputs() -> dict[str, Any]:
    path = Path(os.environ["LINEAGE_AWS_OUTPUTS_FILE"])
    document = json.loads(path.read_text())
    assert isinstance(document, dict) and document
    return document


def _output(document: dict[str, Any], name: str) -> str:
    matches = [stack[name] for stack in document.values() if isinstance(stack, dict) and name in stack]
    assert len(matches) == 1, f"expected exactly one CDK output named {name}"
    return str(matches[0])


def _wait_for_execution(stepfunctions: Any, alias_arn: str, command_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        executions = stepfunctions.list_executions(stateMachineArn=alias_arn, maxResults=25)["executions"]
        match = next((execution for execution in executions if execution["name"].startswith(command_id)), None)
        if match and match["status"] in {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}:
            return stepfunctions.describe_execution(executionArn=match["executionArn"])
        time.sleep(5)
    pytest.fail(f"Incremental execution for {command_id} did not finish within 15 minutes")


def test_seeded_incremental_is_exactly_once_and_leaves_ten_stage_evidence_records() -> None:
    try:
        import boto3
    except ModuleNotFoundError as error:  # pragma: no cover - only enabled in an approved AWS run
        raise AssertionError("AWS test requires apps/api optional dependency 'aws'") from error

    prefix = os.environ["LINEAGE_EPHEMERAL_PREFIX"]
    assert prefix.startswith("lineage-e2e-")
    region = os.environ["AWS_REGION"]
    session = boto3.Session(profile_name=os.environ["AWS_PROFILE"], region_name=region)
    sts = session.client("sts")
    assert sts.get_caller_identity()["Account"] == os.environ["AWS_ACCOUNT_ID"]
    outputs = _outputs()
    evidence_bucket = _output(outputs, "EvidenceBucketName")
    ledger_table = _output(outputs, "LedgerTableName")
    queue_url = _output(outputs, "eventsQueueUrl")
    alias_arn = _output(outputs, "incrementalWorkflowAliasArn")

    seed = {"repository": "seeded/runtime", "artifactDigest": "sha256:seeded-task19"}
    encoded = json.dumps(seed, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    command_id = f"task19-{int(time.time())}"
    key = f"aws-smoke/{command_id}/input.json"
    s3 = session.client("s3")
    put = s3.put_object(
        Bucket=evidence_bucket,
        Key=key,
        Body=encoded,
        ContentType="application/json",
        ChecksumAlgorithm="SHA256",
        ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode(),
        Metadata={"sha256": digest, "schema-version": "1.0.0", "kind": "seed"},
    )
    reference = {
        "bucket": evidence_bucket,
        "key": key,
        "versionId": put["VersionId"],
        "sha256": digest,
        "sizeBytes": len(encoded),
    }
    envelope = {
        "schemaVersion": "1.0.0",
        "commandId": command_id,
        "correlationId": command_id,
        "causationId": f"seed:{command_id}",
        "idempotencyKey": command_id,
        "workflowKind": "INCREMENTAL",
        "workflowVersion": "1.0.0",
        "determinantDigest": f"sha256:{digest}",
        "input": reference,
    }
    sqs = session.client("sqs")
    request = {
        "QueueUrl": queue_url,
        "MessageBody": json.dumps(envelope, sort_keys=True, separators=(",", ":")),
    }
    if queue_url.endswith(".fifo"):
        request.update(MessageGroupId=command_id, MessageDeduplicationId=command_id)
    sqs.send_message(**request)

    stepfunctions = session.client("stepfunctions")
    execution = _wait_for_execution(stepfunctions, alias_arn, command_id)
    assert execution["status"] == "SUCCEEDED", execution.get("error") or execution.get("cause")
    assert json.loads(execution["input"])["correlationId"] == command_id

    logs = session.client("logs")
    log_deadline = time.monotonic() + 120
    correlated_logs: list[dict[str, Any]] = []
    while time.monotonic() < log_deadline and not correlated_logs:
        for target in ("intake", "control-stage", "coverage", "runtime-validation", "consolidation", "proposal", "publication"):
            try:
                response = logs.filter_log_events(
                    logGroupName=f"/aws/lambda/{prefix}-{target}",
                    filterPattern=f'"{command_id}"',
                    limit=10,
                )
            except logs.exceptions.ResourceNotFoundException:
                continue
            correlated_logs.extend(response.get("events", []))
        if not correlated_logs:
            time.sleep(5)
    assert correlated_logs, "CloudWatch did not expose the workflow correlation ID"

    dynamodb = session.client("dynamodb")
    query = dynamodb.query(
        TableName=ledger_table,
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": {"S": f"COMMAND#{command_id}"}},
        ConsistentRead=True,
    )
    stage_items = {
        item["sk"]["S"].split("#", 2)[1]: item
        for item in query["Items"]
        if item["sk"]["S"].startswith("STAGE#I")
    }
    assert set(stage_items) == {f"I{index}" for index in range(1, 11)}
    assert all(item["status"]["S"] == "COMPLETED" for item in stage_items.values())
    for item in stage_items.values():
        artifact = json.loads(item["output"]["S"])
        head = s3.head_object(
            Bucket=artifact["bucket"], Key=artifact["key"], VersionId=artifact["versionId"]
        )
        assert head["Metadata"]["sha256"] == artifact["sha256"]

    execution_count = len(
        [
            item
            for item in stepfunctions.list_executions(stateMachineArn=alias_arn, maxResults=25)["executions"]
            if item["name"].startswith(command_id)
        ]
    )
    duplicate = dict(request)
    if queue_url.endswith(".fifo"):
        duplicate["MessageDeduplicationId"] = f"{command_id}-transport-redelivery"
    sqs.send_message(**duplicate)
    time.sleep(20)
    repeated = dynamodb.query(
        TableName=ledger_table,
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": {"S": f"COMMAND#{command_id}"}},
        ConsistentRead=True,
    )
    assert len([item for item in repeated["Items"] if item["sk"]["S"].startswith("STAGE#I")]) == 10
    assert len(
        [
            item
            for item in stepfunctions.list_executions(stateMachineArn=alias_arn, maxResults=25)["executions"]
            if item["name"].startswith(command_id)
        ]
    ) == execution_count
