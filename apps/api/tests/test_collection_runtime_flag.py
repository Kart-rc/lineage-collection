"""Runtime verification exposed on the product path (CLI + POST /api/collections).

The library-level ``runtime_execution=True`` capability exists on
``RepositoryCollectionService.collect``; these tests pin the contract that the flag is
reachable from every product surface through the shared submission parser, and that the
default remains byte-identical runtime-off.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lineage_api.application.collections import (
    CollectionError,
    parse_collection_submission,
)
from lineage_api.application.repository_acquisition import (
    GitRepositoryRequest,
    LocalCheckoutRequest,
    RepositoryAcquisitionService,
    RepositoryCollectionDescriptor,
)
from lineage_api.application.repository_sources import (
    RepositoryCheckoutDescriptor,
    RepositorySnapshot,
)
from lineage_api.config import Settings
from lineage_api.main import create_app


ORIGIN = "https://example.com/acme/demo"
SPRING_FIXTURE = {
    "pom.xml": """<project><parent><groupId>org.springframework.boot</groupId>
<artifactId>spring-boot-starter-parent</artifactId><version>4.1.0</version></parent>
<dependencies><dependency><groupId>org.springframework.boot</groupId>
<artifactId>spring-boot-starter-data-jpa</artifactId></dependency></dependencies></project>""",
    "src/main/java/example/Owner.java": """package example;
