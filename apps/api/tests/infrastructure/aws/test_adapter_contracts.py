from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from lineage_api.infrastructure.aws.dynamodb_control import DynamoDbControlAdapter
from lineage_api.infrastructure.aws.errors import (
    AwsConflictError,
    AwsMissingObjectError,
    AwsRetryableError,
    AwsStaleFenceError,
)
from lineage_api.infrastructure.aws.kinesis_runtime import KinesisRuntimeAdapter
from lineage_api.infrastructure.aws.neptune_projection import NeptuneProjectionAdapter
from lineage_api.infrastructure.aws.s3_artifacts import S3ArtifactStore
from lineage_api.infrastructure.aws.sqs_broker import SqsLaneBroker


class FakeServiceError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code, "Message": code}}


class FakeClient:
    def __init__(self, **responses: Any) -> None:
        self.responses = {name: list(values) for name, values in responses.items()}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str):
        def call(**kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            values = self.responses.get(name, [{}])
            value = values.pop(0) if values else {}
            if isinstance(value, Exception):
                raise value
            return value

        return call


def test_dynamodb_builds_conditional_receipt_lease_stage_outbox_and_pointer_requests() -> None:
    client = FakeClient(
        put_item=[{}, {}, {}],
        update_item=[
            {"Attributes": {"leaseEpoch": {"N": "4"}, "leaseExpiresAt": {"S": "2026-08-06T00:01:00Z"}}},
            {},
            {"Attributes": {"fence": {"N": "9"}}},
        ],
    )
    adapter = DynamoDbControlAdapter(client, "control", "ledger", "pointer")
    now = datetime(2026, 8, 6, tzinfo=UTC)

    adapter.accept_receipt("evt-1", "s3://evidence/event.json?versionId=v1", "corr-1", now)
    lease = adapter.claim_lease("cmd-1", "worker-1", 60, now)
    adapter.record_stage(
        command_id="cmd-1",
        stage_id="I1",
        idempotency_key="idem-1",
        lease_owner="worker-1",
        lease_epoch=lease["leaseEpoch"],
        output={"bucket": "evidence", "key": "i1.json", "versionId": "v1", "sha256": "a" * 64, "sizeBytes": 10},
        completed_at=now,
    )
    adapter.append_outbox("out-1", "events", "corr-1", {"commandId": "cmd-1"}, now)
    adapter.swap_pointer("prod", "graph-v9", expected_fence=8, next_fence=9, correlation_id="corr-1")

    receipt = client.calls[0][1]
    assert receipt["ConditionExpression"] == "attribute_not_exists(pk)"
    claim = next(kwargs for name, kwargs in client.calls if name == "update_item")
    assert "leaseExpiresAt < :now" in claim["ConditionExpression"]
    assert "leaseEpoch = :expectedEpoch" in claim["ConditionExpression"]
    stage = [kwargs for name, kwargs in client.calls if name == "put_item"][1]
    assert "outputSha256 = :outputSha256" in stage["ConditionExpression"]
    pointer = [kwargs for name, kwargs in client.calls if name == "update_item"][-1]
    assert pointer["ConditionExpression"] == "attribute_not_exists(fence) OR fence = :expectedFence"


def test_dynamodb_translates_conditional_and_throttling_failures() -> None:
    conditional = DynamoDbControlAdapter(
        FakeClient(put_item=[FakeServiceError("ConditionalCheckFailedException")]),
        "control",
        "ledger",
        "pointer",
    )
    with pytest.raises(AwsConflictError):
        conditional.accept_receipt("evt", "ref", "corr", datetime.now(UTC))

    throttled = DynamoDbControlAdapter(
        FakeClient(update_item=[FakeServiceError("ProvisionedThroughputExceededException")]),
        "control",
        "ledger",
        "pointer",
    )
    with pytest.raises(AwsRetryableError):
        throttled.claim_lease("cmd", "worker", 30, datetime.now(UTC))

    stale_pointer = DynamoDbControlAdapter(
        FakeClient(update_item=[FakeServiceError("ConditionalCheckFailedException")]),
        "control",
        "ledger",
        "pointer",
    )
    with pytest.raises(AwsStaleFenceError):
        stale_pointer.swap_pointer(
            "prod",
            "graph-v2",
            expected_fence=1,
            next_fence=2,
            correlation_id="corr-1",
        )


def test_dynamodb_conditionally_persists_and_replays_one_proposal_version() -> None:
    proposal = {
        "schemaVersion": "1.0.0",
        "proposalId": "proposal-001",
        "version": 1,
        "state": "IN_REVIEW",
        "system": "payments",
        "expectedBaseVersion": "graph-v1",
    }
    client = FakeClient(put_item=[{}])
    adapter = DynamoDbControlAdapter(
        client, "control", "ledger", "pointer", proposal_table="proposal"
    )

    assert adapter.put_proposal(proposal) == proposal
    request = client.calls[0][1]
    assert request["TableName"] == "proposal"
    assert request["ConditionExpression"] == "attribute_not_exists(pk)"
    assert request["Item"]["pk"] == {"S": "PROPOSAL#proposal-001"}
    assert request["Item"]["sk"] == {"S": "VERSION#0000000001"}

    replay_client = FakeClient(
        put_item=[FakeServiceError("ConditionalCheckFailedException")],
        get_item=[{"Item": {"document": {"S": json.dumps(proposal)}}}],
    )
    replay = DynamoDbControlAdapter(
        replay_client, "control", "ledger", "pointer", proposal_table="proposal"
    )
    assert replay.put_proposal(proposal) == proposal

    conflict_client = FakeClient(
        put_item=[FakeServiceError("ConditionalCheckFailedException")],
        get_item=[
            {
                "Item": {
                    "document": {
                        "S": json.dumps({**proposal, "state": "APPROVED"})
                    }
                }
            }
        ],
    )
    conflict = DynamoDbControlAdapter(
        conflict_client, "control", "ledger", "pointer", proposal_table="proposal"
    )
    with pytest.raises(AwsConflictError):
        conflict.put_proposal(proposal)


def test_dynamodb_reads_and_atomically_activates_pointer_with_outbox() -> None:
    package_reference = {
        "bucket": "packages",
        "key": "packages/package-001.json",
        "versionId": "v1",
        "sha256": "a" * 64,
        "sizeBytes": 400,
    }
    client = FakeClient(
        get_item=[
            {
                "Item": {
                    "graphVersion": {"S": "graph-v1"},
                    "fence": {"N": "7"},
                    "correlationId": {"S": "corr-prior"},
                }
            }
        ],
        transact_write_items=[{}],
    )
    adapter = DynamoDbControlAdapter(client, "control", "ledger", "pointer")

    assert adapter.active_pointer("staging")["graphVersion"] == "graph-v1"
    activated = adapter.activate_pointer(
        environment="staging",
        graph_version="graph-v2",
        graph_checksum="b" * 64,
        package_reference=package_reference,
        expected_prior="graph-v1",
        expected_fence=7,
        next_fence=8,
        correlation_id="corr-publish",
        activated_at=datetime(2026, 8, 8, 13, tzinfo=UTC),
    )

    assert activated["fence"] == 8
    transaction = client.calls[-1][1]["TransactItems"]
    pointer_update = transaction[0]["Update"]
    assert "graphVersion = :expectedPrior" in pointer_update["ConditionExpression"]
    assert pointer_update["ExpressionAttributeValues"][":nextFence"] == {"N": "8"}
    outbox_put = transaction[1]["Put"]
    assert outbox_put["TableName"] == "ledger"
    assert outbox_put["Item"]["topic"] == {"S": "PUBLICATION_ACTIVATED"}

    replay_client = FakeClient(
        transact_write_items=[FakeServiceError("TransactionCanceledException")],
        get_item=[
            {
                "Item": {
                    "graphVersion": {"S": "graph-v2"},
                    "graphChecksum": {"S": "b" * 64},
                    "packageReference": {"S": json.dumps(package_reference)},
                    "fence": {"N": "8"},
                    "correlationId": {"S": "corr-publish"},
                }
            }
        ],
    )
    replay = DynamoDbControlAdapter(
        replay_client, "control", "ledger", "pointer"
    )
    assert (
        replay.activate_pointer(
            environment="staging",
            graph_version="graph-v2",
            graph_checksum="b" * 64,
            package_reference=package_reference,
            expected_prior="graph-v1",
            expected_fence=7,
            next_fence=8,
            correlation_id="corr-publish",
            activated_at=datetime(2026, 8, 8, 13, tzinfo=UTC),
        )["graphVersion"]
        == "graph-v2"
    )


def test_neptune_copies_applies_tombstones_and_reads_a_staged_namespace() -> None:
    client = FakeClient(
        execute_open_cypher_query=[
            {"results": [{"written": 3}]},
            {"results": [{"deleted": 1}]},
            {"results": [{"written": 1}]},
            {
                "results": [
                    {
                        "edgeId": "edge-1",
                        "source": "urn:a",
                        "target": "urn:b",
                        "type": "DERIVES",
                    }
                ]
            },
        ]
    )
    projection = NeptuneProjectionAdapter(client)

    assert projection.copy_namespace("graph-v1", "graph-v2", fence=8) == 3
    assert projection.delete_edges("graph-v2", ["edge-old"]) == 1
    assert (
        projection.merge_edges(
            "graph-v2",
            [
                {
                    "edgeId": "edge-1",
                    "source": "urn:a",
                    "target": "urn:b",
                    "type": "DERIVES",
                }
            ],
            fence=8,
        )
        == 1
    )
    assert projection.namespace_checksum("graph-v2")[0]["edgeId"] == "edge-1"

    queries = [call[1]["openCypherQuery"] for call in client.calls]
    parameters = [json.loads(call[1]["parameters"]) for call in client.calls]
    assert "copy:LINEAGE" in queries[0]
    assert "DELETE edge" in queries[1]
    assert "MERGE (source)-[edge:LINEAGE" in queries[2]
    assert "ORDER BY edgeId" in queries[3]
    assert parameters[0]["targetNamespace"] == "graph-v2"
    assert parameters[1]["edgeIds"] == ["edge-old"]


def test_s3_uses_versioned_checksum_references_and_immutable_puts() -> None:
    body = {"commandId": "cmd-1", "stage": "I1"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    client = FakeClient(
        put_object=[{"VersionId": "version-1"}],
        get_object=[
            {
                "Body": type("Body", (), {"read": lambda self: encoded})(),
                "VersionId": "version-1",
                "ContentLength": len(encoded),
                "Metadata": {"sha256": digest, "schema-version": "1.0.0"},
            }
        ],
    )
    store = S3ArtifactStore(client, "evidence")

    reference = store.put("stage-result", "commands/cmd-1/I1.json", body, "1.0.0")
    assert reference == {
        "bucket": "evidence",
        "key": "commands/cmd-1/I1.json",
        "versionId": "version-1",
        "sha256": digest,
        "sizeBytes": len(encoded),
    }
    request = client.calls[0][1]
    assert request["IfNoneMatch"] == "*"
    assert request["ChecksumSHA256"] == base64.b64encode(bytes.fromhex(digest)).decode()
    assert store.get(reference) == body


def test_s3_translates_missing_versioned_objects() -> None:
    store = S3ArtifactStore(
        FakeClient(get_object=[FakeServiceError("NoSuchKey")]),
        "evidence",
    )
    with pytest.raises(AwsMissingObjectError):
        store.get({"bucket": "evidence", "key": "missing", "versionId": "v1", "sha256": "a" * 64, "sizeBytes": 1})


def test_sqs_preserves_fifo_ordering_deduplication_and_correlation() -> None:
    client = FakeClient(send_message=[{"MessageId": "message-1"}, {"MessageId": "message-2"}])
    broker = SqsLaneBroker(
        client,
        {"interactive": "https://sqs/interactive.fifo", "events": "https://sqs/events.fifo", "batch": "https://sqs/batch"},
    )

    broker.publish("interactive", "repo-1", "s3://input", "corr-1", message_id="dedupe-1")
    broker.publish("batch", "repo-1", "s3://input", "corr-1", message_id="dedupe-2")

    fifo = client.calls[0][1]
    assert fifo["MessageGroupId"] == "repo-1"
    assert fifo["MessageDeduplicationId"] == "dedupe-1"
    assert fifo["MessageAttributes"]["correlationId"]["StringValue"] == "corr-1"
    standard = client.calls[1][1]
    assert "MessageGroupId" not in standard
    assert "MessageDeduplicationId" not in standard


def test_sqs_claim_exposes_the_actual_visibility_lease_window() -> None:
    client = FakeClient(
        receive_message=[
            {
                "Messages": [
                    {
                        "MessageId": "message-1",
                        "ReceiptHandle": "receipt-1",
                        "Body": json.dumps(
                            {
                                "messageId": "message-1",
                                "lane": "events",
                                "groupKey": "repo-1",
                                "payloadRef": "s3://input",
                                "correlationId": "corr-1",
                                "maxAttempts": 5,
                                "supersessionKey": None,
                            }
                        ),
                        "Attributes": {"ApproximateReceiveCount": "2"},
                    }
                ]
            }
        ]
    )
    broker = SqsLaneBroker(client, {"events": "https://sqs/events.fifo"})
    before = datetime.now(UTC) + timedelta(seconds=44)

    message = broker.claim("events", "worker-1", visibility_timeout_seconds=45)

    assert message is not None
    assert message.lease_expires_at >= before
    assert message.owner == "worker-1|receipt-1"


def test_kinesis_and_neptune_requests_are_partitioned_and_idempotent() -> None:
    kinesis = FakeClient(put_record=[{"SequenceNumber": "7", "ShardId": "shard-1"}])
    runtime = KinesisRuntimeAdapter(kinesis, "runtime-stream")
    result = runtime.put_observation("dataset-urn", {"artifactDigest": "sha256:abc"})
    assert result == {"sequenceNumber": "7", "shardId": "shard-1"}
    assert kinesis.calls[0][1]["PartitionKey"] == "dataset-urn"

    neptune = FakeClient(execute_open_cypher_query=[{"results": [{"written": 1}]}])
    projection = NeptuneProjectionAdapter(neptune)
    projection.merge_edges(
        "graph-v1",
        [{"edgeId": "edge-1", "source": "urn:a", "target": "urn:b", "type": "DERIVES"}],
        fence=4,
    )
    request = neptune.calls[0][1]
    assert "MERGE (source:Dataset" in request["openCypherQuery"]
    assert "MERGE (source)-[edge:LINEAGE" in request["openCypherQuery"]
    assert json.loads(request["parameters"])["fence"] == 4
