from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from lineage_api.infrastructure.aws.errors import aws_call


_COMMAND = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_MAP_TOKEN = re.compile(r"^[A-Za-z0-9-]{1,80}$")
_SUCCESS = re.compile(r"^SUCCEEDED_[0-9]+\.json$")


class S3MapResultReader:
    def __init__(
        self,
        client: Any,
        evidence_bucket: str,
        *,
        max_shards: int = 100,
        max_object_bytes: int = 96 * 1024 * 1024,
    ) -> None:
        if not evidence_bucket:
            raise ValueError("evidence bucket is required")
        if not 1 <= max_shards <= 100:
            raise ValueError("map result shard limit is invalid")
        if not 1 <= max_object_bytes <= 128 * 1024 * 1024:
            raise ValueError("map result object size limit is invalid")
        self.client = client
        self.evidence_bucket = evidence_bucket
        self.max_shards = max_shards
        self.max_object_bytes = max_object_bytes

    def read_succeeded(
        self, bucket: str, manifest_key: str, map_run_arn: str, command_id: str
    ) -> dict[str, Any]:
        if bucket != self.evidence_bucket:
            raise ValueError("map result must use the configured evidence bucket")
        if _COMMAND.fullmatch(command_id) is None:
            raise ValueError("map result command ID is invalid")
        if not isinstance(map_run_arn, str) or ":mapRun:" not in map_run_arn:
            raise ValueError("map run ARN is invalid")
        map_token = map_run_arn.rsplit(":", 1)[-1]
        if _MAP_TOKEN.fullmatch(map_token) is None:
            raise ValueError("map run identity is invalid")
        prefix = f"workflow-results/{command_id}/B5/{map_token}/"
        if manifest_key != f"{prefix}manifest.json":
            raise ValueError("map result manifest is outside its exact run prefix")
        listed = aws_call(
            "s3.list_map_results",
            self.client.list_objects_v2,
            Bucket=bucket,
            Prefix=prefix,
            MaxKeys=self.max_shards + 1,
        )
        if listed.get("IsTruncated") is True:
            raise ValueError("map result export exceeds its shard limit")
        contents = listed.get("Contents", [])
        if not isinstance(contents, list):
            raise ValueError("map result listing is invalid")
        keys: list[str] = []
        for item in contents:
            if not isinstance(item, Mapping) or not isinstance(item.get("Key"), str):
                raise ValueError("map result listing contains an invalid object")
            key = item["Key"]
            if not key.startswith(prefix):
                raise ValueError("map result listing escaped its exact prefix")
            keys.append(key)
        if len(keys) != len(set(keys)) or manifest_key not in keys:
            raise ValueError("map result listing is incomplete or duplicated")
        names = [key.removeprefix(prefix) for key in keys]
        if any(name.startswith(("FAILED_", "PENDING_")) for name in names):
            raise ValueError("map result export contains failed or pending children")
        unexpected = [
            name
            for name in names
            if name != "manifest.json" and _SUCCESS.fullmatch(name) is None
        ]
        if unexpected:
            raise ValueError("map result export contains an unexpected object")
        shard_keys = sorted(
            key for key in keys if _SUCCESS.fullmatch(key.removeprefix(prefix))
        )
        if len(shard_keys) > self.max_shards:
            raise ValueError("map result export exceeds its shard limit")
        manifest_ref, manifest = self._read_json(bucket, manifest_key)
        if not isinstance(manifest, Mapping):
            raise ValueError("map result manifest must be a JSON object")
        shard_refs: list[dict[str, Any]] = []
        children: list[object] = []
        for key in shard_keys:
            reference, body = self._read_json(bucket, key)
            if not isinstance(body, list):
                raise ValueError("map result shard must be a JSON array")
            shard_refs.append(reference)
            children.extend(body)
            if len(children) > 10_000:
                raise ValueError("map result export exceeds its child limit")
        return {
            "manifestRef": manifest_ref,
            "shardRefs": shard_refs,
            "childResults": children,
        }

    def _read_json(self, bucket: str, key: str) -> tuple[dict[str, Any], object]:
        response = aws_call(
            "s3.read_map_result",
            self.client.get_object,
            Bucket=bucket,
            Key=key,
        )
        length = response.get("ContentLength")
        version_id = response.get("VersionId")
        if (
            not isinstance(length, int)
            or isinstance(length, bool)
            or not 0 <= length <= self.max_object_bytes
            or not isinstance(version_id, str)
            or not version_id
        ):
            raise ValueError("map result object metadata is invalid")
        stream = response.get("Body")
        if stream is None or not hasattr(stream, "read"):
            raise ValueError("map result object body is invalid")
        try:
            encoded = stream.read(self.max_object_bytes + 1)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if not isinstance(encoded, bytes) or len(encoded) != length:
            raise ValueError("map result object length does not match its metadata")
        try:
            body = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("map result object is not valid JSON") from error
        return (
            {
                "bucket": bucket,
                "key": key,
                "versionId": version_id,
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "sizeBytes": length,
            },
            body,
        )


__all__ = ["S3MapResultReader"]
