from __future__ import annotations

import hashlib
import io
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from lineage_api.application.stage_execution import validate_artifact_reference
from lineage_api.infrastructure.aws.errors import aws_call


class S3SourceArchiveStore:
    """Materialize an exact immutable source ZIP inside bounded temporary storage."""

    def __init__(
        self,
        client: Any,
        *,
        max_archive_bytes: int = 256 * 1024 * 1024,
        max_unpacked_bytes: int = 1024 * 1024 * 1024,
        max_entries: int = 20_000,
        max_compression_ratio: int = 1_000,
    ) -> None:
        if min(
            max_archive_bytes,
            max_unpacked_bytes,
            max_entries,
            max_compression_ratio,
        ) < 1:
            raise ValueError("source archive bounds must be positive")
        self.client = client
        self.max_archive_bytes = max_archive_bytes
        self.max_unpacked_bytes = max_unpacked_bytes
        self.max_entries = max_entries
        self.max_compression_ratio = max_compression_ratio

    @contextmanager
    def materialize(self, reference: object) -> Iterator[Path]:
        exact = validate_artifact_reference(reference)
        expected_size = int(exact["sizeBytes"])
        if expected_size > self.max_archive_bytes:
            raise ValueError("source archive exceeds its compressed size bound")
        response = aws_call(
            "s3.get_source_version",
            self.client.get_object,
            Bucket=exact["bucket"],
            Key=exact["key"],
            VersionId=exact["versionId"],
            ChecksumMode="ENABLED",
        )
        body = response["Body"]
        try:
            payload = body.read(self.max_archive_bytes + 1)
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        if not isinstance(payload, bytes):
            raise ValueError("source archive body must be bytes")
        if len(payload) != expected_size:
            raise ValueError("source archive size verification failed")
        if hashlib.sha256(payload).hexdigest() != exact["sha256"]:
            raise ValueError("source archive checksum verification failed")

        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile as error:
            raise ValueError("source archive is not a valid ZIP") from error
        with archive:
            members = self._validated_members(archive)
            with tempfile.TemporaryDirectory(prefix="lineage-sca-") as temporary:
                root = Path(temporary) / "repository"
                root.mkdir(mode=0o700)
                self._extract(archive, members, root)
                yield root

    def _validated_members(
        self, archive: zipfile.ZipFile
    ) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
        members = archive.infolist()
        if len(members) > self.max_entries:
            raise ValueError("source archive exceeds its entry bound")
        total_size = 0
        normalized_names: set[str] = set()
        validated: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        for member in members:
            path = self._member_path(member.filename)
            canonical = path.as_posix()
            collision_key = canonical.casefold()
            if collision_key in normalized_names:
                raise ValueError("source archive contains duplicate paths")
            normalized_names.add(collision_key)
            if member.flag_bits & 0x1:
                raise ValueError("encrypted source archive entries are unsupported")
            mode = member.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if file_type == stat.S_IFLNK:
                raise ValueError("source archive symlinks are forbidden")
            if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError("source archive contains a special file")
            total_size += member.file_size
            if total_size > self.max_unpacked_bytes:
                raise ValueError("source archive exceeds its unpacked size bound")
            compression_ratio_exceeded = member.file_size > 0 and (
                member.compress_size == 0
                or member.file_size
                > member.compress_size * self.max_compression_ratio
            )
            if compression_ratio_exceeded:
                raise ValueError("source archive exceeds its compression-ratio bound")
            validated.append((member, path))
        return validated

    @staticmethod
    def _member_path(name: str) -> PurePosixPath:
        path = PurePosixPath(name.rstrip("/"))
        if (
            not name
            or "\x00" in name
            or name.startswith(("/", "\\"))
            or "\\" in name
            or not path.parts
            or path.as_posix() in ("", ".")
            or ".." in path.parts
            or path.as_posix() != name.rstrip("/")
        ):
            raise ValueError("source archive path is unsafe")
        return path

    @staticmethod
    def _extract(
        archive: zipfile.ZipFile,
        members: list[tuple[zipfile.ZipInfo, PurePosixPath]],
        root: Path,
    ) -> None:
        for member, path in members:
            destination = root.joinpath(*path.parts)
            if member.is_dir():
                destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with archive.open(member, "r") as source, destination.open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            destination.chmod(0o400)


__all__ = ["S3SourceArchiveStore"]
