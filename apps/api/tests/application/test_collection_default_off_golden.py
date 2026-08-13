from __future__ import annotations

from pathlib import Path

import pytest

from lineage_api.application.repository_collection import (
    AnalyzerIdentity,
    RepositoryCollectionDescriptor,
    RepositoryIdentity,
)
from lineage_api.application.repository_sources import (
    RepositoryCheckoutDescriptor,
    RepositorySnapshot,
)
from lineage_api.config import Settings
from lineage_api.dependencies import build_services


PROJECT_ROOT = Path(__file__).parents[4]
ORIGIN = "https://example.com/acme/payments-pipeline"
REPOSITORY = "payments-pipeline"
REVISION = "c" * 40
SECRET = "collection-default-off-golden-secret"


def _snapshot() -> RepositorySnapshot:
    repository_root = PROJECT_ROOT / "fixtures" / "repositories" / REPOSITORY
    paths = ("pipeline.py",)
    sources = {path: (repository_root / path).read_bytes() for path in paths}

    def read_source(path: str) -> bytes:
        return sources[path]

    import hashlib

    return RepositorySnapshot(
        descriptor=RepositoryCheckoutDescriptor(
            origin=ORIGIN,
            repository=REPOSITORY,
            revision=REVISION,
            checkout_root=repository_root.resolve(),
            environment="staging",
            platform="snowflake",
            system="payments",
            analyzer_pack="python-fixture-v1",
            ruleset="python-demo-v1",
        ),
        paths=paths,
        scope_digest="sha256:"
        + hashlib.sha256(b"".join(sources[path] for path in paths)).hexdigest(),
        _reader=read_source,
    )


def _descriptor(snapshot: RepositorySnapshot) -> RepositoryCollectionDescriptor:
    return RepositoryCollectionDescriptor(
        repository=RepositoryIdentity(
            origin=snapshot.origin,
            repository=snapshot.repository,
            revision=snapshot.revision,
            environment=snapshot.environment,
            platform=snapshot.platform,
            system=snapshot.system,
        ),
        analyzer=AnalyzerIdentity(
            analyzer_pack=snapshot.analyzer_pack,
            ruleset=snapshot.ruleset,
            source_kind="fixture",
            framework="python-dataset-api",
            schema_profile="snowflake",
        ),
        snapshot=snapshot,
    )


@pytest.fixture
def descriptor() -> RepositoryCollectionDescriptor:
    return _descriptor(_snapshot())


def test_default_off_behavior(tmp_path: Path, descriptor) -> None:
    """The golden test: verify that runtime_execution defaults to False.

    Collect the payments fixture twice in separate service instances — once with no flag
    (default behavior), once with runtime_execution=False explicitly — and assert:
    - Both summaries are identical
    - runtimeStatus == "NOT_PROVIDED"
    - runtimeReasons == ["not-requested"]
    - services.runtime.evidence_status(...) returns {"status": "NOT_PROVIDED", "sessionIds": []}

    This regression lock ensures that the product default remains off.
    """
    # Create first services instance for default collection
    settings_default = Settings(
        project_root=PROJECT_ROOT,
        data_directory=tmp_path / "default",
        fixture_directory=PROJECT_ROOT / "fixtures",
        database_path=tmp_path / "default" / "lineage.db",
        object_directory=tmp_path / "default" / "objects",
        webhook_secret=SECRET,
    )
    services_default = build_services(settings_default)

    # Collect with no flag (default behavior)
    summary_default = services_default.repository_collection.collect(descriptor)

    # Create second services instance for explicit False collection
    settings_explicit = Settings(
        project_root=PROJECT_ROOT,
        data_directory=tmp_path / "explicit",
        fixture_directory=PROJECT_ROOT / "fixtures",
        database_path=tmp_path / "explicit" / "lineage.db",
        object_directory=tmp_path / "explicit" / "objects",
        webhook_secret=SECRET,
    )
    services_explicit = build_services(settings_explicit)

    # Collect with explicit runtime_execution=False
    summary_explicit = services_explicit.repository_collection.collect(
        descriptor, runtime_execution=False
    )

    # Both summaries must be identical
    assert summary_default == summary_explicit, (
        "Default and explicit False should produce identical summaries"
    )

    # Verify runtime status fields on both
    for summary in [summary_default, summary_explicit]:
        assert summary["runtimeStatus"] == "NOT_PROVIDED", (
            f"runtimeStatus should be NOT_PROVIDED but got {summary['runtimeStatus']}"
        )
        assert summary["runtimeReasons"] == ["not-requested"], (
            f"runtimeReasons should be ['not-requested'] but got {summary['runtimeReasons']}"
        )

    # Verify that no session was created via evidence_status in both
    for services in [services_default, services_explicit]:
        evidence = services.runtime.evidence_status(
            repo=descriptor.repository.repository,
            environment=descriptor.repository.environment,
            artifact_digest=descriptor.repository.revision,
        )
        assert evidence["status"] == "NOT_PROVIDED", (
            f"evidence_status should return NOT_PROVIDED but got {evidence['status']}"
        )
        assert evidence["sessionIds"] == [], (
            f"sessionIds should be empty but got {evidence['sessionIds']}"
        )