import jakarta.persistence.Entity; import jakarta.persistence.Table;
@Entity @Table(name="owners") class Owner {}""",
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
}


JAVA_RUNTIME_FIXTURE = (
    Path(__file__).resolve().parents[3] / "fixtures" / "repositories" / "java-petclinic-postgres"
)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        shell=False,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _checkout(
    tmp_path: Path, files: dict[str, str] | None = None
) -> tuple[Path, str]:
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "lineage@example.com")
    _git(root, "config", "user.name", "Lineage Test")
    _git(root, "remote", "add", "origin", ORIGIN)
    for relative, body in (files or SPRING_FIXTURE).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", "fixture")
    return root.resolve(strict=True), _git(root, "rev-parse", "HEAD")


def _java_runtime_fixture_files() -> dict[str, str]:
    """The checked-in runtime-corroborating Java fixture, as checkout content.

    ``test_collection_runtime_acceptance`` proves this exact repository shape reaches
    CORROBORATED through ``collect(..., runtime_execution=True)``; reusing it here
    proves the same outcome is reachable through the product API flag.
    """

    return {
        item.relative_to(JAVA_RUNTIME_FIXTURE).as_posix(): item.read_text(encoding="utf-8")
        for item in sorted(JAVA_RUNTIME_FIXTURE.rglob("*"))
        if item.is_file()
    }


def _settings(tmp_path: Path) -> Settings:
    root = Path(__file__).resolve().parents[3]
    data_directory = tmp_path / "data"
    return Settings(
        project_root=root,
        data_directory=data_directory,
        fixture_directory=root / "fixtures",
        database_path=data_directory / "lineage.db",
        object_directory=data_directory / "objects",
        webhook_secret="test-secret",
        allow_local_repository_sources=True,
    )


def _submission(revision: str, checkout_root: Path) -> dict[str, Any]:
    return {
        "sourceType": "LOCAL_CHECKOUT",
        "origin": ORIGIN,
        "repository": "demo",
        "revision": revision,
        "environment": "staging",
        "platform": "postgres",
        "system": "payments",
        "analyzerPack": "java-spring-data-jpa-v1",
        "ruleset": "spring-data-rules-v1",
        "schemaProfile": "postgres",
        "checkoutPath": str(checkout_root),
    }


# --- shared submission parser ---------------------------------------------------------------


def test_parser_accepts_the_runtime_verification_flag_on_both_source_modes() -> None:
    local = parse_collection_submission(
        _submission("a" * 40, Path("/tmp/checkout")) | {"runtimeVerification": True},
        allow_local_sources=True,
    )
    remote_body = {
        key: value
        for key, value in _submission("b" * 40, Path("/tmp/checkout")).items()
        if key != "checkoutPath"
    }
    remote_body |= {
        "sourceType": "GIT",
        "origin": "https://github.com/acme/demo",
        "runtimeVerification": True,
    }
    remote = parse_collection_submission(remote_body, allow_local_sources=False)

    assert isinstance(local, LocalCheckoutRequest)
    assert local.runtime_verification is True
    assert isinstance(remote, GitRepositoryRequest)
    assert remote.runtime_verification is True


def test_parser_defaults_runtime_verification_off() -> None:
    request = parse_collection_submission(
        _submission("c" * 40, Path("/tmp/checkout")), allow_local_sources=True
    )

    assert request.runtime_verification is False


@pytest.mark.parametrize("value", ("true", 1, 0, None, [True]))
def test_parser_rejects_non_boolean_runtime_verification(value: object) -> None:
    body = _submission("d" * 40, Path("/tmp/checkout")) | {"runtimeVerification": value}

    with pytest.raises(CollectionError) as caught:
        parse_collection_submission(body, allow_local_sources=True)

    assert caught.value.code == "INVALID_REQUEST"
    assert caught.value.status_code == 400


# --- acquisition forwards the flag to the durable collection --------------------------------


def _request_fields() -> dict[str, str]:
    return {
        "origin": "https://github.com/acme/demo",
        "repository": "demo",
        "revision": "e" * 40,
        "environment": "staging",
        "platform": "postgres",
        "system": "payments",
        "analyzer_pack": "java-spring-data-jpa-v1",
        "ruleset": "spring-data-rules-v1",
        "schema_profile": "postgres",
    }


@pytest.mark.parametrize("requested", (True, False))
def test_acquisition_forwards_runtime_verification_to_the_collection(
    tmp_path: Path, requested: bool
) -> None:
    request = GitRepositoryRequest(**_request_fields(), runtime_verification=requested)
    snapshot = RepositorySnapshot(
        descriptor=RepositoryCheckoutDescriptor(
            origin=request.origin,
            repository=request.repository,
            revision=request.revision,
            checkout_root=(tmp_path / "checkout").resolve(),
            environment=request.environment,
            platform=request.platform,
            system=request.system,
            analyzer_pack=request.analyzer_pack,
            ruleset=request.ruleset,
        ),
        paths=("pom.xml",),
        scope_digest="sha256:" + "0" * 64,
        _reader=lambda _path: b"<project/>",
    )
    observed: list[bool] = []

    def collect(
        descriptor: RepositoryCollectionDescriptor, *, runtime_execution: bool = False
    ) -> dict[str, object]:
        observed.append(runtime_execution)
        return {"outcome": "ACCEPTED", "commandId": "command-runtime-flag"}

    service = RepositoryAcquisitionService(
        allow_local_repository_sources=False,
        local_snapshot_provider=lambda _request: pytest.fail("local provider called"),
        remote_snapshot_provider=lambda _request: snapshot,
        collect=collect,
    )

    result = service.collect(request)

    assert result["outcome"] == "ACCEPTED"
    assert observed == [requested]


# --- FastAPI product surface ----------------------------------------------------------------


def test_api_submission_with_runtime_verification_corroborates_the_collection(
    tmp_path: Path,
) -> None:
    checkout_root, revision = _checkout(tmp_path, _java_runtime_fixture_files())
    client = TestClient(create_app(_settings(tmp_path)))

    accepted = client.post(
        "/api/collections",
        json=_submission(revision, checkout_root) | {"runtimeVerification": True},
    )

    assert accepted.status_code == 202
    document = accepted.json()
    assert document["runtimeVerification"] is True
    assert document["runtimeStatus"] == "CORROBORATED"
    assert document["runtimeReasons"] == []

    status = client.get(f"/api/collections/{document['commandId']}").json()
    assert status["runtimeVerification"] is True
    assert status["runtimeStatus"] == "CORROBORATED"


def test_api_submission_without_the_flag_keeps_runtime_off(tmp_path: Path) -> None:
    checkout_root, revision = _checkout(tmp_path)
    client = TestClient(create_app(_settings(tmp_path)))

    accepted = client.post("/api/collections", json=_submission(revision, checkout_root))

    assert accepted.status_code == 202
    document = accepted.json()
    assert document["runtimeVerification"] is False
    assert document["runtimeStatus"] == "NOT_PROVIDED"
    assert document["runtimeReasons"] == ["not-requested"]


# --- CLI product surface --------------------------------------------------------------------


class _RecordingCollection:
    def __init__(self) -> None:
        self.runtime_flags: list[bool] = []

    def collect(
        self, descriptor: object, *, runtime_execution: bool = False
    ) -> dict[str, object]:
        self.runtime_flags.append(runtime_execution)
        return {"outcome": "ACCEPTED", "commandId": "command-cli-runtime"}


@pytest.mark.parametrize("flagged", (True, False))
def test_cli_collect_checkout_exposes_runtime_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flagged: bool,
) -> None:
    from lineage_api.cli import run

    checkout_root, revision = _checkout(tmp_path)
    recording = _RecordingCollection()
    monkeypatch.setattr(
        "lineage_api.cli.build_repository_collection_service", lambda _settings: recording
    )
    monkeypatch.setattr(
        "lineage_api.cli.Settings.from_environment", staticmethod(lambda: object())
    )
    arguments = [
        "collect-checkout",
        "--checkout",
        str(checkout_root),
        "--origin",
        ORIGIN,
        "--revision",
        revision,
        "--repository",
        "demo",
        "--environment",
        "staging",
        "--platform",
        "postgres",
        "--system",
        "payments",
        "--analyzer-pack",
        "java-spring-data-jpa-v1",
        "--ruleset",
        "spring-data-rules-v1",
        "--profile",
        "postgres",
    ]
    if flagged:
        arguments.append("--runtime-verification")

    assert run(arguments) == 0
    assert recording.runtime_flags == [flagged]
    assert json.loads(capsys.readouterr().out)["outcome"] == "ACCEPTED"
