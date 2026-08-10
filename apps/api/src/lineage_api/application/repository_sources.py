from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Protocol
from urllib.parse import urlsplit, urlunsplit


_EXACT_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_REPOSITORY_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?")
_SCOPE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_HTTPS_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")
_HTTPS_PATH = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@/-]+")


class RepositorySourceError(RuntimeError):
    """A checkout failed a source-integrity or resource-bound check."""


class RepositoryIdentityError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def canonicalize_https_origin(origin: str) -> str:
    """Return the credential-free canonical identity for an HTTPS Git origin."""

    if (
        not isinstance(origin, str)
        or not origin
        or not origin.isascii()
        or any(character.isspace() or ord(character) < 32 for character in origin)
    ):
        raise ValueError("origin must be a canonical HTTPS repository URL")
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError as error:
        raise ValueError("origin must be a canonical HTTPS repository URL") from error
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("credential-bearing repository origins are forbidden")
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or port == 0
        or parsed.query
        or parsed.fragment
        or "\\" in origin
    ):
        raise ValueError("origin must be a canonical HTTPS repository URL")

    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    hostname = parsed.hostname.lower()
    if (
        _HTTPS_HOST.fullmatch(hostname) is None
        or _HTTPS_PATH.fullmatch(path) is None
        or path == "/"
        or any(part in {"", ".", ".."} for part in path.split("/")[1:])
    ):
        raise ValueError("origin must be a canonical HTTPS repository URL")

    netloc = hostname if port in {None, 443} else f"{hostname}:{port}"
    return urlunsplit(("https", netloc, path, "", ""))


def validate_repository_identity(origin: str, repository: str) -> str:
    try:
        canonical_origin = canonicalize_https_origin(origin)
    except ValueError as error:
        raise RepositoryIdentityError("origin", str(error)) from None
    if origin != canonical_origin:
        raise RepositoryIdentityError(
            "origin", "origin must be a canonical HTTPS repository URL"
        )
    if _REPOSITORY_NAME.fullmatch(repository) is None:
        raise RepositoryIdentityError("repository", "repository must be a safe repository name")
    if canonical_origin.rsplit("/", 1)[-1] != repository:
        raise RepositoryIdentityError(
            "repository", "repository must match the canonical origin"
        )
    return canonical_origin


def validate_relative_tracked_path(relative_path: str) -> str:
    if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
        raise RepositorySourceError("source path must be a relative tracked path")
    path = PurePosixPath(relative_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RepositorySourceError("source path must be a relative tracked path")
    canonical = path.as_posix()
    if canonical != relative_path:
        raise RepositorySourceError("source path must be a relative tracked path")
    return canonical


@dataclass(frozen=True, slots=True)
class RepositoryCheckoutDescriptor:
    origin: str
    repository: str
    revision: str
    checkout_root: Path
    environment: str
    platform: str
    system: str
    analyzer_pack: str
    ruleset: str
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        validate_repository_identity(self.origin, self.repository)
        if _EXACT_REVISION.fullmatch(self.revision) is None:
            raise ValueError("revision must be an exact lowercase 40- or 64-hex digest")
        if self.schema_version != "1.0.0":
            raise ValueError("repository checkout schema version must be 1.0.0")
        checkout_root = Path(self.checkout_root)
        if not checkout_root.is_absolute():
            raise ValueError("checkout root must be absolute")
        object.__setattr__(self, "checkout_root", checkout_root)
        for name in ("environment", "platform", "system", "analyzer_pack", "ruleset"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name.replace('_', ' ')} must be a non-empty determinant")


@dataclass(frozen=True, slots=True)
class RepositorySourceLimits:
    max_files: int
    max_file_bytes: int
    max_total_bytes: int

    def __post_init__(self) -> None:
        for name in ("max_files", "max_file_bytes", "max_total_bytes"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name.replace('_', ' ')} must be a positive integer")


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    descriptor: RepositoryCheckoutDescriptor
    paths: tuple[str, ...]
    scope_digest: str
    _reader: Callable[[str], bytes] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.paths != tuple(sorted(set(self.paths))):
            raise ValueError("snapshot paths must be sorted and unique")
        for path in self.paths:
            validate_relative_tracked_path(path)
        if _SCOPE_DIGEST.fullmatch(self.scope_digest) is None:
            raise ValueError("scope digest must be an exact sha256 digest")

    @property
    def origin(self) -> str:
        return self.descriptor.origin

    @property
    def schema_version(self) -> str:
        return self.descriptor.schema_version

    @property
    def repository(self) -> str:
        return self.descriptor.repository

    @property
    def revision(self) -> str:
        return self.descriptor.revision

    @property
    def checkout_root(self) -> Path:
        return self.descriptor.checkout_root

    @property
    def environment(self) -> str:
        return self.descriptor.environment

    @property
    def platform(self) -> str:
        return self.descriptor.platform

    @property
    def system(self) -> str:
        return self.descriptor.system

    @property
    def analyzer_pack(self) -> str:
        return self.descriptor.analyzer_pack

    @property
    def ruleset(self) -> str:
        return self.descriptor.ruleset

    def read_bytes(self, relative_path: str) -> bytes:
        path = validate_relative_tracked_path(relative_path)
        if path not in self.paths:
            raise RepositorySourceError("source path is outside the explicit tracked scope")
        return self._reader(path)


class RepositorySource(Protocol):
    def snapshot(self, descriptor: RepositoryCheckoutDescriptor) -> RepositorySnapshot: ...
