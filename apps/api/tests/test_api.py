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


def test_pr_gate_route_is_lineage_read_only_and_upserts_one_stable_check(client) -> None:
    client.post("/api/demo/reset")
    services = client.app.state.services
    authoritative_tables = (
        "events",
        "runs",
        "evidence_objects",
        "edge_ledger",
        "proposals",
        "graph_versions",
        "graph_edges",
        "pointers",
        "publish_reservations",
        "coverage_manifests",
    )
    before = services.database.snapshot(authoritative_tables)
    body = {
        "repo": "payments-pipeline",
        "prNumber": 42,
        "headSha": "head-abc",
        "targetEnvironment": "staging",
        "policyVersion": "1.0.0",
        "candidateArtifactDigest": "sha256:candidate",
        "coverageComplete": True,
        "changes": [
            {
                "changeType": "COLUMN_DROP",
                "subject": "urn:ldp:staging:snowflake:payments:raw.transactions#amount",
                "evidenceMechanisms": ["SCA"],
            }
        ],
        "depth": 5,
    }

    first = client.post("/api/pr-gate/evaluate", json=body)
    second = client.post("/api/pr-gate/evaluate", json=body)

    assert first.status_code == 200
    assert first.json()["verdict"] == "PASS"
    assert first.json()["checkId"] == second.json()["checkId"]
    assert services.database.snapshot(authoritative_tables) == before
    with services.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM pr_gate_checks").fetchone()[0] == 1


def test_approved_artifact_package_is_promoted_by_authenticated_deployment_outcome(client) -> None:
    reset = client.post("/api/demo/reset").json()
    collected = client.post("/api/events/push", json=reset["demoDelivery"]).json()
    proposal = collected["proposal"]
    approved = client.post(
        f"/api/proposals/{proposal['proposalId']}/approve",
        json={
            "version": proposal["version"],
            "actor": "reviewer@example.test",
            "rationale": "verified",
            "expectedLockVersion": proposal["lockVersion"],
        },
    )
    assert approved.status_code == 200

    payload = {
        "schemaVersion": "1.0.0",
        "eventId": "deployment-api-001",
        "eventType": "DEPLOYMENT",
        "provider": "test-deployer",
        "providerSequence": 1,
        "attempt": 1,
        "system": "payments",
        "environment": "staging",
        "outcome": "SUCCEEDED",
        "artifactDigest": "demo-digest-v2",
        "correlationId": "corr-deployment-api-001",
        "auditRef": "provider-audit://deployment-api-001",
        "occurredAt": "2026-08-05T12:00:00Z",
    }

    promoted = client.post(
        "/api/deployments/outcomes",
        json={"payload": payload, "signature": _signature(payload)},
    )

    assert promoted.status_code == 200
    assert promoted.json()["state"] == "PROMOTED"
    assert promoted.json()["deployedArtifactDigest"] == "demo-digest-v2"
    assert promoted.json()["lineagePackageDigest"]
    with client.app.state.services.database.connection() as connection:
        package = connection.execute(
            "SELECT artifact_digest, graph_version FROM lineage_packages"
        ).fetchone()
        state = connection.execute(
            "SELECT deployed_artifact_digest, graph_version FROM deployment_state"
        ).fetchone()
    assert dict(package) == {"artifact_digest": "demo-digest-v2", "graph_version": "v2"}
    assert dict(state) == {
        "deployed_artifact_digest": "demo-digest-v2",
        "graph_version": "v2",
    }

    client.post("/api/demo/reset")
    with client.app.state.services.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM lineage_packages").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM deployment_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM deployment_state").fetchone()[0] == 0


def test_successful_deployment_request_without_artifact_is_rejected_before_state_mutation(
    client,
) -> None:
    client.post("/api/demo/reset")
    payload = {
        "schemaVersion": "1.0.0",
        "eventId": "deployment-invalid-001",
        "eventType": "DEPLOYMENT",
        "provider": "test-deployer",
        "providerSequence": 1,
        "attempt": 1,
        "system": "payments",
        "environment": "staging",
        "outcome": "SUCCEEDED",
        "correlationId": "corr-deployment-invalid-001",
        "auditRef": "provider-audit://deployment-invalid-001",
        "occurredAt": "2026-08-05T12:00:00Z",
    }

    response = client.post(
        "/api/deployments/outcomes",
        json={"payload": payload, "signature": _signature(payload)},
    )

    assert response.status_code == 422
    with client.app.state.services.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM deployment_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM deployment_state").fetchone()[0] == 0
