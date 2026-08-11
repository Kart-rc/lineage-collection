from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from lineage_api.infrastructure.aws.errors import AwsConflictError, AwsMissingObjectError, aws_call


def _canonical_bytes(body: object) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class S3ArtifactStore:
    def __init__(self, client: Any, default_bucket: str) -> None:
        self.client = client
        self.default_bucket = default_bucket

    def put(self, kind: str, key: str, body: object, schema_version: str) -> dict[str, Any]:
        encoded = _canonical_bytes(body)
        digest = hashlib.sha256(encoded).hexdigest()
        try:
            response = aws_call(
                "s3.put_immutable",
                self.client.put_object,
                Bucket=self.default_bucket,
                Key=key,
                Body=encoded,
                ContentType="application/json",
                ChecksumAlgorithm="SHA256",
                ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode(),
                IfNoneMatch="*",
                Metadata={"kind": kind, "schema-version": schema_version, "sha256": digest},
            )
        except AwsConflictError:
            existing = self._head(key)
            if existing.get("Metadata", {}).get("sha256") != digest:
                raise
            response = existing
        version_id = response.get("VersionId")
        if not isinstance(version_id, str) or not version_id:
            raise RuntimeError("S3 immutable write did not return VersionId")
        return {
            "bucket": self.default_bucket,
            "key": key,
            "versionId": version_id,
            "sha256": digest,
            "sizeBytes": len(encoded),
        }

    def get(self, reference: object) -> object:
        if not isinstance(reference, dict):
            raise ValueError("artifact reference must be an object")
        response = aws_call(
            "s3.get_version",
            self.client.get_object,
            Bucket=str(reference["bucket"]),
            Key=str(reference["key"]),
            VersionId=str(reference["versionId"]),
            ChecksumMode="ENABLED",
        )
        encoded = response["Body"].read()
        digest = hashlib.sha256(encoded).hexdigest()
        if digest != reference["sha256"] or len(encoded) != reference["sizeBytes"]:
            raise AwsMissingObjectError("versioned S3 object failed checksum/size verification")
        return json.loads(encoded)

    def _head(self, key: str) -> dict[str, Any]:
        return aws_call(
            "s3.head_immutable",
            self.client.head_object,
            Bucket=self.default_bucket,
            Key=key,
        )

