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
from lineage_api.domain.product_confidence import project_confidence


PROJECT_ROOT = Path(__file__).parents[4]
ORIGIN = "https://example.com/acme/payments-pipeline"
REPOSITORY = "payments-pipeline"
REVISION = "c" * 40
SECRET = "collection-runtime-acceptance-secret"


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
def services(tmp_path: Path):
    settings = Settings(
        project_root=PROJECT_ROOT,
        data_directory=tmp_path,
        fixture_directory=PROJECT_ROOT / "fixtures",
        database_path=tmp_path / "lineage.db",
        object_directory=tmp_path / "objects",
        webhook_secret=SECRET,
    )
    return build_services(settings)


@pytest.fixture
def descriptor() -> RepositoryCollectionDescriptor:
    return _descriptor(_snapshot())


def test_payments_pipeline_reaches_high_through_a_real_session(services, descriptor):
    summary = services.repository_collection.collect(
        descriptor, runtime_execution=True
    )
    assert summary["runtimeStatus"] == "CORROBORATED"
    assert summary["runtimeReasons"] == []

    evidence = services.runtime.completed_evidence(
        repo=descriptor.repository.repository,
        environment=descriptor.repository.environment,
        artifact_digest=descriptor.repository.revision,
    )
    assert evidence and evidence[0]["manifest"]["outcome"] == "COMPLETE"

    # Band proof: at least one consolidated edge carries SCA + ELEMENT runtime -> HIGH.
    edges = [
        edge.as_dict()
        for edge in services.orchestration._consolidation._latest_edges()
    ]
    high = [e for e in edges if e["band"] == "HIGH"]
    assert high, [e["band"] for e in edges]
    mechanisms = {p["mechanism"] for p in high[0]["provenance"]}
    assert mechanisms == {"SCA", "RUNTIME"}

    display = project_confidence(high[0]["band"], high[0]["provenance"])
    assert display.display_band == "VERIFIED"
    assert display.percent == 92
