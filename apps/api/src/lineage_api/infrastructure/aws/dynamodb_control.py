from __future__ import annotations

import hashlib
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

    def __init__(
        self,
        client: Any,
        control_table: str,
        ledger_table: str,
        pointer_table: str,
        *,
        proposal_table: str | None = None,
    ) -> None:
        self.client = client
        self.control_table = control_table
        self.ledger_table = ledger_table
        self.pointer_table = pointer_table
        self.proposal_table = proposal_table

    def put_proposal(self, proposal: dict[str, Any]) -> dict[str, Any]:
        if self.proposal_table is None:
            raise RuntimeError("proposal table is not configured")
        proposal_id = str(proposal["proposalId"])
        version = int(proposal["version"])
        encoded = _json(proposal)
        if len(encoded.encode()) > 350_000:
            raise ValueError("proposal document exceeds its DynamoDB safety bound")
        try:
            aws_call(
                "dynamodb.put_proposal",
                self.client.put_item,
                TableName=self.proposal_table,
                Item={
                    "pk": {"S": f"PROPOSAL#{proposal_id}"},
                    "sk": {"S": f"VERSION#{version:010d}"},
                    "document": {"S": encoded},
                    "state": {"S": str(proposal["state"])},
                    "system": {"S": str(proposal["system"])},
                    "expectedBaseVersion": {
                        "S": str(proposal["expectedBaseVersion"])
                    },
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except AwsConflictError:
            existing = self.get_proposal(proposal_id, version)
            if existing == proposal:
                return existing
            raise
        return json.loads(encoded)

    def get_proposal(
        self, proposal_id: str, version: int
    ) -> dict[str, Any] | None:
        if self.proposal_table is None:
            raise RuntimeError("proposal table is not configured")
        response = aws_call(
            "dynamodb.get_proposal",
            self.client.get_item,
            TableName=self.proposal_table,
            Key={
                "pk": {"S": f"PROPOSAL#{proposal_id}"},
                "sk": {"S": f"VERSION#{version:010d}"},
            },
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        document = json.loads(item["document"]["S"])
        if not isinstance(document, dict):
            raise ValueError("stored proposal document is invalid")
        return document

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

    def active_pointer(self, environment: str) -> dict[str, Any]:
        response = aws_call(
            "dynamodb.active_pointer",
            self.client.get_item,
            TableName=self.pointer_table,
            Key={"pk": {"S": f"ENV#{environment}"}, "sk": {"S": "ACTIVE"}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return {
                "environment": environment,
                "graphVersion": "NONE",
                "fence": 0,
                "correlationId": None,
            }
        pointer: dict[str, Any] = {
            "environment": environment,
            "graphVersion": item["graphVersion"]["S"],
            "fence": int(item["fence"]["N"]),
            "correlationId": item.get("correlationId", {"NULL": True}).get("S"),
        }
        if "graphChecksum" in item:
            pointer["graphChecksum"] = item["graphChecksum"]["S"]
        if "packageReference" in item:
            pointer["package"] = json.loads(item["packageReference"]["S"])
        return pointer

    def activate_pointer(
        self,
        *,
        environment: str,
        graph_version: str,
        graph_checksum: str,
        package_reference: dict[str, Any],
        system: str,
        artifact_digest: str,
        expected_prior: str,
        expected_fence: int,
        next_fence: int,
        correlation_id: str,
        activated_at: datetime,
    ) -> dict[str, Any]:
        payload = {
            "environment": environment,
            "system": system,
            "artifactDigest": artifact_digest,
            "graphVersion": graph_version,
            "graphChecksum": graph_checksum,
            "packageRef": package_reference,
            "fence": next_fence,
        }
        identity = hashlib.sha256(_json(payload).encode()).hexdigest()
        try:
            aws_call(
                "dynamodb.activate_pointer",
                self.client.transact_write_items,
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self.pointer_table,
                            "Key": {
                                "pk": {"S": f"ENV#{environment}"},
                                "sk": {"S": "ACTIVE"},
                            },
                            "UpdateExpression": (
                                "SET graphVersion = :graphVersion, graphChecksum = :graphChecksum, "
                                "packageReference = :packageReference, fence = :nextFence, "
                                "correlationId = :correlationId, activatedAt = :activatedAt"
                            ),
                            "ConditionExpression": (
                                "attribute_not_exists(fence) OR "
                                "(fence = :expectedFence AND graphVersion = :expectedPrior)"
                            ),
                            "ExpressionAttributeValues": {
                                ":graphVersion": {"S": graph_version},
                                ":graphChecksum": {"S": graph_checksum},
                                ":packageReference": {"S": _json(package_reference)},
                                ":expectedPrior": {"S": expected_prior},
                                ":expectedFence": {"N": str(expected_fence)},
                                ":nextFence": {"N": str(next_fence)},
                                ":correlationId": {"S": correlation_id},
                                ":activatedAt": {"S": _utc(activated_at)},
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.ledger_table,
                            "Item": {
                                "pk": {"S": f"OUTBOX#publication-{identity}"},
                                "sk": {"S": "EVENT"},
                                "topic": {"S": "PUBLICATION_ACTIVATED"},
                                "correlationId": {"S": correlation_id},
                                "payload": {"S": _json(payload)},
                                "status": {"S": "PENDING"},
                                "createdAt": {"S": _utc(activated_at)},
                            },
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.ledger_table,
                            "Item": {
                                "pk": {"S": f"PACKAGE#{system}#{environment}"},
                                "sk": {"S": f"ARTIFACT#{artifact_digest}"},
                                "packageReference": {"S": _json(package_reference)},
                                "packageId": {
                                    "S": str(package_reference["key"])
                                    .rsplit("/", 1)[-1]
                                    .removesuffix(".json")
                                },
                                "graphVersion": {"S": graph_version},
                                "graphChecksum": {"S": graph_checksum},
                                "registeredAt": {"S": _utc(activated_at)},
                            },
                            "ConditionExpression": (
                                "attribute_not_exists(pk) OR "
                                "(packageReference = :packageReference AND "
                                "graphVersion = :graphVersion AND "
                                "graphChecksum = :graphChecksum)"
                            ),
                            "ExpressionAttributeValues": {
                                ":packageReference": {"S": _json(package_reference)},
                                ":graphVersion": {"S": graph_version},
                                ":graphChecksum": {"S": graph_checksum},
                            },
                        }
                    },
                ],
                ClientRequestToken=identity[:36],
            )
        except AwsConflictError as error:
            existing = self.active_pointer(environment)
            if (
                existing.get("graphVersion") == graph_version
                and existing.get("graphChecksum") == graph_checksum
                and existing.get("package") == package_reference
                and existing.get("fence") == next_fence
                and existing.get("correlationId") == correlation_id
            ):
                return existing
            raise AwsStaleFenceError(
                f"pointer activation for {environment} lost fence {expected_fence}"
            ) from error
        return {
            "environment": environment,
            "graphVersion": graph_version,
            "graphChecksum": graph_checksum,
            "package": json.loads(_json(package_reference)),
            "fence": next_fence,
            "correlationId": correlation_id,
        }

    def package_for(
        self, system: str, environment: str, artifact_digest: str
    ) -> dict[str, Any] | None:
        response = aws_call(
            "dynamodb.package_for",
            self.client.get_item,
            TableName=self.ledger_table,
            Key={
                "pk": {"S": f"PACKAGE#{system}#{environment}"},
                "sk": {"S": f"ARTIFACT#{artifact_digest}"},
            },
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        return {
            "packageReference": json.loads(item["packageReference"]["S"]),
            "packageId": item["packageId"]["S"],
            "graphVersion": item["graphVersion"]["S"],
            "graphChecksum": item["graphChecksum"]["S"],
        }

    def claim_deployment_event(
        self, event: dict[str, Any], event_digest: str
    ) -> dict[str, Any]:
        event_id = str(event["eventId"])
        try:
            aws_call(
                "dynamodb.claim_deployment_event",
                self.client.put_item,
                TableName=self.control_table,
                Item={
                    "pk": {"S": f"DEPLOYMENT_EVENT#{event_id}"},
                    "sk": {"S": "EVENT"},
                    "eventDigest": {"S": event_digest},
                    "document": {"S": _json(event)},
                    "correlationId": {"S": str(event["correlationId"])},
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except AwsConflictError:
            response = aws_call(
                "dynamodb.get_deployment_event",
                self.client.get_item,
                TableName=self.control_table,
                Key={
                    "pk": {"S": f"DEPLOYMENT_EVENT#{event_id}"},
                    "sk": {"S": "EVENT"},
                },
                ConsistentRead=True,
            )
            item = response.get("Item")
            if item and item.get("eventDigest", {}).get("S") == event_digest:
                return {"disposition": "DUPLICATE"}
            raise
        return {"disposition": "CLAIMED"}

    def establish_deployment_order(self, event: dict[str, Any]) -> dict[str, Any]:
        key = self._deployment_state_key(
            str(event["system"]), str(event["environment"])
        )
        try:
            aws_call(
                "dynamodb.establish_deployment_order",
                self.client.update_item,
                TableName=self.control_table,
                Key=key,
                UpdateExpression=(
                    "SET provider = :provider, providerSequence = :providerSequence, "
                    "attempt = :attempt, eventId = :eventId, correlationId = :correlationId, "
                    "#status = :authoritative"
                ),
                ConditionExpression=(
                    "attribute_not_exists(providerSequence) OR eventId = :eventId OR "
                    "(provider = :provider AND "
                    "(providerSequence < :providerSequence OR "
                    "(providerSequence = :providerSequence AND attempt < :attempt)))"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":provider": {"S": str(event["provider"])},
                    ":providerSequence": {"N": str(event["providerSequence"])},
                    ":attempt": {"N": str(event["attempt"])},
                    ":eventId": {"S": str(event["eventId"])},
                    ":correlationId": {"S": str(event["correlationId"])},
                    ":authoritative": {"S": "AUTHORITATIVE"},
                },
            )
        except AwsConflictError:
            current = self.deployment_state(
                str(event["system"]), str(event["environment"])
            )
            if current and current.get("eventId") == event["eventId"]:
                return {"disposition": "AUTHORITATIVE"}
            return {"disposition": "STALE"}
        return {"disposition": "AUTHORITATIVE"}

    def record_deployed_digest(self, event: dict[str, Any]) -> None:
        artifact = event.get("artifactDigest")
        aws_call(
            "dynamodb.record_deployed_digest",
            self.client.update_item,
            TableName=self.control_table,
            Key=self._deployment_state_key(
                str(event["system"]), str(event["environment"])
            ),
            UpdateExpression=(
                "SET deployedArtifactDigest = :artifactDigest, outcome = :outcome, "
                "auditRef = :auditRef, occurredAt = :occurredAt, #status = :recorded"
            ),
            ConditionExpression=(
                "eventId = :eventId AND correlationId = :correlationId AND "
                "(#status = :recorded OR result = :result)"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":artifactDigest": (
                    {"S": str(artifact)} if artifact is not None else {"NULL": True}
                ),
                ":outcome": {"S": str(event["outcome"])},
                ":auditRef": {"S": str(event["auditRef"])},
                ":occurredAt": {"S": str(event["occurredAt"])},
                ":recorded": {"S": "RECORDED"},
                ":eventId": {"S": str(event["eventId"])},
                ":correlationId": {"S": str(event["correlationId"])},
            },
        )

    def promote_deployment(
        self,
        *,
        environment: str,
        graph_version: str,
        graph_checksum: str,
        package_reference: dict[str, Any],
        system: str,
        artifact_digest: str,
        expected_prior: str,
        expected_fence: int,
        next_fence: int,
        correlation_id: str,
        activated_at: datetime,
        action: str,
    ) -> dict[str, Any]:
        if action not in {"DEPLOYMENT_PROMOTED", "DEPLOYMENT_ROLLED_BACK"}:
            raise ValueError("deployment promotion action is invalid")
        payload = {
            "environment": environment,
            "system": system,
            "artifactDigest": artifact_digest,
            "graphVersion": graph_version,
            "graphChecksum": graph_checksum,
            "packageRef": package_reference,
            "fence": next_fence,
            "action": action,
        }
        identity = hashlib.sha256(_json(payload).encode()).hexdigest()
        try:
            aws_call(
                "dynamodb.promote_deployment",
                self.client.transact_write_items,
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self.pointer_table,
                            "Key": {
                                "pk": {"S": f"ENV#{environment}"},
                                "sk": {"S": "ACTIVE"},
                            },
                            "UpdateExpression": (
                                "SET graphVersion = :graphVersion, graphChecksum = :graphChecksum, "
                                "packageReference = :packageReference, fence = :nextFence, "
                                "correlationId = :correlationId, activatedAt = :activatedAt"
                            ),
                            "ConditionExpression": (
                                "fence = :expectedFence AND graphVersion = :expectedPrior"
                            ),
                            "ExpressionAttributeValues": {
                                ":graphVersion": {"S": graph_version},
                                ":graphChecksum": {"S": graph_checksum},
                                ":packageReference": {"S": _json(package_reference)},
                                ":nextFence": {"N": str(next_fence)},
                                ":correlationId": {"S": correlation_id},
                                ":activatedAt": {"S": _utc(activated_at)},
                                ":expectedFence": {"N": str(expected_fence)},
                                ":expectedPrior": {"S": expected_prior},
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.ledger_table,
                            "Item": {
                                "pk": {"S": f"OUTBOX#deployment-{identity}"},
                                "sk": {"S": "EVENT"},
                                "topic": {"S": action},
                                "correlationId": {"S": correlation_id},
                                "payload": {"S": _json(payload)},
                                "status": {"S": "PENDING"},
                                "createdAt": {"S": _utc(activated_at)},
                            },
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    {
                        "ConditionCheck": {
                            "TableName": self.control_table,
                            "Key": self._deployment_state_key(system, environment),
                            "ConditionExpression": (
                                "correlationId = :correlationId AND #status = :recorded"
                            ),
                            "ExpressionAttributeNames": {"#status": "status"},
                            "ExpressionAttributeValues": {
                                ":correlationId": {"S": correlation_id},
                                ":recorded": {"S": "RECORDED"},
                            },
                        }
                    },
                ],
                ClientRequestToken=identity[:36],
            )
        except AwsConflictError as error:
            existing = self.active_pointer(environment)
            if (
                existing.get("graphVersion") == graph_version
                and existing.get("graphChecksum") == graph_checksum
                and existing.get("package") == package_reference
                and existing.get("fence") == next_fence
                and existing.get("correlationId") == correlation_id
            ):
                return existing
            raise AwsStaleFenceError(
                f"deployment promotion for {environment} lost fence {expected_fence}"
            ) from error
        return {
            "environment": environment,
            "graphVersion": graph_version,
            "graphChecksum": graph_checksum,
            "package": json.loads(_json(package_reference)),
            "fence": next_fence,
            "correlationId": correlation_id,
        }

    def complete_deployment(
        self, event: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        persisted = {**result, "eventId": str(event["eventId"])}
        if len(_json(persisted).encode()) > 350_000:
            raise ValueError("deployment result exceeds its DynamoDB safety bound")
        aws_call(
            "dynamodb.complete_deployment",
            self.client.update_item,
            TableName=self.control_table,
            Key=self._deployment_state_key(
                str(event["system"]), str(event["environment"])
            ),
            UpdateExpression="SET #status = :completed, result = :result",
            ConditionExpression="eventId = :eventId AND correlationId = :correlationId",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":completed": {"S": str(result["terminalOutcome"])},
                ":recorded": {"S": "RECORDED"},
                ":result": {"S": _json(persisted)},
                ":eventId": {"S": str(event["eventId"])},
                ":correlationId": {"S": str(event["correlationId"])},
            },
        )
        return json.loads(_json(persisted))

    def deployment_state(
        self, system: str, environment: str
    ) -> dict[str, Any] | None:
        response = aws_call(
            "dynamodb.deployment_state",
            self.client.get_item,
            TableName=self.control_table,
            Key=self._deployment_state_key(system, environment),
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        if "result" in item:
            result = json.loads(item["result"]["S"])
            if isinstance(result, dict):
                return result
            raise ValueError("stored deployment result is invalid")
        state: dict[str, Any] = {"eventId": item["eventId"]["S"]}
        if "correlationId" in item:
            state["correlationId"] = item["correlationId"]["S"]
        if "status" in item:
            state["status"] = item["status"]["S"]
        return state

    @staticmethod
    def _deployment_state_key(system: str, environment: str) -> dict[str, dict[str, str]]:
        return {
            "pk": {"S": f"DEPLOYMENT#{system}#{environment}"},
            "sk": {"S": "STATE"},
        }

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
