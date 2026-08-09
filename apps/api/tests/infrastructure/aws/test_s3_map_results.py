from __future__ import annotations

import io
import json
from typing import Any

import pytest

from lineage_api.infrastructure.aws.s3_map_results import S3MapResultReader


class Client:
    def __init__(self, *, include_failure: bool = False) -> None:
        self.include_failure = include_failure
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list", kwargs))
        keys = [
            "workflow-results/cmd-baseline/B5/map-1/manifest.json",
            "workflow-results/cmd-baseline/B5/map-1/SUCCEEDED_0.json",
        ]
        if self.include_failure:
            keys.append("workflow-results/cmd-baseline/B5/map-1/FAILED_0.json")
        return {
            "IsTruncated": False,
            "Contents": [{"Key": key, "Size": 512} for key in keys],
        }

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get", kwargs))
        key = kwargs["Key"]
        if key.endswith("manifest.json"):
            body: object = {"MapRunArn": "fixture-map-run"}
            version = "manifest-v1"
        else:
            body = [
                {
                    "outcome": "SUCCEEDED",
                    "output": {
                        "bucket": "evidence",
                        "key": "commands/cmd-baseline/stages/B5/idem-0.json",
                        "versionId": "result-v1",
                        "sha256": "a" * 64,
                        "sizeBytes": 1024,
                    },
                }
            ]
            version = "shard-v1"
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        return {
            "Body": io.BytesIO(encoded),
            "ContentLength": len(encoded),
            "VersionId": version,
        }


def test_s3_map_result_reader_pins_exact_unique_export_objects() -> None:
    client = Client()
    reader = S3MapResultReader(client, "evidence")
    manifest = "workflow-results/cmd-baseline/B5/map-1/manifest.json"

    result = reader.read_succeeded(
        "evidence",
        manifest,
        "arn:aws:states:us-east-1:111111111111:mapRun:baseline/Map:map-1",
        "cmd-baseline",
    )

    assert result["manifestRef"]["versionId"] == "manifest-v1"
    assert result["shardRefs"][0]["versionId"] == "shard-v1"
    assert result["childResults"][0]["outcome"] == "SUCCEEDED"
    assert client.calls[0] == (
        "list",
        {
            "Bucket": "evidence",
            "Prefix": "workflow-results/cmd-baseline/B5/map-1/",
            "MaxKeys": 101,
        },
    )


def test_s3_map_result_reader_rejects_failed_children_and_cross_bucket_reads() -> None:
    with pytest.raises(ValueError, match="configured evidence bucket"):
        S3MapResultReader(Client(), "evidence").read_succeeded(
            "another-bucket",
            "workflow-results/cmd-baseline/B5/map-1/manifest.json",
            "arn:aws:states:us-east-1:111111111111:mapRun:baseline/Map:map-1",
            "cmd-baseline",
        )
    with pytest.raises(ValueError, match="failed or pending"):
        S3MapResultReader(Client(include_failure=True), "evidence").read_succeeded(
            "evidence",
            "workflow-results/cmd-baseline/B5/map-1/manifest.json",
            "arn:aws:states:us-east-1:111111111111:mapRun:baseline/Map:map-1",
            "cmd-baseline",
        )
