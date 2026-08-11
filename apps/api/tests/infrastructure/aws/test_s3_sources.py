from __future__ import annotations

import hashlib
import io
import zipfile
from typing import Any

import pytest

from lineage_api.infrastructure.aws.s3_sources import S3SourceArchiveStore


class Body:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, amount: int | None = None) -> bytes:
        if amount is None:
            return self._payload
        return self._payload[:amount]


class S3:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "Body": Body(self.payload),
            "ContentLength": len(self.payload),
            "VersionId": kwargs["VersionId"],
        }


def _archive(files: dict[str, bytes]) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return target.getvalue()


def _reference(payload: bytes) -> dict[str, object]:
    return {
        "bucket": "evidence",
        "key": "sources/payments.zip",
        "versionId": "source-v7",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "sizeBytes": len(payload),
    }


def test_s3_source_materializes_only_the_exact_checksummed_archive() -> None:
    payload = _archive({"pipeline.py": b"print('safe')\n", "src/helper.py": b"VALUE = 1\n"})
    client = S3(payload)
    store = S3SourceArchiveStore(client)

    with store.materialize(_reference(payload)) as root:
        materialized = root
        assert (root / "pipeline.py").read_text() == "print('safe')\n"
        assert (root / "src/helper.py").read_text() == "VALUE = 1\n"

    assert not materialized.exists()
    assert client.calls == [
        {
            "Bucket": "evidence",
            "Key": "sources/payments.zip",
            "VersionId": "source-v7",
            "ChecksumMode": "ENABLED",
        }
    ]


def test_s3_source_rejects_checksum_mismatch_before_extraction() -> None:
    payload = _archive({"pipeline.py": b"print('safe')\n"})
    reference = _reference(payload)
    reference["sha256"] = "0" * 64

    with pytest.raises(ValueError, match="checksum"):
        with S3SourceArchiveStore(S3(payload)).materialize(reference):
            raise AssertionError("corrupt source must not be yielded")


@pytest.mark.parametrize("name", ("../secret.py", "/absolute.py", "src\\escape.py"))
def test_s3_source_rejects_archive_path_escape(name: str) -> None:
    payload = _archive({name: b"secret"})

    with pytest.raises(ValueError, match="archive path"):
        with S3SourceArchiveStore(S3(payload)).materialize(_reference(payload)):
            raise AssertionError("unsafe source must not be yielded")


def test_s3_source_rejects_symlinks_and_unpacked_size_overflow() -> None:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        link = zipfile.ZipInfo("pipeline.py")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        archive.writestr(link, "../../secret.py")
    symlink = target.getvalue()

    with pytest.raises(ValueError, match="symlink"):
        with S3SourceArchiveStore(S3(symlink)).materialize(_reference(symlink)):
            raise AssertionError("symlink source must not be yielded")

    oversized = _archive({"pipeline.py": b"x" * 101})
    with pytest.raises(ValueError, match="unpacked size"):
        with S3SourceArchiveStore(
            S3(oversized), max_unpacked_bytes=100
        ).materialize(_reference(oversized)):
            raise AssertionError("oversized source must not be yielded")
