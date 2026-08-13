from __future__ import annotations

import hashlib
import hmac
import json
import math
import sqlite3
import stat
from collections import Counter
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
_MAX_APPLICATION_TABLES = 128
_MAX_ROWS_PER_TABLE = 100_000
_MAX_DATABASE_SNAPSHOT_BYTES = 128 * 1024 * 1024
_MAX_SQLITE_VALUE_BYTES = 8 * 1024 * 1024
_MAX_OBJECT_ENTRIES = 10_000
_MAX_OBJECT_FILE_BYTES = 16 * 1024 * 1024
_MAX_OBJECT_TREE_BYTES = 128 * 1024 * 1024
_MAX_RELATIVE_PATH_BYTES = 4_096
_REQUIRED_EFFECT_TABLES = frozenset(
    {
        "audit_events",
        "classification_decisions",
        "command_attempts",
        "commands",
        "coverage_manifests",
        "edge_ledger",
        "evidence_objects",
        "events",
        "lane_messages",
        "outbox_events",
        "proposals",
        "quarantines",
        "run_stages",
        "runs",
        "stage_results",
    }
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=PROJECT_ROOT,
        data_directory=tmp_path,
        fixture_directory=PROJECT_ROOT / "fixtures",
        database_path=tmp_path / "lineage.db",
        object_directory=tmp_path / "objects",
        webhook_secret=SECRET,
    )


def _snapshot(
    tmp_path: Path, *, reads: Counter[str] | None = None
) -> RepositorySnapshot:
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
        if reads is not None:
            reads[path] += 1
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
        "runtimeReasons": ["not-requested"],
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


