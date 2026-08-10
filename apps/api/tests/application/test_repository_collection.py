from __future__ import annotations

import hashlib
import hmac
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from lineage_api.application.repository_collection import (
    AnalyzerIdentity,
    RepositoryCollectionDescriptor,
    RepositoryCollectionError,
    RepositoryCollectionService,
    RepositoryIdentity,
)
from lineage_api.application.repository_sources import (
    RepositoryCheckoutDescriptor,
    RepositorySnapshot,
)
from lineage_api.config import Settings
from lineage_api.dependencies import build_services


PROJECT_ROOT = Path(__file__).parents[4]
ORIGIN = "https://example.com/acme/spring-service"
REVISION = "a" * 40
SECRET = "repository-collection-secret"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=PROJECT_ROOT,
        data_directory=tmp_path,
        fixture_directory=PROJECT_ROOT / "fixtures",
        database_path=tmp_path / "lineage.db",
        object_directory=tmp_path / "objects",
        webhook_secret=SECRET,
    )


def _snapshot(tmp_path: Path) -> RepositorySnapshot:
    sources = {
        "pom.xml": b"""<project><parent><groupId>org.springframework.boot</groupId>
<artifactId>spring-boot-starter-parent</artifactId><version>4.1.0</version></parent>
<dependencies><dependency><groupId>org.springframework.boot</groupId>
<artifactId>spring-boot-starter-data-jpa</artifactId></dependency></dependencies></project>""",
        "src/main/java/example/Owner.java": (
            b'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
            b'@Entity @Table(name="owners") class Owner {}'
        ),
        "src/main/java/example/OwnerRepository.java": (
            b"package example; import org.springframework.data.jpa.repository.JpaRepository; "
            b"interface OwnerRepository extends JpaRepository<Owner,Integer> {}"
        ),
        "src/main/java/example/OwnerService.java": (
            b"package example; class OwnerService { private final OwnerRepository owners; "
            b"OwnerService(OwnerRepository owners) { this.owners = owners; } "
            b"Owner read(Integer id) { return owners.findById(id).orElseThrow(); } "
            b"Owner write(Owner owner) { return owners.save(owner); }}"
        ),
        "src/main/resources/db/postgres/schema.sql": (
            b"create table owners (id integer primary key);"
        ),
    }
    return RepositorySnapshot(
        descriptor=RepositoryCheckoutDescriptor(
            origin=ORIGIN,
            repository="spring-service",
            revision=REVISION,
            checkout_root=tmp_path.resolve(),
            environment="staging",
            platform="postgres",
            system="orders",
            analyzer_pack="java-spring-data-jpa-v1",
            ruleset="spring-data-rules-v1",
        ),
        paths=tuple(sorted(sources)),
        scope_digest="sha256:" + hashlib.sha256(b"bounded-test-scope").hexdigest(),
        _reader=sources.__getitem__,
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
            source_kind="git-checkout",
            framework="spring-data-jpa",
            schema_profile="postgres",
        ),
        snapshot=snapshot,
    )


def _orchestration_result() -> dict[str, object]:
    return {
        "outcome": "ACCEPTED",
        "reason": None,
        "command": {
            "commandId": "command-collection",
            "status": "COMPLETED",
            "determinantDigest": "sha256:" + "b" * 64,
        },
        "run": {
            "runId": "run-collection",
            "state": "IN_REVIEW",
            "stages": [{"stage": "QUEUED"}, {"stage": "IN_REVIEW"}],
        },
        "proposal": {
            "proposalId": "proposal-collection",
            "state": "IN_REVIEW",
        },
        "runtimeStatus": "NOT_PROVIDED",
        "analysis": {
            "status": "COMPLETE",
            "statusReasons": [],
            "edgeCount": 15,
            "readCount": 10,
            "writeCount": 5,
            "residueCount": 0,
            "unresolvedCount": 0,
        },
        "coverageManifest": {
            "manifestId": "coverage-collection",
            "state": "COMPLETE",
            "determinantDigest": "sha256:" + "c" * 64,
            "sourceScopeDispositionDigest": "sha256:" + "d" * 64,
            "expectedScope": ["one", "two"],
            "completedScope": ["one"],
            "skippedScope": ["two"],
            "unsupportedScope": [],
            "failedScope": [],
        },
    }


