from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from lineage_api.application.repository_collection import (
    AnalyzerIdentity,
    RepositoryCollectionDescriptor,
    RepositoryIdentity,
)
from lineage_api.application.repository_sources import RepositorySnapshot


_EXACT_COMMIT = re.compile(r"[0-9a-f]{40}")
_MAX_LOCAL_PATH_BYTES = 4_096


@dataclass(frozen=True, slots=True)
class _RepositoryRequest:
    origin: str
    repository: str
    revision: str
    environment: str
    platform: str
    system: str
    analyzer_pack: str
    ruleset: str
    schema_profile: str

    def __post_init__(self) -> None:
        self.repository_identity()
        self.analyzer_identity()
        if _EXACT_COMMIT.fullmatch(self.revision) is None:
            raise ValueError("revision must be an exact lowercase 40-hex commit")

    def repository_identity(self) -> RepositoryIdentity:
        return RepositoryIdentity(
            origin=self.origin,
            repository=self.repository,
            revision=self.revision,
            environment=self.environment,
            platform=self.platform,
            system=self.system,
        )

    def analyzer_identity(self) -> AnalyzerIdentity:
        return AnalyzerIdentity(
            analyzer_pack=self.analyzer_pack,
            ruleset=self.ruleset,
            source_kind="git-checkout",
            framework="spring-data-jpa",
            schema_profile=self.schema_profile,
        )


@dataclass(frozen=True, slots=True)
class LocalCheckoutRequest(_RepositoryRequest):
    checkout_root: Path
    source_type: Literal["LOCAL_CHECKOUT"] = field(
        init=False, default="LOCAL_CHECKOUT"
    )

    def __post_init__(self) -> None:
        _RepositoryRequest.__post_init__(self)
        root = Path(self.checkout_root)
        rendered = str(root)
        if (
            not root.is_absolute()
            or len(rendered.encode()) > _MAX_LOCAL_PATH_BYTES
            or any(ord(character) < 32 for character in rendered)
        ):
            raise ValueError("local checkout path is outside bounds")
        object.__setattr__(self, "checkout_root", root)


@dataclass(frozen=True, slots=True)
class GitRepositoryRequest(_RepositoryRequest):
    source_type: Literal["GIT"] = field(init=False, default="GIT")


RepositorySourceRequest = LocalCheckoutRequest | GitRepositoryRequest


class RepositoryAcquisitionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message[:160])
        self.code = code


LocalSnapshotProvider = Callable[[LocalCheckoutRequest], RepositorySnapshot]
RemoteSnapshotProvider = Callable[[GitRepositoryRequest], RepositorySnapshot]
CollectionCallback = Callable[[RepositoryCollectionDescriptor], dict[str, Any]]


class RepositoryAcquisitionService:
    def __init__(
        self,
        *,
        allow_local_repository_sources: bool,
        local_snapshot_provider: LocalSnapshotProvider,
        remote_snapshot_provider: RemoteSnapshotProvider,
        collect: CollectionCallback,
    ) -> None:
        if not isinstance(allow_local_repository_sources, bool):
            raise TypeError("local repository source policy must be boolean")
        if not all(
            callable(candidate)
            for candidate in (local_snapshot_provider, remote_snapshot_provider, collect)
        ):
            raise TypeError("repository acquisition providers must be callable")
        self._allow_local = allow_local_repository_sources
        self._local_snapshot = local_snapshot_provider
        self._remote_snapshot = remote_snapshot_provider
        self._collect = collect

    def collect(self, request: RepositorySourceRequest) -> dict[str, Any]:
        if isinstance(request, LocalCheckoutRequest):
            if not self._allow_local:
                raise RepositoryAcquisitionError(
                    "LOCAL_SOURCE_DISABLED", "local repository sources are disabled"
                )
            provider: Callable[[RepositorySourceRequest], RepositorySnapshot] = (
                self._local_snapshot
            )
        elif isinstance(request, GitRepositoryRequest):
            provider = self._remote_snapshot
        else:
            raise TypeError("bounded repository source request is required")
        try:
            snapshot = provider(request)
            if not isinstance(snapshot, RepositorySnapshot):
                raise TypeError("repository source provider returned no snapshot")
        except Exception:
            raise RepositoryAcquisitionError(
                "SOURCE_ACQUISITION_FAILED", "repository source acquisition failed"
            ) from None
        descriptor = RepositoryCollectionDescriptor(
            repository=request.repository_identity(),
            analyzer=request.analyzer_identity(),
            snapshot=snapshot,
        )
        return self._collect(descriptor)
