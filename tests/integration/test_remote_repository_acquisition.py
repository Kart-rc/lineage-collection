"""Opt-in proof that remote exact-revision acquisition reaches the real pinned commit.

This module makes an outbound HTTPS request to a public Git host and is therefore
skipped unless the operator explicitly opts in:

    LINEAGE_REAL_REMOTE_ACQUISITION=1 \\
      uv run --project apps/api --extra dev pytest \\
      tests/integration/test_remote_repository_acquisition.py -q

Everything else in the remote acquisition suite runs offline through an injected
transport boundary; only this file is allowed to leave the machine.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lineage_api.application.repository_sources import (
    RemoteRepositoryRequest,
    RepositoryCheckoutDescriptor,
    RepositorySourceLimits,
)
from lineage_api.infrastructure.local_git_source import LocalGitRepositorySource
from lineage_api.infrastructure.remote_git_source import RemoteGitRepositorySource


ORIGIN = "https://github.com/spring-projects/spring-petclinic"
REVISION = "88e37c15cf6fc8490b01bc3e8e2c800cec1ac272"
LIMITS = RepositorySourceLimits(
    max_files=10_000,
    max_file_bytes=4 * 1024 * 1024,
    max_total_bytes=128 * 1024 * 1024,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("LINEAGE_REAL_REMOTE_ACQUISITION") != "1",
    reason="opt in with LINEAGE_REAL_REMOTE_ACQUISITION=1 to allow outbound Git traffic",
)


def _request() -> RemoteRepositoryRequest:
    return RemoteRepositoryRequest(
        origin=ORIGIN,
        repository="spring-petclinic",
        revision=REVISION,
        environment="staging",
        platform="postgres",
        system="petclinic",
        analyzer_pack="java-spring-data-jpa-v1",
        ruleset="spring-data-rules-v1",
    )


def _source() -> RemoteGitRepositorySource:
    return RemoteGitRepositorySource(LIMITS, timeout_seconds=300)


def test_acquires_the_pinned_petclinic_commit_over_https() -> None:
    snapshot = _source().acquire(_request())

    assert snapshot.revision == REVISION
    assert snapshot.origin == ORIGIN
    assert snapshot.scope_digest.startswith("sha256:")
    assert "src/main/resources/db/postgres/schema.sql" in snapshot.paths
    assert b"create table" in snapshot.read_bytes(
        "src/main/resources/db/postgres/schema.sql"
    ).lower()


def test_remote_acquisition_reproduces_the_existing_local_snapshot() -> None:
    checkout = os.environ.get("LINEAGE_REAL_REPOSITORY_CHECKOUT")
    if not checkout:
        pytest.skip("set LINEAGE_REAL_REPOSITORY_CHECKOUT to compare against a local checkout")

    local = LocalGitRepositorySource(LIMITS).snapshot(
        RepositoryCheckoutDescriptor(
            origin=ORIGIN,
            repository="spring-petclinic",
            revision=REVISION,
            checkout_root=Path(checkout).resolve(strict=True),
            environment="staging",
            platform="postgres",
            system="petclinic",
            analyzer_pack="java-spring-data-jpa-v1",
            ruleset="spring-data-rules-v1",
        )
    )
    remote = _source().acquire(_request())

    assert remote.paths == local.paths
    assert remote.scope_digest == local.scope_digest


def test_repeated_remote_acquisition_produces_no_new_identity() -> None:
    source = _source()

    first = source.acquire(_request())
    second = source.acquire(_request())

    assert first.scope_digest == second.scope_digest
    assert first.paths == second.paths
