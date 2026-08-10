from __future__ import annotations

import json
import hashlib
import hmac
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from lineage_api.cli import run
from lineage_api.services.analyzer_registry import AnalyzerRegistry, AnalyzerSelection


ORIGIN = "https://example.com/acme/spring-service"


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        shell=False,
        text=True,
    )
    return completed.stdout.strip()


def _checkout(
    tmp_path: Path,
    *,
    overrides: dict[str, str] | None = None,
    removed: tuple[str, ...] = (),
) -> tuple[Path, str]:
    root = tmp_path / "spring-service"
    files = {
        "pom.xml": """<project><parent><groupId>org.springframework.boot</groupId>
<artifactId>spring-boot-starter-parent</artifactId><version>4.1.0</version></parent>
<dependencies><dependency><groupId>org.springframework.boot</groupId>
<artifactId>spring-boot-starter-data-jpa</artifactId></dependency></dependencies></project>""",
        "src/main/java/example/Owner.java": """package example;
import jakarta.persistence.Entity; import jakarta.persistence.Table;
@Entity @Table(name=\"owners\") class Owner {}""",
        "src/main/java/example/OwnerRepository.java": """package example;
import org.springframework.data.jpa.repository.JpaRepository;
interface OwnerRepository extends JpaRepository<Owner,Integer> {}""",
        "src/main/java/example/OwnerService.java": """package example;
class OwnerService { private final OwnerRepository owners;
OwnerService(OwnerRepository owners) { this.owners = owners; }
Owner read(Integer id) { return owners.findById(id).orElseThrow(); }
Owner write(Owner owner) { return owners.save(owner); }}""",
        "src/main/resources/db/postgres/schema.sql": (
            "create table owners (id integer primary key);"
        ),
        "src/main/resources/db/h2/schema.sql": (
            "create table owners (id integer primary key);"
        ),
        "build.gradle": """plugins { id 'org.springframework.boot' version '4.1.0' }
dependencies { implementation 'org.springframework.boot:spring-boot-starter-data-jpa' }""",
        "src/main/resources/db/postgres/data.sql": (
            "insert into owners(id) values (1);"
        ),
        "src/test/java/example/OwnerServiceTest.java": """package example;
class OwnerServiceTest { OwnerRepository owners;
Owner testOnly(Integer id) { return owners.findById(id).orElseThrow(); }}""",
    }
    files.update(overrides or {})
    for relative in removed:
        files.pop(relative, None)
    root.mkdir()
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "lineage@example.test")
    _git(root, "config", "user.name", "Lineage Test")
    _git(root, "remote", "add", "origin", ORIGIN)
    for relative, body in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", "fixture")
    return root, _git(root, "rev-parse", "HEAD")


def _arguments(checkout: Path, revision: str, *, profile: str = "postgres") -> list[str]:
    return [
        "collect-checkout",
        "--checkout",
        str(checkout),
        "--origin",
        ORIGIN,
        "--revision",
        revision,
        "--repository",
        "spring-service",
        "--environment",
        "staging",
        "--platform",
        profile,
        "--system",
        "orders",
        "--analyzer-pack",
        "java-spring-data-jpa-v1",
        "--ruleset",
        "spring-data-rules-v1",
        "--profile",
        profile,
    ]


