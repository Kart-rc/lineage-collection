"""Prove the pinned Petclinic repository through the real product collection API.

This drives `POST /api/collections` and `GET /api/collections/{commandId}` against an
exact local checkout — not a seeded fixture — so the product contract, the durable
workflow, and the retained evidence are all exercised by one path.

Opt in by pointing at a checkout of the pinned revision:

    LINEAGE_REAL_REPOSITORY_CHECKOUT=/path/to/spring-petclinic \\
      uv run --project apps/api --extra dev pytest \\
      tests/integration/test_repository_collection_product_flow.py -q
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lineage_api.config import Settings
from lineage_api.main import create_app


ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "https://github.com/spring-projects/spring-petclinic"
REVISION = "88e37c15cf6fc8490b01bc3e8e2c800cec1ac272"

# The established Petclinic oracle records 8 residue entries, all
# "ignored-schema-statement" (tests/integration/test_spring_petclinic_repository.py).
# 23 edges = 15 dataset-scope + 8 element-scope: since element-grounded Java SCA
# edges, a proven column claim emits its own `dataset#column` edge alongside the
# dataset-scope edge for the same repository call.
EXPECTED_COUNTS = {"edges": 23, "reads": 18, "writes": 5, "residue": 8, "unresolved": 0}
EXPECTED_COVERAGE = {
    "expected": 131,
    "completed": 33,
    "skipped": 98,
    "unsupported": 0,
    "failed": 0,
}

# Anything that must never reach a user-facing response or retained evidence.
FORBIDDEN_FRAGMENTS = ("checkoutPath", "Traceback", "-----BEGIN", "password", "token=")


def _checkout() -> Path:
    value = os.environ.get("LINEAGE_REAL_REPOSITORY_CHECKOUT")
    if not value:
        pytest.skip(
            "set LINEAGE_REAL_REPOSITORY_CHECKOUT to a checkout of the pinned revision"
        )
    root = Path(value)
    if root.is_symlink():
        pytest.skip("the configured checkout must not be a symlink")
    try:
        resolved = root.resolve(strict=True)
    except OSError:
        pytest.skip("the configured checkout is not accessible")
    if not resolved.is_dir():
        pytest.skip("the configured checkout is not a directory")
    head = subprocess.run(
        ["git", "-C", str(resolved), "rev-parse", "--verify", "HEAD^{commit}"],
        shell=False,
        check=False,
        capture_output=True,
        text=True,
    )
    if head.returncode != 0 or head.stdout.strip() != REVISION:
        pytest.skip(f"the configured checkout is not pinned at {REVISION}")
    return resolved


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    data_directory = tmp_path / "data"
    settings = Settings(
        project_root=ROOT,
        data_directory=data_directory,
        fixture_directory=ROOT / "fixtures",
        database_path=data_directory / "lineage.db",
        object_directory=data_directory / "objects",
        webhook_secret="product-flow-secret",
        allow_local_repository_sources=True,
    )
    with TestClient(create_app(settings)) as configured:
        configured.lineage_settings = settings  # type: ignore[attr-defined]
        yield configured


def _submission(checkout: Path) -> dict[str, Any]:
    return {
        "sourceType": "LOCAL_CHECKOUT",
        "origin": ORIGIN,
        "repository": "spring-petclinic",
        "revision": REVISION,
        "environment": "staging",
        "platform": "postgres",
        "system": "petclinic",
        "analyzerPack": "java-spring-data-jpa-v1",
        "ruleset": "spring-data-rules-v1",
        "schemaProfile": "postgres",
        "checkoutPath": str(checkout),
    }


def _database_digest(settings: Settings) -> str:
    with sqlite3.connect(settings.database_path) as connection:
        connection.row_factory = sqlite3.Row
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        digest = hashlib.sha256()
        for table in tables:
            digest.update(table.encode())
            # Identifiers come from sqlite_master, but quote them anyway so the digest
            # survives reserved words and embedded quotes.
            quoted = '"' + table.replace('"', '""') + '"'
            for row in connection.execute(f"SELECT * FROM {quoted}"):  # noqa: S608
                digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()


def _evidence_digest(settings: Settings) -> str:
    digest = hashlib.sha256()
    root = settings.object_directory
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def test_pinned_petclinic_collection_reaches_review_through_the_product_api(
    client: TestClient,
) -> None:
    checkout = _checkout()

    accepted = client.post("/api/collections", json=_submission(checkout))

    assert accepted.status_code == 202
    submitted = accepted.json()
    command_id = submitted["commandId"]
    assert accepted.headers["location"] == f"/api/collections/{command_id}"

    response = client.get(f"/api/collections/{command_id}")
    assert response.status_code == 200
    status = response.json()

    assert status["revision"] == REVISION
    assert status["outcome"] in {"ACCEPTED", "DUPLICATE", "REUSED"}
    assert status["runStatus"] == "IN_REVIEW"
    assert status["proposalStatus"] == "IN_REVIEW"
    assert status["runId"]
    assert status["proposalId"]
    assert status["terminal"] is True
    assert status["counts"] == EXPECTED_COUNTS
    assert status["coverageManifest"]["counts"] == EXPECTED_COVERAGE
    # Runtime evidence was never supplied, and absence must never be read as
    # corroboration.
    assert status["runtimeStatus"] == "NOT_PROVIDED"


def test_duplicate_submission_reuses_identity_with_no_new_effects(
    client: TestClient,
) -> None:
    checkout = _checkout()
    settings: Settings = client.lineage_settings  # type: ignore[attr-defined]
    body = _submission(checkout)

    first = client.post("/api/collections", json=body).json()
    database_after_first = _database_digest(settings)
    evidence_after_first = _evidence_digest(settings)

    second = client.post("/api/collections", json=body).json()

    for key in (
        "commandId",
        "collectionId",
        "runId",
        "proposalId",
        "determinantDigest",
        "scopeDigest",
    ):
        assert first[key] == second[key], key
    assert first["counts"] == second["counts"]
    assert _database_digest(settings) == database_after_first
    assert _evidence_digest(settings) == evidence_after_first


def test_product_responses_and_retained_evidence_stay_free_of_source_and_paths(
    client: TestClient,
) -> None:
    checkout = _checkout()
    settings: Settings = client.lineage_settings  # type: ignore[attr-defined]

    accepted = client.post("/api/collections", json=_submission(checkout))
    status = client.get(f"/api/collections/{accepted.json()['commandId']}")

    for rendered in (accepted.text, status.text):
        assert str(checkout) not in rendered
        for fragment in FORBIDDEN_FRAGMENTS:
            assert fragment not in rendered
        # The canonical, credential-free origin is safe; the local path is not.
        assert json.loads(rendered)["origin"] == ORIGIN

    for path in sorted(settings.object_directory.rglob("*")):
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        assert str(checkout) not in body, path
        assert "-----BEGIN" not in body, path


def test_user_facing_status_agrees_with_the_durable_run(client: TestClient) -> None:
    checkout = _checkout()

    accepted = client.post("/api/collections", json=_submission(checkout))
    status = client.get(f"/api/collections/{accepted.json()['commandId']}").json()
    run = client.get(f"/api/runs/{status['runId']}").json()

    run_document = run.get("run", run)
    assert run_document["digest"] == REVISION
    assert status["stages"], "the status projection must expose the executed stages"
    assert status["revision"] == run_document["digest"]
