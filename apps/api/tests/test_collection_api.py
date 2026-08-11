from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lineage_api.application.collections import (
    CollectionError,
    CollectionService,
    parse_collection_submission,
)
from lineage_api.application.product_api import ProductApiError, ProductApiService
from lineage_api.application.repository_acquisition import (
    GitRepositoryRequest,
    LocalCheckoutRequest,
)
from lineage_api.config import Settings
from lineage_api.entrypoints.aws import product_api as aws_product_api
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


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        shell=False,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _checkout(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "checkout"
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


def _client(tmp_path: Path, *, allow_local: bool = True) -> TestClient:
    return TestClient(create_app(_settings(tmp_path, allow_local=allow_local)))


# --- environment-neutral submission parsing -------------------------------------------------


def test_parses_both_discriminated_source_variants() -> None:
    local = parse_collection_submission(
        _submission("a" * 40, Path("/tmp/checkout")), allow_local_sources=True
    )
    remote_body = {
        key: value
        for key, value in _submission("b" * 40, Path("/tmp/checkout")).items()
        if key != "checkoutPath"
    }
    remote_body["sourceType"] = "GIT"
    remote_body["origin"] = "https://github.com/acme/demo"
    remote_body["repository"] = "demo"
    remote = parse_collection_submission(remote_body, allow_local_sources=False)

    assert isinstance(local, LocalCheckoutRequest)
    assert local.source_type == "LOCAL_CHECKOUT"
    assert isinstance(remote, GitRepositoryRequest)
    assert remote.source_type == "GIT"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ({"unexpected": "value"}, "INVALID_REQUEST"),
        ({"revision": "HEAD"}, "INVALID_REQUEST"),
        ({"revision": "A" * 40}, "INVALID_REQUEST"),
        ({"sourceType": "FTP"}, "INVALID_REQUEST"),
        ({"origin": "http://example.com/acme/demo"}, "INVALID_REQUEST"),
        ({"origin": "https://user:token@example.com/acme/demo"}, "INVALID_REQUEST"),
        ({"repository": "x" * 200}, "INVALID_REQUEST"),
        ({"environment": " staging"}, "INVALID_REQUEST"),
    ],
)
def test_rejects_malformed_submissions_with_stable_codes(
    mutation: dict[str, Any], code: str
) -> None:
    body = _submission("c" * 40, Path("/tmp/checkout")) | mutation

    with pytest.raises(CollectionError) as caught:
        parse_collection_submission(body, allow_local_sources=True)

    assert caught.value.code == code
    assert caught.value.status_code == 400


def test_rejects_missing_required_fields() -> None:
    body = _submission("d" * 40, Path("/tmp/checkout"))
    del body["system"]

    with pytest.raises(CollectionError) as caught:
        parse_collection_submission(body, allow_local_sources=True)

    assert "system" in str(caught.value)


def test_local_sources_are_refused_under_production_policy() -> None:
    with pytest.raises(CollectionError) as caught:
        parse_collection_submission(
            _submission("e" * 40, Path("/tmp/checkout")), allow_local_sources=False
        )

    assert caught.value.code == "LOCAL_SOURCE_DISABLED"
    assert caught.value.status_code == 403


def test_git_sources_may_not_carry_a_checkout_path() -> None:
    body = _submission("f" * 40, Path("/tmp/checkout"))
    body["sourceType"] = "GIT"
    body["origin"] = "https://github.com/acme/demo"

    with pytest.raises(CollectionError) as caught:
        parse_collection_submission(body, allow_local_sources=True)

    assert caught.value.code == "INVALID_REQUEST"
    assert "checkoutPath" in str(caught.value)


# --- FastAPI contract -----------------------------------------------------------------------


def test_submit_returns_202_with_a_location_and_a_queryable_status(tmp_path: Path) -> None:
    checkout_root, revision = _checkout(tmp_path)
    client = _client(tmp_path)

    accepted = client.post("/api/collections", json=_submission(revision, checkout_root))

    assert accepted.status_code == 202
    document = accepted.json()
    command_id = document["commandId"]
    assert accepted.headers["location"] == f"/api/collections/{command_id}"
    assert document["statusUrl"] == f"/api/collections/{command_id}"
    assert document["revision"] == revision
    assert document["sourceType"] == "LOCAL_CHECKOUT"
    assert document["runtimeStatus"] == "NOT_PROVIDED"

    status = client.get(f"/api/collections/{command_id}")

    assert status.status_code == 200
    assert status.json() == document


def test_duplicate_submissions_return_the_same_durable_identities(tmp_path: Path) -> None:
    checkout_root, revision = _checkout(tmp_path)
    client = _client(tmp_path)
    body = _submission(revision, checkout_root)

    first = client.post("/api/collections", json=body).json()
    second = client.post("/api/collections", json=body).json()

    for key in ("commandId", "collectionId", "runId", "proposalId", "determinantDigest"):
        assert first[key] == second[key], key
    assert first["scopeDigest"] == second["scopeDigest"]