def test_exact_checkout_uses_durable_pipeline_and_duplicate_has_no_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checkout, revision = _checkout(tmp_path)
    data = tmp_path / "state"
    monkeypatch.setenv("LINEAGE_DATA_DIR", str(data))
    monkeypatch.setenv("LINEAGE_WEBHOOK_SECRET", "secret-marker")

    assert run(_arguments(checkout, revision)) == 0
    first_text = capsys.readouterr().out
    first = json.loads(first_text)
    assert "collectionId" not in first
    assert "statusUrl" not in first
    assert first["outcome"] == "ACCEPTED"
    assert first["runStatus"] == "IN_REVIEW"
    assert first["proposalStatus"] == "IN_REVIEW"
    assert first["runtimeStatus"] == "NOT_PROVIDED"
    assert first["counts"] == {
        "edges": 2,
        "reads": 1,
        "residue": 0,
        "unresolved": 0,
        "writes": 1,
    }
    assert first["stages"] == [
        "QUEUED",
        "CLASSIFYING",
        "ANALYZING",
        "RESOLVING",
        "STORING_EVIDENCE",
        "MERGING",
        "PROPOSING",
        "IN_REVIEW",
    ]
    assert str(checkout) not in first_text
    assert "secret-marker" not in first_text
    assert "return owners" not in first_text
    with sqlite3.connect(data / "lineage.db") as connection:
        event = json.loads(
            connection.execute(
                "SELECT payload_json FROM events"
            ).fetchone()[0]
        )
        source_scope_digest = event["repositorySource"]["scopeDispositionDigest"]
        assert source_scope_digest.startswith("sha256:")
        assert len(source_scope_digest) == 71
        coverage = json.loads(
            connection.execute(
                "SELECT payload_json FROM coverage_manifests"
            ).fetchone()[0]
        )
        assert coverage["sourceScopeDispositionDigest"] == source_scope_digest
        expected = {
            "build.gradle",
            "pom.xml",
            "src/main/java/example/Owner.java",
            "src/main/java/example/OwnerRepository.java",
            "src/main/java/example/OwnerService.java",
            "src/main/resources/db/h2/schema.sql",
            "src/main/resources/db/postgres/data.sql",
            "src/main/resources/db/postgres/schema.sql",
            "src/test/java/example/OwnerServiceTest.java",
        }
        completed = {
            "build.gradle",
            "pom.xml",
            "src/main/java/example/Owner.java",
            "src/main/java/example/OwnerRepository.java",
            "src/main/java/example/OwnerService.java",
            "src/main/resources/db/postgres/schema.sql",
        }
        skipped = expected - completed
        assert set(coverage["expectedScope"]) == expected
        assert set(coverage["completedScope"]) == completed
        assert set(coverage["skippedScope"]) == skipped
        assert coverage["unsupportedScope"] == []
        assert coverage["failedScope"] == []
        dispositions = (
            coverage["completedScope"],
            coverage["skippedScope"],
            coverage["unsupportedScope"],
            coverage["failedScope"],
        )
        assert sum(len(values) for values in dispositions) == len(expected)
        assert set().union(*(set(values) for values in dispositions)) == expected
        before_duplicate = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "events",
                "commands",
                "runs",
                "proposals",
                "edge_ledger",
                "stage_results",
            )
        }

    assert run(_arguments(checkout, revision)) == 0
    duplicate = json.loads(capsys.readouterr().out)
    assert duplicate["outcome"] == "DUPLICATE"
    assert duplicate["commandId"] == first["commandId"]
    assert duplicate["runId"] == first["runId"]
    assert duplicate["proposalId"] == first["proposalId"]
    assert duplicate["counts"] == first["counts"]
    with sqlite3.connect(data / "lineage.db") as connection:
        after_duplicate = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before_duplicate
        }
    assert after_duplicate == before_duplicate


def test_h2_profile_truthfully_returns_integration_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checkout, revision = _checkout(tmp_path)
    monkeypatch.setenv("LINEAGE_DATA_DIR", str(tmp_path / "h2-state"))

    assert run(_arguments(checkout, revision, profile="h2")) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "INTEGRATION_REQUIRED"
    assert result["proposalId"] is None
    assert result["runtimeStatus"] == "NOT_PROVIDED"
    assert result["counts"]["edges"] == 0
    assert result["counts"]["residue"] > 0

    assert run(_arguments(checkout, revision, profile="h2")) == 2
    duplicate = json.loads(capsys.readouterr().out)
    assert duplicate["outcome"] == "INTEGRATION_REQUIRED"
    assert duplicate["reasonCode"] == "INTEGRATION_REQUIRED"
    assert duplicate["commandStatus"] == "COMPLETED"
    assert duplicate["proposalId"] is None
    assert duplicate["analysisStatus"] == "INTEGRATION_REQUIRED"


