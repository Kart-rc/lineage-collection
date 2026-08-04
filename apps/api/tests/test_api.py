from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from lineage_api.config import Settings


PROJECT_ROOT = Path(__file__).parents[3]
SECRET = "test-lineage-secret"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=PROJECT_ROOT,
        data_directory=tmp_path,
        fixture_directory=PROJECT_ROOT / "fixtures",
        database_path=tmp_path / "lineage.db",
        object_directory=tmp_path / "objects",
        webhook_secret=SECRET,
    )


def _payload(event_id: str = "delivery-api-001") -> dict:
    return {
        "eventId": event_id,
        "eventType": "repo.push",
        "repo": "payments-pipeline",
        "digest": "demo-digest-v2",
        "env": "staging",
        "system": "payments",
        "changedFiles": ["pipeline.py"],
        "receivedAt": "2026-08-04T16:00:00Z",
    }


def _signature(payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def client(tmp_path: Path):
    from fastapi.testclient import TestClient
    from lineage_api.main import create_app

    with TestClient(create_app(_settings(tmp_path))) as test_client:
        yield test_client


def test_health_reset_overview_and_quarantine_routes(client) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    reset = client.post("/api/demo/reset")
    assert reset.status_code == 200
    assert reset.json()["activeVersion"] == "v1"
    assert reset.json()["demoDelivery"]["signature"].startswith("sha256=")

    bad = client.post(
        "/api/events/push", json={"payload": _payload("bad-sig"), "signature": "sha256=bad"}
    )
    assert bad.status_code == 202
    assert bad.json()["outcome"] == "QUARANTINED"
    assert bad.json()["reason"] == "BAD_SIGNATURE"
    assert client.get("/api/quarantine").json()[0]["reason"] == "BAD_SIGNATURE"

    overview = client.get("/api/overview").json()
    assert overview["activeVersion"] == "v1"
    assert overview["counts"] == {"runs": 0, "inReview": 0, "quarantined": 1}


def test_duplicate_missing_resource_depth_and_concurrent_error_shapes(client) -> None:
    client.post("/api/demo/reset")
    payload = _payload()
    request = {"payload": payload, "signature": _signature(payload)}
    first = client.post("/api/events/push", json=request)
    duplicate = client.post("/api/events/push", json=request)
    assert first.status_code == 202
    assert duplicate.status_code == 200
    assert duplicate.json()["outcome"] == "DUPLICATE"
    assert len(client.get("/api/runs").json()) == 1

    missing = client.get("/api/proposals/does-not-exist")
    assert missing.status_code == 404
    assert set(missing.json()) >= {"code", "message", "correlationId"}

    too_deep = client.get(
        "/api/lineage/urn:ldp:staging:snowflake:payments:raw.transactions%23amount",
        params={"depth": 6},
    )
    assert too_deep.status_code == 422
    assert too_deep.json()["code"] == "DEPTH_EXCEEDED"

    proposal = first.json()["proposal"]
    decision = {
        "version": proposal["version"],
        "actor": "reviewer@example.test",
        "rationale": "verified",
        "expectedLockVersion": proposal["lockVersion"],
    }
    assert client.post(
        f"/api/proposals/{proposal['proposalId']}/approve", json=decision
    ).status_code == 200
    conflict = client.post(
        f"/api/proposals/{proposal['proposalId']}/approve", json=decision
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "CONCURRENT_DECISION"


def test_list_and_detail_routes_expose_evidence_and_audit(client) -> None:
    reset = client.post("/api/demo/reset").json()
    collected = client.post("/api/events/push", json=reset["demoDelivery"]).json()
    proposal = collected["proposal"]

    detail = client.get(f"/api/proposals/{proposal['proposalId']}").json()
    assert detail["diff"]["added"][0]["band"] == "HIGH"
    assert {item["mechanism"] for item in detail["diff"]["added"][0]["provenance"]} == {
        "SCA",
        "RUNTIME",
    }
    sca_provenance = next(
        item
        for item in detail["diff"]["added"][0]["provenance"]
        if item["mechanism"] == "SCA"
    )
    assert sca_provenance["citation"]["file"] == "pipeline.py"
    assert sca_provenance["citation"]["line"] > 0
    assert client.get(f"/api/runs/{collected['run']['runId']}").json()["stages"]
    assert client.get("/api/proposals").json()[0]["proposalId"] == proposal["proposalId"]

    approved = client.post(
        f"/api/proposals/{proposal['proposalId']}/approve",
        json={
            "version": 1,
            "actor": "reviewer@example.test",
            "rationale": "verified",
            "expectedLockVersion": 1,
        },
    ).json()
    edge_key = approved["proposal"]["diff"]["added"][0]["edgeKey"]
    assert client.get(f"/api/edges/{edge_key}").json()["provenance"]
    assert client.get("/api/audit").json()[0]["actor"] == "reviewer@example.test"
