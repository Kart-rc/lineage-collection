from __future__ import annotations

import json
from typing import Any

from lineage_api.infrastructure.aws.errors import aws_call


class KinesisRuntimeAdapter:
    def __init__(self, client: Any, stream_name: str) -> None:
        self.client = client
        self.stream_name = stream_name

    def put_observation(self, partition_key: str, observation: object) -> dict[str, str]:
        encoded = json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()
        response = aws_call(
            "kinesis.put_observation",
            self.client.put_record,
            StreamName=self.stream_name,
            PartitionKey=partition_key,
            Data=encoded,
        )
        return {
            "sequenceNumber": str(response["SequenceNumber"]),
            "shardId": str(response["ShardId"]),
        }

    def records(self, shard_iterator: str, limit: int = 100) -> tuple[list[object], str | None]:
        response = aws_call(
            "kinesis.get_records",
            self.client.get_records,
            ShardIterator=shard_iterator,
            Limit=max(1, min(limit, 1_000)),
        )
        records = [json.loads(record["Data"]) for record in response.get("Records", [])]
        return records, response.get("NextShardIterator")