def test_invalid_pack_fails_before_durable_intake_with_bounded_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checkout, revision = _checkout(tmp_path)
    data = tmp_path / "invalid-state"
    monkeypatch.setenv("LINEAGE_DATA_DIR", str(data))
    arguments = _arguments(checkout, revision)
    arguments[arguments.index("java-spring-data-jpa-v1")] = "unknown-v1"

    assert run(arguments) == 2
    text = capsys.readouterr().out
    result = json.loads(text)
    assert result == {
        "errorCode": "UNKNOWN_ANALYZER_PACK",
        "outcome": "INVALID",
    }
    assert len(text.encode()) < 512
    assert str(checkout) not in text
    assert not (data / "lineage.db").exists()


def test_source_scope_disposition_is_deterministic_and_tamper_evident(
    tmp_path: Path,
) -> None:
    from lineage_api.application.repository_sources import (
        RepositoryCheckoutDescriptor,
        RepositorySourceLimits,
    )
    from lineage_api.infrastructure.local_git_source import LocalGitRepositorySource
    from lineage_api.services.analyzer_registry import (
        AnalyzerSelectionError,
        PinnedSnapshotProvider,
        canonical_source_metadata,
    )

    checkout, revision = _checkout(tmp_path)
    snapshot = LocalGitRepositorySource(
        RepositorySourceLimits(100, 1024 * 1024, 8 * 1024 * 1024)
    ).snapshot(
        RepositoryCheckoutDescriptor(
            origin=ORIGIN,
            repository="spring-service",
            revision=revision,
            checkout_root=checkout,
            environment="staging",
            platform="postgres",
            system="orders",
            analyzer_pack="java-spring-data-jpa-v1",
            ruleset="spring-data-rules-v1",
        )
    )

    metadata = canonical_source_metadata(snapshot, schema_profile="postgres")
    assert metadata == canonical_source_metadata(snapshot, schema_profile="postgres")
    assert metadata["scopeDispositionDigest"].startswith("sha256:")
    envelope = {
        "repo": snapshot.repository,
        "digest": snapshot.revision,
        "env": snapshot.environment,
        "system": snapshot.system,
        "changedFiles": list(snapshot.paths),
        "repositorySource": metadata,
    }
    assert PinnedSnapshotProvider(snapshot).resolve(envelope) is snapshot

    envelope["repositorySource"] = {
        **metadata,
        "scopeDispositionDigest": "sha256:" + "0" * 64,
    }
    with pytest.raises(AnalyzerSelectionError) as captured:
        PinnedSnapshotProvider(snapshot).resolve(envelope)
    assert captured.value.code == "SOURCE_DETERMINANT_MISMATCH"


def test_relevant_java_outside_production_scope_requires_integration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checkout, revision = _checkout(
        tmp_path,
        overrides={"legacy/LegacyRepository.java": "interface LegacyRepository {}"},
    )
    data = tmp_path / "unsupported-java"
    monkeypatch.setenv("LINEAGE_DATA_DIR", str(data))

    assert run(_arguments(checkout, revision)) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "INTEGRATION_REQUIRED"
    assert result["proposalId"] is None
    assert "unsupported-source-scope" in result["statusReasons"]
    assert result["coverageManifest"]["state"] == "INCOMPLETE"
    assert set(result["coverageManifest"]) == {
        "manifestId",
        "state",
        "determinantDigest",
        "sourceScopeDispositionDigest",
        "counts",
    }
    with sqlite3.connect(data / "lineage.db") as connection:
        row = connection.execute(
            "SELECT payload_json FROM coverage_manifests"
        ).fetchone()
        assert row is not None
        first_coverage = json.loads(row[0])
        assert first_coverage["state"] == "INCOMPLETE"
        assert first_coverage["unsupportedScope"] == [
            "legacy/LegacyRepository.java"
        ]
        assert first_coverage["failedScope"] == []
        dispositions = (
            first_coverage["completedScope"],
            first_coverage["skippedScope"],
            first_coverage["unsupportedScope"],
            first_coverage["failedScope"],
        )
        assert sum(len(values) for values in dispositions) == len(
            first_coverage["expectedScope"]
        )
        assert set().union(*(set(values) for values in dispositions)) == set(
            first_coverage["expectedScope"]
        )
        assert result["coverageManifest"] == {
            "manifestId": first_coverage["manifestId"],
            "state": "INCOMPLETE",
            "determinantDigest": first_coverage["determinantDigest"],
            "sourceScopeDispositionDigest": first_coverage[
                "sourceScopeDispositionDigest"
            ],
            "counts": {
                "expected": len(first_coverage["expectedScope"]),
                "completed": len(first_coverage["completedScope"]),
                "skipped": len(first_coverage["skippedScope"]),
                "unsupported": 1,
                "failed": 0,
            },
        }

    assert run(_arguments(checkout, revision)) == 2
    duplicate = json.loads(capsys.readouterr().out)
    assert duplicate["outcome"] == "INTEGRATION_REQUIRED"
    assert duplicate["coverageManifest"] == result["coverageManifest"]
    with sqlite3.connect(data / "lineage.db") as connection:
        rows = connection.execute(
            "SELECT payload_json FROM coverage_manifests"
        ).fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][0]) == first_coverage


