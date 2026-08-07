from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from lineage_api.infrastructure.aws.errors import (
    AwsConflictError,
    AwsStaleFenceError,
    aws_call,
)


def _utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class DynamoDbControlAdapter:
    """Conditional control/ledger/pointer operations over low-level DynamoDB clients."""

    def __init__(self, client: Any, control_table: str, ledger_table: str, pointer_table: str) -> None:
        self.client = client
        self.control_table = control_table
        self.ledger_table = ledger_table
        self.pointer_table = pointer_table

    def accept_receipt(
        self,
        event_id: str,
        payload_ref: str,
        correlation_id: str,
        created_at: datetime,
    ) -> bool:
        aws_call(
            "dynamodb.accept_receipt",
            self.client.put_item,
            TableName=self.control_table,
            Item={
                "pk": {"S": f"RECEIPT#{event_id}"},
                "sk": {"S": "RECEIPT"},
                "payloadRef": {"S": payload_ref},
                "correlationId": {"S": correlation_id},
                "createdAt": {"S": _utc(created_at)},
            },
            ConditionExpression="attribute_not_exists(pk)",
        )
        return True

    def put_command(self, command: dict[str, Any]) -> None:
        command_id = str(command["commandId"])
        idempotency_key = str(command["idempotencyKey"])
        aws_call(
            "dynamodb.put_command",
            self.client.transact_write_items,
            TransactItems=[
                {
                    "Put": {
                        "TableName": self.control_table,
                        "Item": {
                            "pk": {"S": f"COMMAND#{command_id}"},
                            "sk": {"S": "STATE"},
                            "document": {"S": _json(command)},
                            "status": {"S": "QUEUED"},
                            "attempt": {"N": "0"},
                            "leaseEpoch": {"N": "0"},
                        },
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                },
                {
                    "Put": {
                        "TableName": self.control_table,
                        "Item": {
                            "pk": {"S": f"IDEMPOTENCY#{idempotency_key}"},
                            "sk": {"S": "COMMAND"},
                            "commandId": {"S": command_id},
                        },
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                },
            ],
            ClientRequestToken=idempotency_key[:36],
        )

    def claim_lease(
        self,
        command_id: str,
        owner: str,
        lease_seconds: int,
        now: datetime,
        *,
        expected_epoch: int = 0,
    ) -> dict[str, Any]:
        if not owner or lease_seconds < 1:
            raise ValueError("owner and positive lease seconds are required")
        response = aws_call(
            "dynamodb.claim_lease",
            self.client.update_item,
            TableName=self.control_table,
            Key={"pk": {"S": f"COMMAND#{command_id}"}, "sk": {"S": "STATE"}},
            UpdateExpression=(
                "SET #status = :running, leaseOwner = :owner, leaseExpiresAt = :expires, "
                "leaseEpoch = :nextEpoch ADD attempt :one"
            ),
            ConditionExpression=(
                "leaseEpoch = :expectedEpoch AND (attribute_not_exists(leaseExpiresAt) OR "
                "leaseExpiresAt < :now) AND (#status = :queued OR #status = :retry OR "
                "#status = :redrivable OR #status = :running)"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":running": {"S": "RUNNING"},
                ":queued": {"S": "QUEUED"},
                ":retry": {"S": "RETRY_WAIT"},
                ":redrivable": {"S": "FAILED_REDRIVABLE"},
                ":owner": {"S": owner},
                ":expires": {"S": _utc(now + timedelta(seconds=lease_seconds))},
                ":now": {"S": _utc(now)},
                ":expectedEpoch": {"N": str(expected_epoch)},
                ":nextEpoch": {"N": str(expected_epoch + 1)},
                ":one": {"N": "1"},
            },
            ReturnValues="ALL_NEW",
        )
        attributes = response.get("Attributes", {})
        return {
            "commandId": command_id,
            "owner": owner,
            "leaseEpoch": int(attributes.get("leaseEpoch", {"N": str(expected_epoch + 1)})["N"]),
            "leaseExpiresAt": attributes.get(
                "leaseExpiresAt", {"S": _utc(now + timedelta(seconds=lease_seconds))}
            )["S"],
        }

    def claim_stage(
        self,
        command_id: str,
        stage_id: str,
        idempotency_key: str,
        owner: str,
        lease_seconds: int,
        now: datetime,
    ) -> dict[str, Any]:
        key = {
            "pk": {"S": f"COMMAND#{command_id}"},
            "sk": {"S": f"STAGE#{stage_id}#{idempotency_key}"},
        }
        try:
            response = aws_call(
                "dynamodb.claim_stage",
                self.client.update_item,
                TableName=self.ledger_table,
                Key=key,
                UpdateExpression=(
                    "SET #status = :running, leaseOwner = :owner, leaseExpiresAt = :expires, "
                    "leaseEpoch = if_not_exists(leaseEpoch, :zero) + :one, idempotencyKey = :idem"
                ),
                ConditionExpression=(
                    "attribute_not_exists(#status) OR (#status = :running AND leaseExpiresAt < :now) "
                    "OR #status = :redrivable"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":running": {"S": "RUNNING"},
                    ":redrivable": {"S": "FAILED_REDRIVABLE"},
                    ":owner": {"S": owner},
                    ":expires": {"S": _utc(now + timedelta(seconds=lease_seconds))},
                    ":now": {"S": _utc(now)},
                    ":zero": {"N": "0"},
                    ":one": {"N": "1"},
                    ":idem": {"S": idempotency_key},
                },
                ReturnValues="ALL_NEW",
            )
            attrs = response.get("Attributes", {})
            return {"status": "RUNNING", "leaseEpoch": int(attrs.get("leaseEpoch", {"N": "1"})["N"])}
        except AwsConflictError:
            existing = self.get_stage(command_id, stage_id, idempotency_key)
            if existing and existing.get("status") == "COMPLETED":
                return existing
            raise

    def get_stage(self, command_id: str, stage_id: str, idempotency_key: str) -> dict[str, Any] | None:
        response = aws_call(
            "dynamodb.get_stage",
            self.client.get_item,
            TableName=self.ledger_table,
            Key={
                "pk": {"S": f"COMMAND#{command_id}"},
                "sk": {"S": f"STAGE#{stage_id}#{idempotency_key}"},
            },
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        result: dict[str, Any] = {"status": item["status"]["S"]}
        if "leaseEpoch" in item:
            result["leaseEpoch"] = int(item["leaseEpoch"]["N"])
        if "output" in item:
            result["output"] = json.loads(item["output"]["S"])
        return result

    def record_stage(
        self,
        *,
        command_id: str,
        stage_id: str,
        idempotency_key: str,
        lease_owner: str,
        lease_epoch: int,
        output: dict[str, Any],
        completed_at: datetime,
    ) -> dict[str, Any]:
        aws_call(
            "dynamodb.record_stage",
            self.client.put_item,
            TableName=self.ledger_table,
            Item={
                "pk": {"S": f"COMMAND#{command_id}"},
                "sk": {"S": f"STAGE#{stage_id}#{idempotency_key}"},
                "status": {"S": "COMPLETED"},
                "idempotencyKey": {"S": idempotency_key},
                "output": {"S": _json(output)},
                "outputSha256": {"S": str(output["sha256"])},
                "leaseEpoch": {"N": str(lease_epoch)},
                "completedAt": {"S": _utc(completed_at)},
            },
            ConditionExpression=(
                "(#status = :running AND leaseOwner = :owner AND leaseEpoch = :epoch) OR "
                "(#status = :completed AND outputSha256 = :outputSha256)"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":running": {"S": "RUNNING"},
                ":completed": {"S": "COMPLETED"},
                ":owner": {"S": lease_owner},
                ":epoch": {"N": str(lease_epoch)},
                ":outputSha256": {"S": str(output["sha256"])},
            },
        )
        return output

    def append_outbox(
        self,
        outbox_id: str,
        topic: str,
        correlation_id: str,
        payload: object,
        created_at: datetime,
    ) -> None:
        aws_call(
            "dynamodb.append_outbox",
            self.client.put_item,
            TableName=self.ledger_table,
            Item={
                "pk": {"S": f"OUTBOX#{outbox_id}"},
                "sk": {"S": "EVENT"},
                "topic": {"S": topic},
                "correlationId": {"S": correlation_id},
                "payload": {"S": _json(payload)},
                "status": {"S": "PENDING"},
                "createdAt": {"S": _utc(created_at)},
            },
            ConditionExpression="attribute_not_exists(pk)",
        )

    def swap_pointer(
        self,
        environment: str,
        graph_version: str,
        *,
        expected_fence: int,
        next_fence: int,
        correlation_id: str,
    ) -> int:
        try:
            response = aws_call(
                "dynamodb.swap_pointer",
                self.client.update_item,
                TableName=self.pointer_table,
                Key={"pk": {"S": f"ENV#{environment}"}, "sk": {"S": "ACTIVE"}},
                UpdateExpression=(
                    "SET graphVersion = :graphVersion, fence = :nextFence, "
                    "correlationId = :correlationId"
                ),
                ConditionExpression="attribute_not_exists(fence) OR fence = :expectedFence",
                ExpressionAttributeValues={
                    ":graphVersion": {"S": graph_version},
                    ":expectedFence": {"N": str(expected_fence)},
                    ":nextFence": {"N": str(next_fence)},
                    ":correlationId": {"S": correlation_id},
                },
                ReturnValues="ALL_NEW",
            )
        except AwsConflictError as error:
            raise AwsStaleFenceError(
                f"pointer fence for {environment} is no longer {expected_fence}"
            ) from error
        return int(response.get("Attributes", {}).get("fence", {"N": str(next_fence)})["N"])