def test_signed_delivery_payload_mutation_cannot_invalidate_signed_bytes(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    observed: dict[str, object] = {}

    def mutate_callback_payload(_snapshot, delivery):
        signed_body = delivery.canonical_body
        callback_payload = delivery.payload
        callback_payload["digest"] = "f" * 40
        callback_payload["repositorySource"]["scopeDigest"] = "sha256:" + "f" * 64
        current_body = delivery.canonical_body
        observed.update(
            body_is_cached=current_body is signed_body,
            body_is_unchanged=current_body == signed_body,
            digest=delivery.payload["digest"],
            scope_digest=delivery.payload["repositorySource"]["scopeDigest"],
            signature_is_valid=hmac.compare_digest(
                delivery.signature.removeprefix("sha256="),
                hmac.new(SECRET.encode(), current_body, hashlib.sha256).hexdigest(),
            ),
        )
        return _orchestration_result()

    service = RepositoryCollectionService(
        webhook_secret=SECRET,
        process_push=mutate_callback_payload,
    )

    service.collect(_descriptor(snapshot))

    assert observed == {
        "body_is_cached": True,
        "body_is_unchanged": True,
        "digest": REVISION,
        "scope_digest": snapshot.scope_digest,
        "signature_is_valid": True,
    }


def test_status_reasons_are_detached_from_orchestration_and_caller(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    orchestration_result = _orchestration_result()
    analysis = orchestration_result["analysis"]
    assert isinstance(analysis, dict)
    upstream_reasons = ["unsupported-source-scope"]
    analysis["statusReasons"] = upstream_reasons
    service = RepositoryCollectionService(
        webhook_secret=SECRET,
        process_push=lambda _snapshot, _delivery: orchestration_result,
    )

    result = service.collect(_descriptor(snapshot))
    upstream_reasons.append("failed-source-scope")
    result["statusReasons"].append("caller-only-reason")

    assert result["statusReasons"] == [
        "unsupported-source-scope",
        "caller-only-reason",
    ]
    assert upstream_reasons == [
        "unsupported-source-scope",
        "failed-source-scope",
    ]


@pytest.mark.parametrize(
    "invalid_reasons",
    (
        "unsupported-source-scope",
        [1],
        ["x" * 129],
        ["source/path/disclosure"],
    ),
)
def test_status_reasons_reject_unbounded_or_non_code_values(
    tmp_path: Path, invalid_reasons: object
) -> None:
    snapshot = _snapshot(tmp_path)
    orchestration_result = _orchestration_result()
    analysis = orchestration_result["analysis"]
    assert isinstance(analysis, dict)
    analysis["statusReasons"] = invalid_reasons
    service = RepositoryCollectionService(
        webhook_secret=SECRET,
        process_push=lambda _snapshot, _delivery: orchestration_result,
    )

    with pytest.raises(RepositoryCollectionError) as captured:
        service.collect(_descriptor(snapshot))

    assert captured.value.code == "PIPELINE_FAILED"
    assert str(captured.value) == "repository collection failed"


def test_equivalent_collection_reuses_identities_without_durable_or_evidence_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: Counter[str] = Counter()
    snapshot = _snapshot(tmp_path, reads=reads)
    settings = _settings(tmp_path)
    services = build_services(settings, repository_snapshot=snapshot)
    service = services.repository_collection
    descriptor = _descriptor(snapshot)
    analysis_calls = 0
    analyze = services.orchestration._analyzer_registry.analyze

    def count_analysis(*args, **kwargs):
        nonlocal analysis_calls
        analysis_calls += 1
        return analyze(*args, **kwargs)

    monkeypatch.setattr(
        services.orchestration._analyzer_registry, "analyze", count_analysis
    )

    first = service.collect(descriptor)
    before = _application_effect_snapshot(settings)
    reads_before_duplicate = reads.copy()
    analysis_before_duplicate = analysis_calls
    assert reads_before_duplicate
    assert analysis_before_duplicate == 1
    assert before["objects"]
    database_before = before["database"]
    assert isinstance(database_before, dict)
    assert database_before["evidence_objects"]
    duplicate = service.collect(descriptor)
    after = _application_effect_snapshot(settings)

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
    assert reads == reads_before_duplicate
    assert analysis_calls == analysis_before_duplicate


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


def _application_effect_snapshot(settings: Settings) -> dict[str, object]:
    return {
        "database": _database_effect_snapshot(settings.database_path),
        "objects": _object_tree_snapshot(settings.object_directory),
    }


def _database_effect_snapshot(database_path: Path) -> dict[str, tuple[str, ...]]:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        table_names = tuple(
            str(row[0])
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_schema
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        )
        if len(table_names) > _MAX_APPLICATION_TABLES:
            raise AssertionError("application table snapshot bound exceeded")
        missing = _REQUIRED_EFFECT_TABLES.difference(table_names)
        if missing:
            raise AssertionError(
                f"required application tables missing: {sorted(missing)}"
            )

        snapshot: dict[str, tuple[str, ...]] = {}
        total_bytes = 0
        for table_name in table_names:
            rows = connection.execute(
                f"SELECT * FROM {_quoted_identifier(table_name)}"
            ).fetchall()
            if len(rows) > _MAX_ROWS_PER_TABLE:
                raise AssertionError("application table row bound exceeded")
            canonical_rows = []
            for row in rows:
                document = {
                    key: _bounded_sqlite_value(row[key]) for key in row.keys()
                }
                encoded = json.dumps(
                    document,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
                total_bytes += len(encoded)
                if total_bytes > _MAX_DATABASE_SNAPSHOT_BYTES:
                    raise AssertionError("application database snapshot bound exceeded")
                canonical_rows.append(encoded.decode())
            snapshot[table_name] = tuple(sorted(canonical_rows))
        return snapshot


def _quoted_identifier(value: str) -> str:
    encoded = value.encode()
    if not value or len(encoded) > 128 or any(byte < 32 for byte in encoded):
        raise AssertionError("application table identifier is outside bounds")
    return '"' + value.replace('"', '""') + '"'


def _bounded_sqlite_value(value: object) -> object:
    if value is None or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AssertionError("non-finite SQLite value is not canonical")
        return value
    if isinstance(value, str):
        if len(value.encode()) > _MAX_SQLITE_VALUE_BYTES:
            raise AssertionError("SQLite text value snapshot bound exceeded")
        return value
    if isinstance(value, bytes):
        if len(value) > _MAX_SQLITE_VALUE_BYTES:
            raise AssertionError("SQLite blob value snapshot bound exceeded")
        return {"sqliteBlobHex": value.hex()}
    raise AssertionError("unsupported SQLite value type")


def _object_tree_snapshot(root: Path) -> tuple[tuple[object, ...], ...]:
    if not root.exists():
        return ()
    entries = sorted(
        root.rglob("*"), key=lambda path: path.relative_to(root).as_posix()
    )
    if len(entries) > _MAX_OBJECT_ENTRIES:
        raise AssertionError("evidence object entry bound exceeded")
    result: list[tuple[object, ...]] = []
    total_bytes = 0
    for entry in entries:
        relative = entry.relative_to(root).as_posix()
        if not relative or len(relative.encode()) > _MAX_RELATIVE_PATH_BYTES:
            raise AssertionError("evidence object path bound exceeded")
        metadata = entry.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISLNK(metadata.st_mode):
            raise AssertionError("evidence object tree must not contain symlinks")
        if stat.S_ISDIR(metadata.st_mode):
            result.append((relative, "directory", mode, metadata.st_mtime_ns))
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise AssertionError("evidence object tree contains unsupported entry")
        if metadata.st_size > _MAX_OBJECT_FILE_BYTES:
            raise AssertionError("evidence object file bound exceeded")
        content = entry.read_bytes()
        total_bytes += len(content)
        if total_bytes > _MAX_OBJECT_TREE_BYTES:
            raise AssertionError("evidence object tree snapshot bound exceeded")
        result.append(
            (
                relative,
                "file",
                mode,
                len(content),
                metadata.st_mtime_ns,
                hashlib.sha256(content).hexdigest(),
            )
        )
    return tuple(result)