def test_conflicting_root_build_cells_require_integration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checkout, revision = _checkout(
        tmp_path,
        overrides={
            "build.gradle": """plugins { id 'org.springframework.boot' version '3.5.5' }
dependencies { implementation 'org.springframework.boot:spring-boot-starter-data-jpa' }"""
        },
    )
    monkeypatch.setenv("LINEAGE_DATA_DIR", str(tmp_path / "conflict-state"))

    assert run(_arguments(checkout, revision)) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "INTEGRATION_REQUIRED"
    assert "ambiguous-framework-evidence" in result["statusReasons"]


@pytest.mark.parametrize(
    "untrusted_path",
    [
        "src/test/resources/db/postgres/schema.sql",
        "test/fixtures/db/postgres/schema.sql",
        "scripts/setup/db/postgres/schema.sql",
        "user/db/postgres/schema.sql",
    ],
)
def test_untrusted_profile_schema_paths_cannot_authorize_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    untrusted_path: str,
) -> None:
    checkout, revision = _checkout(
        tmp_path,
        overrides={
            untrusted_path: "create table owners (id integer primary key);"
        },
        removed=("src/main/resources/db/postgres/schema.sql",),
    )
    monkeypatch.setenv("LINEAGE_DATA_DIR", str(tmp_path / "untrusted-schema"))

    assert run(_arguments(checkout, revision)) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "INTEGRATION_REQUIRED"
    assert result["counts"]["edges"] == 0
    assert "missing-profile-schema" in result["statusReasons"]