def test_status_of_an_unknown_command_is_a_stable_not_found(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/collections/command-does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "COLLECTION_NOT_FOUND"
    assert body["correlationId"]


def test_production_rejects_local_checkout_submissions(tmp_path: Path) -> None:
    checkout_root, revision = _checkout(tmp_path)
    client = _client(tmp_path, allow_local=False)

    response = client.post("/api/collections", json=_submission(revision, checkout_root))

    assert response.status_code == 403
    assert response.json()["code"] == "LOCAL_SOURCE_DISABLED"


def test_responses_never_expose_paths_credentials_or_stack_traces(tmp_path: Path) -> None:
    checkout_root, revision = _checkout(tmp_path)
    client = _client(tmp_path)

    accepted = client.post("/api/collections", json=_submission(revision, checkout_root))
    rendered = accepted.text

    assert str(checkout_root) not in rendered
    assert "checkoutPath" not in rendered
    assert "Traceback" not in rendered
    assert "/private/var" not in rendered
    assert "/tmp" not in rendered


def test_unknown_fields_and_bad_revisions_are_rejected_by_the_typed_route(
    tmp_path: Path,
) -> None:
    checkout_root, revision = _checkout(tmp_path)
    client = _client(tmp_path)

    unexpected = client.post(
        "/api/collections",
        json=_submission(revision, checkout_root) | {"unexpected": "value"},
    )
    mutable_revision = client.post(
        "/api/collections", json=_submission(revision, checkout_root) | {"revision": "main"}
    )

    assert unexpected.status_code == 422
    assert unexpected.json()["code"] == "INVALID_REQUEST"
    assert mutable_revision.status_code == 422


def test_git_submissions_that_cannot_be_acquired_fail_closed(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = {
        key: value
        for key, value in _submission("a" * 40, tmp_path).items()
        if key != "checkoutPath"
    }
    body["sourceType"] = "GIT"
    body["origin"] = "https://lineage-collector-invalid.example/acme/demo"
    body["repository"] = "demo"

    response = client.post("/api/collections", json=body)

    assert response.status_code == 502
    document = response.json()
    assert document["code"] == "SOURCE_ACQUISITION_FAILED"
    assert "lineage-collector-invalid.example" not in document["message"]


# --- environment-neutral product API parity ---------------------------------------------------


class _RecordingPort:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def submit_collection(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("submit_collection", kwargs))
        return {"commandId": "cmd-1", "statusUrl": "/api/collections/cmd-1"}

    def get_collection(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_collection", kwargs))
        return {"commandId": kwargs["command_id"], "statusUrl": "/api/collections/cmd-1"}


def _handle(port: _RecordingPort, **overrides: Any):
    request = {
        "method": "POST",
        "path": "/api/collections",
        "query": {},
        "body": {"sourceType": "GIT"},
        "principal": "arn:aws:iam::111111111111:role/lineage-writer",
        "correlation_id": "corr-1",
    }
    request.update(overrides)
    return ProductApiService(port).handle(**request)


def test_product_api_submit_returns_202_and_a_location_header() -> None:
    port = _RecordingPort()

    result = _handle(port)

    assert result.status_code == 202
    assert result.headers["location"] == "/api/collections/cmd-1"
    assert port.calls[0][0] == "submit_collection"
    assert port.calls[0][1]["body"] == {"sourceType": "GIT"}
    assert port.calls[0][1]["correlation_id"] == "corr-1"


def test_product_api_status_route_is_bounded_and_returns_200() -> None:
    port = _RecordingPort()

    result = _handle(port, method="GET", path="/api/collections/cmd-1", body=None)

    assert result.status_code == 200
    assert result.headers == {}
    assert port.calls[0][1]["command_id"] == "cmd-1"


def test_product_api_rejects_unsupported_collection_query_parameters() -> None:
    with pytest.raises(ProductApiError):
        _handle(_RecordingPort(), query={"force": "true"})


def test_product_api_requires_a_submission_body() -> None:
    with pytest.raises(ProductApiError):
        _handle(_RecordingPort(), body=None)


def test_aws_entrypoint_propagates_the_accepted_status_and_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _RecordingPort()
    service = ProductApiService(port)
    monkeypatch.setattr(aws_product_api, "service_factory", lambda: service)

    response = aws_product_api.handler(
        {
            "httpMethod": "POST",
            "path": "/api/collections",
            "headers": {"X-Correlation-Id": "corr-aws-1"},
            "queryStringParameters": None,
            "body": json.dumps({"sourceType": "GIT"}),
            "isBase64Encoded": False,
            "requestContext": {
                "requestId": "request-1",
                "identity": {"userArn": "arn:aws:iam::111111111111:role/lineage-writer"},
            },
        },
        object(),
    )

    assert response["statusCode"] == 202
    assert response["headers"]["location"] == "/api/collections/cmd-1"
    assert response["headers"]["x-correlation-id"] == "corr-aws-1"
    assert json.loads(response["body"])["commandId"] == "cmd-1"


def test_aws_entrypoint_status_route_returns_the_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        aws_product_api, "service_factory", lambda: ProductApiService(_RecordingPort())
    )

    response = aws_product_api.handler(
        {
            "httpMethod": "GET",
            "path": "/api/collections/cmd-1",
            "headers": {"X-Correlation-Id": "corr-aws-2"},
            "queryStringParameters": None,
            "body": None,
            "isBase64Encoded": False,
            "requestContext": {
                "requestId": "request-2",
                "identity": {"userArn": "arn:aws:iam::111111111111:role/lineage-reader"},
            },
        },
        object(),
    )

    assert response["statusCode"] == 200
    assert "location" not in response["headers"]
    assert json.loads(response["body"])["commandId"] == "cmd-1"


def test_collection_service_requires_a_boolean_source_policy() -> None:
    with pytest.raises(TypeError):
        CollectionService(
            acquisition=object(),  # type: ignore[arg-type]
            store=object(),  # type: ignore[arg-type]
            allow_local_sources="yes",  # type: ignore[arg-type]
        )
