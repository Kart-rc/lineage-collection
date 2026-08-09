from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

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

    def __init__(
        self,
        limits: RepositorySourceLimits,
        *,
        git_executable: Path | str | None = None,
    ) -> None:
        self._limits = limits
        requested = Path(git_executable) if git_executable is not None else _default_git_executable()
        if not requested.is_absolute():
            raise ValueError("trusted Git executable must be an absolute executable file")
        normalized = Path(os.path.abspath(requested))
        try:
            resolved = normalized.resolve(strict=True)
        except OSError as error:
            raise ValueError("trusted Git executable must be an absolute executable file") from error
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise ValueError("trusted Git executable must be an absolute executable file")
        self._requested_git_executable = normalized
        self._git_executable = resolved

    def snapshot(self, descriptor: RepositoryCheckoutDescriptor) -> RepositorySnapshot:
        root = self._validated_root(descriptor.checkout_root)
        if _is_within(self._requested_git_executable, root) or _is_within(
            self._git_executable, root
        ):
            raise RepositorySourceError("trusted Git executable must be outside the checkout")
        self._validate_git_state(root, descriptor)
        paths = self._tracked_paths(root)
        if len(paths) > self._limits.max_files:
            raise RepositorySourceError("repository exceeds the tracked file count limit")

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
        if self._git(root, "rev-parse", "--is-inside-work-tree") != "true":
            raise RepositorySourceError("checkout root is not a Git work tree")
        top_level = self._git(root, "rev-parse", "--show-toplevel")
        try:
            resolved_top_level = Path(top_level).resolve(strict=True)
        except OSError as error:
            raise RepositorySourceError("Git work tree root is not accessible") from error
        if resolved_top_level != root:
            raise RepositorySourceError("checkout root must be the Git work tree root")

        head = self._git(root, "rev-parse", "--verify", "HEAD^{commit}")
        if head != descriptor.revision:
            raise RepositorySourceError("checkout HEAD does not match the exact revision")

        configured_origin = self._git(root, "config", "--get", "remote.origin.url")
        try:
            canonical_origin = canonicalize_https_origin(configured_origin)
        except ValueError:
            raise RepositorySourceError(
                "configured origin is not a credential-free canonical HTTPS origin"
            ) from None
        if canonical_origin != descriptor.origin:
            raise RepositorySourceError("configured origin does not match the requested origin")

    def _tracked_paths(self, root: Path) -> tuple[str, ...]:
        output = self._git(root, "ls-files", "-z")
        paths = tuple(validate_relative_tracked_path(item) for item in output.split("\0") if item)
        if len(paths) != len(set(paths)):
            raise RepositorySourceError("tracked file scope contains duplicate paths")
        return tuple(sorted(paths))

    def _tracked_entries(self, root: Path) -> dict[str, tuple[str, str]]:
        output = self._git(root, "ls-files", "--stage", "-z")
        entries: dict[str, tuple[str, str]] = {}
        for entry in output.split("\0"):
            if not entry:
                continue
            try:
                metadata, raw_path = entry.split("\t", 1)
                mode, object_id, stage = metadata.split(" ", 2)
                path = validate_relative_tracked_path(raw_path)
            except (ValueError, RepositorySourceError) as error:
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
        output = self._git(root, "ls-tree", "-r", "-z", "--full-tree", "HEAD")
        committed: dict[str, tuple[str, str]] = {}
        for entry in output.split("\0"):
            if not entry:
                continue
            try:
                metadata, raw_path = entry.split("\t", 1)
                mode, _object_type, object_id = metadata.split(" ", 2)
                path = validate_relative_tracked_path(raw_path)
            except (ValueError, RepositorySourceError) as error:
                raise RepositorySourceError("Git tree contains an unsafe tracked entry") from error
            if path in committed:
                raise RepositorySourceError("Git tree contains duplicate tracked entries")
            committed[path] = (mode, object_id)
        if committed != entries:
            raise RepositorySourceError("tracked content is not clean")

    def _git(self, root: Path, *arguments: str) -> str:
        try:
            completed = subprocess.run(
                [str(self._git_executable), *_GIT_OPTIONS, "-C", str(root), *arguments],
                shell=False,
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
                env=_git_environment(),
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as error:
            raise RepositorySourceError("local Git validation failed") from error
        return completed.stdout.rstrip("\n")


def _git_environment() -> dict[str, str]:
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "PATH": os.defpath,
        }
    )
    return environment


def _default_git_executable() -> Path:
    executable = shutil.which("git", path=os.defpath)
    if executable is None:
        raise ValueError("trusted Git executable was not found on the system search path")
    return Path(executable)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


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
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_flags = flags | getattr(os, "O_DIRECTORY", 0)
    try:
        current = os.open(root, directory_flags)
        descriptors.append(current)
        for directory in parts[:-1]:
            current = os.open(directory, directory_flags, dir_fd=current)
            descriptors.append(current)
        file_descriptor = os.open(parts[-1], flags, dir_fd=current)
        descriptors.append(file_descriptor)
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RepositorySourceError("tracked path is not a regular file")
        expected_executable = expected_mode == "100755"
        actual_executable = bool(before.st_mode & 0o111)
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