def test_collect_returns_api_representation_and_signs_deterministic_payload(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    deliveries = []

    def process_push(requested_snapshot, delivery):
        assert requested_snapshot is snapshot
        deliveries.append(delivery)
        return _orchestration_result()

    service = RepositoryCollectionService(
        webhook_secret=SECRET,
        process_push=process_push,
    )

    result = service.collect(_descriptor(snapshot))

    assert result == {
        "collectionId": "command-collection",
        "commandId": "command-collection",
        "statusUrl": "/api/collections/command-collection",
        "outcome": "ACCEPTED",
        "reasonCode": None,
        "commandStatus": "COMPLETED",
        "determinantDigest": "sha256:" + "b" * 64,
        "revision": REVISION,
        "scopeDigest": snapshot.scope_digest,
        "runId": "run-collection",
        "runStatus": "IN_REVIEW",
        "stages": ["QUEUED", "IN_REVIEW"],
        "proposalId": "proposal-collection",
        "proposalStatus": "IN_REVIEW",
        "runtimeStatus": "NOT_PROVIDED",
        "analysisStatus": "COMPLETE",
        "statusReasons": [],
        "coverageManifest": {
            "manifestId": "coverage-collection",
            "state": "COMPLETE",
            "determinantDigest": "sha256:" + "c" * 64,
            "sourceScopeDispositionDigest": "sha256:" + "d" * 64,
            "counts": {
                "expected": 2,
                "completed": 1,
                "skipped": 1,
                "unsupported": 0,
                "failed": 0,
            },
        },
        "counts": {
            "edges": 15,
            "reads": 10,
            "writes": 5,
            "residue": 0,
            "unresolved": 0,
        },
    }
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery.payload["eventId"].startswith("checkout-")
    assert delivery.payload["receivedAt"] == "1970-01-01T00:00:00Z"
    expected_signature = hmac.new(
        SECRET.encode(), delivery.canonical_body, hashlib.sha256
    ).hexdigest()
    assert delivery.signature == f"sha256={expected_signature}"


def test_equivalent_collection_reuses_identities_without_durable_or_evidence_effects(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    service = build_services(
        _settings(tmp_path), repository_snapshot=snapshot
    ).repository_collection
    descriptor = _descriptor(snapshot)

    first = service.collect(descriptor)
    before = _table_counts(_settings(tmp_path).database_path)
    duplicate = service.collect(descriptor)
    after = _table_counts(_settings(tmp_path).database_path)

    assert first["outcome"] == "ACCEPTED"
    assert first["runStatus"] == "IN_REVIEW"
    assert first["counts"] == {
        "edges": 2,
        "reads": 1,
        "writes": 1,
        "residue": 0,
        "unresolved": 0,
    }
    assert duplicate["outcome"] == "DUPLICATE"
    assert duplicate["collectionId"] == first["collectionId"]
    assert duplicate["commandId"] == first["commandId"]
    assert duplicate["runId"] == first["runId"]
    assert duplicate["proposalId"] == first["proposalId"]
    assert after == before


def test_collection_inputs_are_immutable_and_mismatches_fail_without_source_leaks(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    descriptor = _descriptor(snapshot)
    source_marker = "return owners.findById"

    with pytest.raises(FrozenInstanceError):
        descriptor.repository.repository = "changed"  # type: ignore[misc]

    mismatched = RepositoryCollectionDescriptor(
        repository=RepositoryIdentity(
            origin=descriptor.repository.origin,
            repository=descriptor.repository.repository,
            revision="f" * 40,
            environment=descriptor.repository.environment,
            platform=descriptor.repository.platform,
            system=descriptor.repository.system,
        ),
        analyzer=descriptor.analyzer,
        snapshot=snapshot,
    )
    service = RepositoryCollectionService(
        webhook_secret=SECRET,
        process_push=lambda _snapshot, _delivery: pytest.fail(
            "invalid descriptor reached orchestration"
        ),
    )

    with pytest.raises(ValueError) as captured:
        service.collect(mismatched)

    message = str(captured.value)
    assert str(tmp_path) not in message
    assert source_marker not in message


def test_collection_inputs_and_pipeline_errors_remain_bounded(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    descriptor = _descriptor(snapshot)

    with pytest.raises(ValueError):
        RepositoryIdentity(
            origin=None,  # type: ignore[arg-type]
            repository="spring-service",
            revision=REVISION,
            environment="staging",
            platform="postgres",
            system="orders",
        )

    leaking_error = f"{tmp_path}: return owners.findById(id)"
    service = RepositoryCollectionService(
        webhook_secret=SECRET,
        process_push=lambda _snapshot, _delivery: (_ for _ in ()).throw(
            RuntimeError(leaking_error)
        ),
    )

    with pytest.raises(RepositoryCollectionError) as captured:
        service.collect(descriptor)

    assert captured.value.code == "PIPELINE_FAILED"
    assert str(captured.value) == "repository collection failed"
    assert str(tmp_path) not in str(captured.value)


def _table_counts(database_path: Path) -> dict[str, int]:
    import sqlite3

    with sqlite3.connect(database_path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "events",
                "commands",
                "outbox_events",
                "lane_messages",
                "runs",
                "proposals",
                "edge_ledger",
                "stage_results",
                "coverage_manifests",
                "evidence_objects",
            )
        }
