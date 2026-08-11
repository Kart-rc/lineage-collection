from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest

from lineage_api.application import repository_acquisition, repository_collection
from lineage_api.application import repository_sources
from lineage_api.application.repository_acquisition import (
    GitRepositoryRequest,
    LocalCheckoutRequest,
    RepositoryAcquisitionError,
    RepositoryAcquisitionService,
)
from lineage_api.config import Settings
from lineage_api.dependencies import build_repository_acquisition_service


ORIGIN = "https://example.com/acme/demo"


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        shell=False,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


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
    "src/main/resources/db/h2/schema.sql": (
        "create table owners (id integer primary key);"
    ),
}


def _checkout(tmp_path: Path, *, name: str = "checkout") -> tuple[Path, str]:
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "lineage@example.com")
    _git(root, "config", "user.name", "Lineage Test")
    _git(root, "remote", "add", "origin", ORIGIN)
    for relative, body in SPRING_FIXTURE.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", "fixture")
    return root.resolve(strict=True), _git(root, "rev-parse", "HEAD")


def _settings(tmp_path: Path, *, allow_local: bool) -> Settings:
    root = Path(__file__).resolve().parents[3]
    data_directory = tmp_path / "data"
    return Settings(
        project_root=root,
        data_directory=data_directory,
        fixture_directory=root / "fixtures",
        database_path=data_directory / "lineage.db",
        object_directory=data_directory / "objects",
        webhook_secret="test-secret",
        allow_local_repository_sources=allow_local,
    )


def _request(revision: str, checkout_root: Path) -> LocalCheckoutRequest:
    return LocalCheckoutRequest(
        origin=ORIGIN,
        repository="demo",
        revision=revision,
        environment="staging",
        platform="postgres",
        system="payments",
        analyzer_pack="java-spring-data-jpa-v1",
        ruleset="spring-data-rules-v1",
        schema_profile="postgres",
        checkout_root=checkout_root,
    )


def test_application_layer_never_imports_concrete_infrastructure() -> None:
    for module in (repository_acquisition, repository_collection, repository_sources):
        source = inspect.getsource(module)
        assert "lineage_api.infrastructure" not in source, (
            f"{module.__name__} must not import concrete infrastructure"
        )


def test_wiring_returns_an_acquisition_service_for_both_source_modes(
    tmp_path: Path,
) -> None:
    service = build_repository_acquisition_service(_settings(tmp_path, allow_local=True))

    assert isinstance(service, RepositoryAcquisitionService)


def test_wired_local_source_collects_an_exact_checkout(tmp_path: Path) -> None:
    checkout_root, revision = _checkout(tmp_path)
    service = build_repository_acquisition_service(_settings(tmp_path, allow_local=True))

    result = service.collect(_request(revision, checkout_root))

    assert result["outcome"] in {"ACCEPTED", "DUPLICATE", "REUSED"}
    assert result["commandId"]
    assert result["revision"] == revision
    assert result["scopeDigest"].startswith("sha256:")


def test_wired_local_source_is_fail_closed_under_production_policy(tmp_path: Path) -> None:
    checkout_root, revision = _checkout(tmp_path)
    service = build_repository_acquisition_service(_settings(tmp_path, allow_local=False))

    with pytest.raises(RepositoryAcquisitionError) as caught:
        service.collect(_request(revision, checkout_root))

    assert caught.value.code == "LOCAL_SOURCE_DISABLED"


def test_wired_remote_source_fails_closed_without_leaking_details(tmp_path: Path) -> None:
    service = build_repository_acquisition_service(_settings(tmp_path, allow_local=False))

    with pytest.raises(RepositoryAcquisitionError) as caught:
        service.collect(
            GitRepositoryRequest(
                origin="https://lineage-collector-invalid.example/acme/demo",
                repository="demo",
                revision="a" * 40,
                environment="staging",
                platform="local",
                system="payments",
                analyzer_pack="python-demo-v1",
                ruleset="python-demo-v1",
                schema_profile="local",
            )
        )

    assert caught.value.code == "SOURCE_ACQUISITION_FAILED"
    message = str(caught.value)
    assert "lineage-collector-invalid.example" not in message
    assert len(message) <= 160
