from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from lineage_api.application.repository_collection import (
    AnalyzerIdentity,
    RepositoryCollectionDescriptor,
    RepositoryCollectionService,
    RepositoryIdentity,
)
from lineage_api.application.repository_sources import (
    RepositoryCheckoutDescriptor,
    RepositorySnapshot,
)


PROJECT_ROOT = Path(__file__).parents[4]
ORIGIN = "https://example.com/acme/spring-service"
REVISION = "a" * 40
SECRET = "repository-collection-secret"


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

    def read_source(path: str) -> bytes:
        return sources[path]

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


@pytest.fixture
def collection_service(tmp_path):
    def process_push(requested_snapshot, delivery):
        return _orchestration_result()

    return RepositoryCollectionService(
        webhook_secret=SECRET,
        process_push=process_push,
    )


@pytest.fixture
def descriptor(tmp_path):
    snapshot = _snapshot(tmp_path)
    return _descriptor(snapshot)


def test_collect_defaults_to_not_requested(collection_service, descriptor):
    summary = collection_service.collect(descriptor)
    assert summary["runtimeStatus"] == "NOT_PROVIDED"
    assert summary["runtimeReasons"] == ["not-requested"]


def test_collect_with_runtime_execution_adds_the_payload_flag(monkeypatch, tmp_path):
    snapshot = _snapshot(tmp_path)
    descriptor = _descriptor(snapshot)
    captured_payloads = {}

    def process_push(requested_snapshot, delivery):
        payload = delivery.payload
        captured_payloads["with_runtime"] = payload.copy()
        return _orchestration_result()

    service = RepositoryCollectionService(
        webhook_secret=SECRET,
        process_push=process_push,
    )

    # Call with runtime_execution=True
    service.collect(descriptor, runtime_execution=True)
    assert "runtimeExecution" in captured_payloads["with_runtime"]
    assert captured_payloads["with_runtime"]["runtimeExecution"] is True

    # Call without runtime_execution (default False)
    service.collect(descriptor)
    captured_payloads["without_runtime"] = {}

    def process_push_default(requested_snapshot, delivery):
        payload = delivery.payload
        captured_payloads["without_runtime"] = payload.copy()
        return _orchestration_result()

    service = RepositoryCollectionService(
        webhook_secret=SECRET,
        process_push=process_push_default,
    )
    service.collect(descriptor)
    assert "runtimeExecution" not in captured_payloads["without_runtime"]
