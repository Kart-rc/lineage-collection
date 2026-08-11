from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from lineage_api.application.models import LaneMessage
from lineage_api.infrastructure.aws.errors import aws_call


class SqsLaneBroker:
    def __init__(self, client: Any, queue_urls: dict[str, str]) -> None:
        self.client = client
        self.queue_urls = dict(queue_urls)

    def _queue(self, lane: str) -> str:
        try:
            return self.queue_urls[lane]
        except KeyError as error:
            raise ValueError(f"unknown queue lane: {lane}") from error

    def publish(
        self,
        lane: str,
        group_key: str,
        payload_ref: str,
        correlation_id: str,
        *,
        message_id: str,
        max_attempts: int = 5,
        supersession_key: str | None = None,
    ) -> str:
        queue_url = self._queue(lane)
        body = json.dumps(
            {
                "messageId": message_id,
                "lane": lane,
                "groupKey": group_key,
                "payloadRef": payload_ref,
                "correlationId": correlation_id,
                "maxAttempts": max_attempts,
                "supersessionKey": supersession_key,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        request: dict[str, Any] = {
            "QueueUrl": queue_url,
            "MessageBody": body,
            "MessageAttributes": {
                "correlationId": {"DataType": "String", "StringValue": correlation_id},
                "messageId": {"DataType": "String", "StringValue": message_id},
            },
        }
        if queue_url.endswith(".fifo"):
            request.update(MessageGroupId=group_key, MessageDeduplicationId=message_id)
        response = aws_call("sqs.publish", self.client.send_message, **request)
        return str(response["MessageId"])

    def claim(
        self, lane: str, owner: str, visibility_timeout_seconds: int = 30
    ) -> LaneMessage | None:
        response = aws_call(
            "sqs.claim",
            self.client.receive_message,
            QueueUrl=self._queue(lane),
            MaxNumberOfMessages=1,
            VisibilityTimeout=visibility_timeout_seconds,
            WaitTimeSeconds=10,
            AttributeNames=["ApproximateReceiveCount"],
            MessageAttributeNames=["All"],
        )
        messages = response.get("Messages", [])
        if not messages:
            return None
        raw = messages[0]
        body = json.loads(raw["Body"])
        attempt = int(raw.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
        return LaneMessage(
            message_id=body["messageId"],
            lane=lane,
            group_key=body["groupKey"],
            payload_ref=body["payloadRef"],
            correlation_id=body["correlationId"],
            attempt=attempt,
            max_attempts=int(body["maxAttempts"]),
            delivery_epoch=attempt,
            owner=f"{owner}|{raw['ReceiptHandle']}",
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=visibility_timeout_seconds),
            supersession_key=body.get("supersessionKey"),
        )

    def acknowledge(self, message: LaneMessage) -> None:
        aws_call(
            "sqs.acknowledge",
            self.client.delete_message,
            QueueUrl=self._queue(message.lane),
            ReceiptHandle=message.owner.split("|", 1)[1],
        )

    def retry(self, message: LaneMessage, available_at: datetime, error_code: str) -> None:
        delay = max(0, min(43_200, int((available_at - datetime.now(UTC)).total_seconds())))
        aws_call(
            "sqs.retry",
            self.client.change_message_visibility,
            QueueUrl=self._queue(message.lane),
            ReceiptHandle=message.owner.split("|", 1)[1],
            VisibilityTimeout=delay,
        )

    def redrive(self, message_id: str, available_at: datetime) -> None:
        raise ValueError(
            f"AWS redrive requires the original DLQ receipt; message {message_id} must use StartMessageMoveTask"
        )
