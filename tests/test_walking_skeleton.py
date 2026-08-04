from __future__ import annotations

from pathlib import Path

from lineage_api.config import Settings


PROJECT_ROOT = Path(__file__).parents[1]


def test_signed_push_to_approved_projection_to_impact(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from lineage_api.main import create_app

    settings = Settings(
        project_root=PROJECT_ROOT,
        data_directory=tmp_path,
        fixture_directory=PROJECT_ROOT / "fixtures",
        database_path=tmp_path / "lineage.db",
        object_directory=tmp_path / "objects",
        webhook_secret="local-lineage-demo-secret",
    )
    with TestClient(create_app(settings)) as client:
        demo = client.post("/api/demo/reset").json()["demoDelivery"]
        collected = client.post("/api/events/push", json=demo).json()
        proposal = collected["proposal"]
        published = client.post(
            f"/api/proposals/{proposal['proposalId']}/approve",
            json={
                "version": proposal["version"],
                "actor": "demo.reviewer@example.test",
                "rationale": "Evidence inspected in the prototype.",
                "expectedLockVersion": proposal["lockVersion"],
            },
        ).json()

        assert published["pointer"]["activeVersion"] == "v2"
        subject = "urn:ldp:staging:snowflake:payments:raw.transactions#amount"
        lineage = client.get(
            f"/api/lineage/{subject.replace('#', '%23')}",
            params={"direction": "down", "depth": 2},
        ).json()
        impact = client.post(
            "/api/impact",
            json={"subject": subject, "changeType": "COLUMN_DROP", "depth": 5},
        ).json()

        assert lineage["namespaceVersion"] == "v2"
        assert any(edge["band"] == "HIGH" for edge in lineage["edges"])
        assert impact["summary"]["block"] >= 1
        assert impact["affected"][0]["severity"] == "BLOCK"
