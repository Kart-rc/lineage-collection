from __future__ import annotations

import hashlib
import os
import selectors
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from lineage_api.application.repository_sources import (
    RepositoryCheckoutDescriptor,
    RepositorySnapshot,
    RepositorySourceError,
    RepositorySourceLimits,
    canonicalize_https_origin,
    validate_relative_tracked_path,
)


_GIT_OPTIONS = (
    "--no-optional-locks",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.pager=cat",
    "-c",
    "diff.external=",
    "-c",
    "protocol.file.allow=never",
)
_SUPPORTED_PLATFORMS = {"darwin", "linux"}
_MAX_TRACKED_PATH_BYTES = 512
_MAX_INDEX_RECORD_OVERHEAD = 128
_MAX_ORIGIN_BYTES = 4096
_MAX_METADATA_BYTES = 8192
_MAX_GIT_STDERR_BYTES = 16 * 1024
_GIT_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True, slots=True)
class _ProcessOutput:
    stdout: bytes
    stderr: bytes


class _BoundedProcessRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        stdout_limit: int,
        stderr_limit: int,
        timeout_seconds: float,
    ) -> _ProcessOutput:
        if stdout_limit < 0 or stderr_limit < 0 or timeout_seconds <= 0:
            raise ValueError("process bounds must be positive")

        process: subprocess.Popen[bytes] | None = None
        selector = selectors.DefaultSelector()
        stdout = bytearray()
        stderr = bytearray()
        buffers = {"stdout": stdout, "stderr": stderr}
        limits = {"stdout": stdout_limit, "stderr": stderr_limit}
        try:
            process = subprocess.Popen(
                list(argv),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=dict(env),
                close_fds=True,
            )
            if process.stdout is None or process.stderr is None:
                raise RepositorySourceError("local Git pipes were not created")
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            deadline = time.monotonic() + timeout_seconds

            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RepositorySourceError("local Git process timed out")
                events = selector.select(remaining)
                if not events:
                    raise RepositorySourceError("local Git process timed out")
                for key, _mask in events:
                    stream_name = str(key.data)
                    buffer = buffers[stream_name]
                    limit = limits[stream_name]
                    chunk = os.read(key.fd, min(64 * 1024, limit - len(buffer) + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    buffer.extend(chunk)
                    if len(buffer) > limit:
                        raise RepositorySourceError(
                            f"local Git {stream_name} byte limit exceeded"
                        )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RepositorySourceError("local Git process timed out")
            try:
                return_code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as error:
                raise RepositorySourceError("local Git process timed out") from error
            if return_code != 0:
                raise RepositorySourceError("local Git validation failed")
            return _ProcessOutput(stdout=bytes(stdout), stderr=bytes(stderr))
        except RepositorySourceError:
            if process is not None:
                _terminate_process(process)
            raise
        except (OSError, subprocess.SubprocessError) as error:
            if process is not None:
                _terminate_process(process)
            raise RepositorySourceError("local Git validation failed") from error
        finally:
            selector.close()
            if process is not None:
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()


@dataclass(frozen=True, slots=True)
class _ExecutableIdentity:
    path: Path
    device: int
    inode: int
    mode: int
    owner: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class _FileRecord:
    path: str
    mode: str
    size: int
    digest: str


@dataclass(frozen=True, slots=True)
class _BoundSnapshotReader:
    root: Path
    max_file_bytes: int
    records: tuple[_FileRecord, ...]

    def __call__(self, relative_path: str) -> bytes:
        record = next((item for item in self.records if item.path == relative_path), None)
        if record is None:
            raise RepositorySourceError("source path is outside the explicit tracked scope")
        content = _read_regular_file(
            self.root,
            relative_path,
            self.max_file_bytes,
            expected_mode=record.mode,
        )
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != record.size or digest != record.digest:
            raise RepositorySourceError("tracked file changed after snapshot creation")
        return content


class LocalGitRepositorySource:
    """Builds an immutable, resource-bounded view of a clean local Git checkout."""

    def __init__(self, limits: RepositorySourceLimits) -> None:
        _require_supported_posix_platform()
        self._limits = limits
        self._git_executable = _trusted_system_git()
        self._runner = _BoundedProcessRunner()

    def snapshot(self, descriptor: RepositoryCheckoutDescriptor) -> RepositorySnapshot:
        root = self._validated_root(descriptor.checkout_root)
        self._validate_git_state(root, descriptor)
        paths = self._tracked_paths(root)
        entries = self._tracked_entries(root)
        self._validate_committed_index(root, paths, entries)
        records: list[_FileRecord] = []
        total_bytes = 0
        scope = hashlib.sha256(b"repository-scope-v1\0")
        for relative_path in paths:
            mode, object_id = entries[relative_path]
            if mode == "120000":
                raise RepositorySourceError("tracked symlinks are forbidden")
            if mode == "160000":
                raise RepositorySourceError("tracked submodules are forbidden")
            if mode not in {"100644", "100755"}:
                raise RepositorySourceError("tracked path is not a regular file")

            content = _read_regular_file(
                root,
                relative_path,
                self._limits.max_file_bytes,
                expected_mode=mode,
            )
            if _git_blob_id(content, len(descriptor.revision)) != object_id:
                raise RepositorySourceError("tracked content is not clean")
            total_bytes += len(content)
            if total_bytes > self._limits.max_total_bytes:
                raise RepositorySourceError("repository exceeds the total byte limit")
            content_digest = hashlib.sha256(content).hexdigest()
            records.append(_FileRecord(relative_path, mode, len(content), content_digest))
            encoded_path = relative_path.encode("utf-8")
            scope.update(len(encoded_path).to_bytes(8, "big"))
            scope.update(encoded_path)
            scope.update(len(content).to_bytes(8, "big"))
            scope.update(content)

        self._validate_git_state(root, descriptor)
        self._validate_committed_index(root, paths, self._tracked_entries(root))
        reader = _BoundSnapshotReader(root, self._limits.max_file_bytes, tuple(records))
        return RepositorySnapshot(
            descriptor=descriptor,
            paths=paths,
            scope_digest=f"sha256:{scope.hexdigest()}",
            _reader=reader,
        )

    @staticmethod
    def _validated_root(checkout_root: Path) -> Path:
        requested = Path(checkout_root)
        try:
            metadata = requested.lstat()
            root = requested.resolve(strict=True)
        except OSError as error:
            raise RepositorySourceError("checkout root is not an accessible directory") from error
        if not stat.S_ISDIR(metadata.st_mode) or requested != root:
            raise RepositorySourceError("checkout root must be a canonical, non-symlink directory")
        return root

    def _validate_git_state(
        self, root: Path, descriptor: RepositoryCheckoutDescriptor
    ) -> None:
        if self._git_scalar(
            root,
            "rev-parse",
            "--is-inside-work-tree",
            stdout_limit=16,
        ) != "true":
            raise RepositorySourceError("checkout root is not a Git work tree")
        top_level = self._git_scalar(
            root,
            "rev-parse",
            "--show-toplevel",
            stdout_limit=_MAX_METADATA_BYTES,
        )
        try:
            resolved_top_level = Path(top_level).resolve(strict=True)
        except OSError as error:
            raise RepositorySourceError("Git work tree root is not accessible") from error
        if resolved_top_level != root:
            raise RepositorySourceError("checkout root must be the Git work tree root")

        head = self._git_scalar(
            root,
            "rev-parse",
            "--verify",
            "HEAD^{commit}",
            stdout_limit=128,
        )
        if head != descriptor.revision:
            raise RepositorySourceError("checkout HEAD does not match the exact revision")

        raw_origins = _nul_records(
            self._git(
                root,
                "config",
                "--null",
                "--get-all",
                "remote.origin.url",
                stdout_limit=2 * (_MAX_ORIGIN_BYTES + 1),
            ),
            max_records=1,
            max_record_bytes=_MAX_ORIGIN_BYTES,
            count_error="checkout must define exactly one raw origin URL",
            record_error="raw origin URL exceeds the byte limit",
        )
        if len(raw_origins) != 1:
            raise RepositorySourceError("checkout must define exactly one raw origin URL")
        self._validate_origin(raw_origins[0], descriptor.origin, "raw origin")

        effective_origin = _single_line_record(
            self._git(
                root,
                "remote",
                "get-url",
                "--all",
                "origin",
                stdout_limit=2 * (_MAX_ORIGIN_BYTES + 1),
            ),
            max_record_bytes=_MAX_ORIGIN_BYTES,
            count_error="checkout must resolve exactly one effective origin URL",
        )
        self._validate_origin(effective_origin, descriptor.origin, "effective origin")

    @staticmethod
    def _validate_origin(raw_origin: bytes, expected: str, label: str) -> None:
        try:
            origin = raw_origin.decode("utf-8", errors="strict")
            canonical = canonicalize_https_origin(origin)
        except (UnicodeError, ValueError):
            raise RepositorySourceError(
                f"{label} is not a credential-free canonical HTTPS origin"
            ) from None
        if canonical != expected:
            raise RepositorySourceError(f"{label} does not match the requested origin")

    def _tracked_paths(self, root: Path) -> tuple[str, ...]:
        output = self._git(
            root,
            "ls-files",
            "-z",
            stdout_limit=_nul_output_limit(
                self._limits.max_files,
                _MAX_TRACKED_PATH_BYTES,
            ),
        )
        records = _nul_records(
            output,
            max_records=self._limits.max_files,
            max_record_bytes=_MAX_TRACKED_PATH_BYTES,
            count_error="repository exceeds the tracked file count limit",
            record_error="tracked path exceeds the tracked path byte limit",
        )
        paths = tuple(_decode_tracked_path(record) for record in records)
        if len(paths) != len(set(paths)):
            raise RepositorySourceError("tracked file scope contains duplicate paths")
        return tuple(sorted(paths))

    def _tracked_entries(self, root: Path) -> dict[str, tuple[str, str]]:
        output = self._git(
            root,
            "ls-files",
            "--stage",
            "-z",
            stdout_limit=_nul_output_limit(
                self._limits.max_files,
                _MAX_TRACKED_PATH_BYTES + _MAX_INDEX_RECORD_OVERHEAD,
            ),
        )
        records = _nul_records(
            output,
            max_records=self._limits.max_files,
            max_record_bytes=_MAX_TRACKED_PATH_BYTES + _MAX_INDEX_RECORD_OVERHEAD,
            count_error="repository exceeds the tracked file count limit",
            record_error="Git index entry exceeds the byte limit",
        )
        entries: dict[str, tuple[str, str]] = {}
        for entry in records:
            try:
                metadata, raw_path = entry.split(b"\t", 1)
                mode_bytes, object_id_bytes, stage_bytes = metadata.split(b" ", 2)
                mode = mode_bytes.decode("ascii", errors="strict")
                object_id = object_id_bytes.decode("ascii", errors="strict")
                stage = stage_bytes.decode("ascii", errors="strict")
                path = _decode_tracked_path(raw_path)
            except (UnicodeError, ValueError, RepositorySourceError) as error:
                raise RepositorySourceError("Git index contains an unsafe tracked entry") from error
            if stage != "0" or path in entries:
                raise RepositorySourceError("Git index contains unresolved or duplicate entries")
            entries[path] = (mode, object_id)
        return entries

    def _validate_committed_index(
        self,
        root: Path,
        paths: tuple[str, ...],
        entries: dict[str, tuple[str, str]],
    ) -> None:
        if set(paths) != set(entries):
            raise RepositorySourceError("Git index does not match the tracked file scope")
        output = self._git(
            root,
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            "HEAD",
            stdout_limit=_nul_output_limit(
                self._limits.max_files,
                _MAX_TRACKED_PATH_BYTES + _MAX_INDEX_RECORD_OVERHEAD,
            ),
        )
        records = _nul_records(
            output,
            max_records=self._limits.max_files,
            max_record_bytes=_MAX_TRACKED_PATH_BYTES + _MAX_INDEX_RECORD_OVERHEAD,
            count_error="repository exceeds the tracked file count limit",
            record_error="Git tree entry exceeds the byte limit",
        )
        committed: dict[str, tuple[str, str]] = {}
        for entry in records:
            try:
                metadata, raw_path = entry.split(b"\t", 1)
                mode_bytes, _object_type, object_id_bytes = metadata.split(b" ", 2)
                mode = mode_bytes.decode("ascii", errors="strict")
                object_id = object_id_bytes.decode("ascii", errors="strict")
                path = _decode_tracked_path(raw_path)
            except (UnicodeError, ValueError, RepositorySourceError) as error:
                raise RepositorySourceError("Git tree contains an unsafe tracked entry") from error
            if path in committed:
                raise RepositorySourceError("Git tree contains duplicate tracked entries")
            committed[path] = (mode, object_id)
        if committed != entries:
            raise RepositorySourceError("tracked content is not clean")

    def _git_scalar(
        self,
        root: Path,
        *arguments: str,
        stdout_limit: int,
    ) -> str:
        output = self._git(root, *arguments, stdout_limit=stdout_limit)
        try:
            value = output.decode("utf-8", errors="strict")
        except UnicodeError as error:
            raise RepositorySourceError("local Git returned invalid metadata") from error
        if value.endswith("\n"):
            value = value[:-1]
        if not value or "\n" in value or "\r" in value or "\0" in value:
            raise RepositorySourceError("local Git returned ambiguous metadata")
        return value

    def _git(self, root: Path, *arguments: str, stdout_limit: int) -> bytes:
        _revalidate_executable(self._git_executable)
        return self._runner.run(
            [str(self._git_executable.path), *_GIT_OPTIONS, "-C", str(root), *arguments],
            env=_git_environment(),
            stdout_limit=stdout_limit,
            stderr_limit=_MAX_GIT_STDERR_BYTES,
            timeout_seconds=_GIT_TIMEOUT_SECONDS,
        ).stdout


def _git_environment() -> dict[str, str]:
    return {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/var/empty",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.defpath,
        "XDG_CONFIG_HOME": "/var/empty",
    }


def _require_supported_posix_platform() -> None:
    if (
        os.name != "posix"
        or sys.platform not in _SUPPORTED_PLATFORMS
        or not getattr(os, "O_NOFOLLOW", 0)
        or not getattr(os, "O_DIRECTORY", 0)
    ):
        raise RuntimeError(
            "local Git repository sources require POSIX Darwin/Linux with no-follow directory opens"
        )


def _trusted_system_git() -> _ExecutableIdentity:
    executable = shutil.which("git", path=os.defpath)
    if executable is None:
        raise RuntimeError("trusted administrator-owned Git executable was not found")
    try:
        path = Path(executable).resolve(strict=True)
        _validate_administrator_owned_path(path)
        return _executable_identity(path)
    except OSError as error:
        raise RuntimeError("trusted administrator-owned Git executable is unavailable") from error


def _validate_administrator_owned_path(path: Path) -> None:
    current = path
    is_executable = True
    while True:
        metadata = current.stat(follow_symlinks=False)
        if metadata.st_uid != 0 or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise RuntimeError("trusted Git executable path must be administrator-owned")
        if is_executable:
            if not stat.S_ISREG(metadata.st_mode) or not metadata.st_mode & stat.S_IXUSR:
                raise RuntimeError("trusted Git executable must be a regular owner-executable file")
            is_executable = False
        elif not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError("trusted Git executable ancestors must be directories")
        if current.parent == current:
            break
        current = current.parent


def _executable_identity(path: Path) -> _ExecutableIdentity:
    metadata = path.stat(follow_symlinks=False)
    return _ExecutableIdentity(
        path=path,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        owner=metadata.st_uid,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
    )


def _revalidate_executable(expected: _ExecutableIdentity) -> None:
    try:
        _validate_administrator_owned_path(expected.path)
        current = _executable_identity(expected.path)
    except (OSError, RuntimeError) as error:
        raise RepositorySourceError("trusted Git executable identity is no longer valid") from error
    if current != expected:
        raise RepositorySourceError("trusted Git executable identity changed before execution")


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=0.2)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _nul_output_limit(max_records: int, max_record_bytes: int) -> int:
    return (max_records + 1) * (max_record_bytes + 1)


def _nul_records(
    output: bytes,
    *,
    max_records: int,
    max_record_bytes: int,
    count_error: str,
    record_error: str,
) -> tuple[bytes, ...]:
    if not output:
        return ()
    records: list[bytes] = []
    offset = 0
    while offset < len(output):
        terminator = output.find(b"\0", offset)
        if terminator < 0:
            raise RepositorySourceError("local Git returned an unterminated NUL record")
        record = output[offset:terminator]
        if not record:
            raise RepositorySourceError("local Git returned an empty NUL record")
        if len(record) > max_record_bytes:
            raise RepositorySourceError(record_error)
        records.append(record)
        if len(records) > max_records:
            raise RepositorySourceError(count_error)
        offset = terminator + 1
    return tuple(records)


def _single_line_record(
    output: bytes,
    *,
    max_record_bytes: int,
    count_error: str,
) -> bytes:
    if not output.endswith(b"\n") or b"\0" in output or b"\r" in output:
        raise RepositorySourceError(count_error)
    records = output[:-1].split(b"\n")
    if len(records) != 1 or not records[0] or len(records[0]) > max_record_bytes:
        raise RepositorySourceError(count_error)
    return records[0]


def _decode_tracked_path(raw_path: bytes) -> str:
    if len(raw_path) > _MAX_TRACKED_PATH_BYTES:
        raise RepositorySourceError("tracked path exceeds the tracked path byte limit")
    try:
        decoded = raw_path.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise RepositorySourceError("tracked path is not valid UTF-8") from error
    return validate_relative_tracked_path(decoded)


def _git_blob_id(content: bytes, digest_length: int) -> str:
    if digest_length == 40:
        hasher = hashlib.sha1(usedforsecurity=False)
    elif digest_length == 64:
        hasher = hashlib.sha256()
    else:  # Descriptor validation makes this branch unreachable.
        raise RepositorySourceError("unsupported Git object format")
    hasher.update(f"blob {len(content)}\0".encode("ascii"))
    hasher.update(content)
    return hasher.hexdigest()


def _read_regular_file(
    root: Path,
    relative_path: str,
    max_file_bytes: int,
    *,
    expected_mode: str,
) -> bytes:
    path = validate_relative_tracked_path(relative_path)
    parts = path.split("/")
    descriptors: list[int] = []
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
    directory_flags = flags | os.O_DIRECTORY
    file_flags = flags | os.O_NONBLOCK
    try:
        current = os.open(root, directory_flags)
        descriptors.append(current)
        for directory in parts[:-1]:
            current = os.open(directory, directory_flags, dir_fd=current)
            descriptors.append(current)
        file_descriptor = os.open(parts[-1], file_flags, dir_fd=current)
        descriptors.append(file_descriptor)
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RepositorySourceError("tracked path is not a regular file")
        expected_executable = expected_mode == "100755"
        actual_executable = bool(before.st_mode & stat.S_IXUSR)
        if actual_executable != expected_executable:
            raise RepositorySourceError("tracked content is not clean")
        if before.st_size > max_file_bytes:
            raise RepositorySourceError("tracked file exceeds the per-file byte limit")

        chunks: list[bytes] = []
        remaining = max_file_bytes + 1
        while remaining:
            chunk = os.read(file_descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > max_file_bytes:
            raise RepositorySourceError("tracked file exceeds the per-file byte limit")
        after = os.fstat(file_descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if identity_before != identity_after or len(content) != after.st_size:
            raise RepositorySourceError("tracked file changed while it was being read")
        return content
    except RepositorySourceError:
        raise
    except OSError as error:
        raise RepositorySourceError("tracked path is not a stable regular file") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