def test_exact_production_schema_path_is_selected_while_alternates_are_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checkout, revision = _checkout(
        tmp_path,
        overrides={
            "module/src/main/resources/db/postgres/schema.sql": (
                "create table ignored_module_table (id integer primary key);"
            ),
            "src/test/resources/db/postgres/schema.sql": (
                "create table ignored_test_table (id integer primary key);"
            ),
        },
    )
    monkeypatch.setenv("LINEAGE_DATA_DIR", str(tmp_path / "trusted-schema"))

    assert run(_arguments(checkout, revision)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "ACCEPTED"
    assert result["counts"] == {
        "edges": 2,
        "reads": 1,
        "residue": 0,
        "unresolved": 0,
        "writes": 1,
    }


def test_duplicate_trusted_schema_candidates_remain_ambiguous() -> None:
    sources = {
        "pom.xml": b"""<project><parent><groupId>org.springframework.boot</groupId>
<artifactId>spring-boot-starter-parent</artifactId><version>4.1.0</version></parent>
<dependencies><dependency><groupId>org.springframework.boot</groupId>
<artifactId>spring-boot-starter-data-jpa</artifactId></dependency></dependencies></project>""",
        "src/main/resources/db/postgres/schema.sql": (
            b"create table owners (id integer primary key);"
        ),
    }
    snapshot = SimpleNamespace(
        origin=ORIGIN,
        repository="spring-service",
        revision="a" * 40,
        scope_digest="sha256:" + "b" * 64,
        environment="staging",
        platform="postgres",
        system="orders",
        analyzer_pack="java-spring-data-jpa-v1",
        ruleset="spring-data-rules-v1",
        paths=(
            "pom.xml",
            "src/main/resources/db/postgres/schema.sql",
            "src/main/resources/db/postgres/schema.sql",
        ),
        read_bytes=lambda path: sources[path],
    )

    result = AnalyzerRegistry.default().analyze(
        snapshot,
        AnalyzerSelection(
            analyzer_pack="java-spring-data-jpa-v1",
            ruleset="spring-data-rules-v1",
            source_kind="git-checkout",
            framework="spring-data-jpa",
            schema_profile="postgres",
        ),
        "run-ambiguous-schema",
        "correlation-ambiguous-schema",
    )

    assert result.status == "INTEGRATION_REQUIRED"
    assert "ambiguous-profile-schema" in result.status_reasons


def test_failure_after_sca_checkpoint_resumes_without_reanalysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lineage_api.application.repository_sources import (
        RepositoryCheckoutDescriptor,
        RepositorySourceLimits,
    )
    from lineage_api.config import Settings
    from lineage_api.dependencies import build_services
    from lineage_api.infrastructure.local_git_source import LocalGitRepositorySource
    from lineage_api.services.analyzer_registry import (
        canonical_source_metadata,
        deterministic_checkout_event_id,
    )
    from lineage_api.services.intake import PushDelivery

    checkout, revision = _checkout(tmp_path)
    monkeypatch.setenv("LINEAGE_DATA_DIR", str(tmp_path / "retry-state"))
    monkeypatch.setenv("LINEAGE_WEBHOOK_SECRET", "retry-secret")
    snapshot = LocalGitRepositorySource(
        RepositorySourceLimits(100, 1024 * 1024, 8 * 1024 * 1024)
    ).snapshot(
        RepositoryCheckoutDescriptor(
            origin=ORIGIN,
            repository="spring-service",
            revision=revision,
            checkout_root=checkout,
            environment="staging",
            platform="postgres",
            system="orders",
            analyzer_pack="java-spring-data-jpa-v1",
            ruleset="spring-data-rules-v1",
        )
    )
    metadata = canonical_source_metadata(snapshot, schema_profile="postgres")
    payload = {
        "eventId": deterministic_checkout_event_id(
            metadata,
            snapshot.repository,
            snapshot.environment,
            snapshot.system,
        ),
        "eventType": "repo.push",
        "repo": snapshot.repository,
        "digest": snapshot.revision,
        "env": snapshot.environment,
        "system": snapshot.system,
        "changedFiles": list(snapshot.paths),
        "repositorySource": metadata,
        "receivedAt": "2026-08-09T12:00:00Z",
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    delivery = PushDelivery(
        payload,
        "sha256=" + hmac.new(b"retry-secret", body, hashlib.sha256).hexdigest(),
    )
    services = build_services(
        Settings.from_environment(), repository_snapshot=snapshot
    )
    failed_once = False

    def fail_after_i5(stage: str) -> None:
        nonlocal failed_once
        if stage == "I5" and not failed_once:
            failed_once = True
            raise RuntimeError("injected after I5")

    services.orchestration.set_fault_injector(fail_after_i5)
    with pytest.raises(RuntimeError, match="injected after I5"):
        services.orchestration.process_push(delivery)

    restarted = build_services(
        Settings.from_environment(), repository_snapshot=snapshot
    )
    recovered = restarted.orchestration.process_push(delivery)

    assert recovered["outcome"] == "DUPLICATE"
    assert recovered["command"]["status"] == "COMPLETED"
    assert recovered["proposal"] is not None
    assert recovered["run"]["state"] == "IN_REVIEW"
    assert recovered["runtimeStatus"] == "NOT_PROVIDED"
    assert recovered["resume"]["reusedStages"] == ["I1", "I2", "I3", "I4", "I5"]
    with restarted.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM edge_ledger").fetchone()[0] == 2
