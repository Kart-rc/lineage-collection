from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from lineage_api.config import Settings


PROJECT_ROOT = Path(__file__).parents[4]
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


def _delivery(
    event_id: str = "delivery-001",
    *,
    digest: str = "demo-digest-v2",
    env: str = "staging",
    runtime_execution: bool = False,
):
    from lineage_api.services.intake import PushDelivery

    payload = {
        "eventId": event_id,
        "eventType": "repo.push",
        "repo": "payments-pipeline",
        "digest": digest,
        "env": env,
        "system": "payments",
        "changedFiles": ["pipeline.py"],
        "receivedAt": "2026-08-04T16:00:00Z",
    }
    if runtime_execution:
        payload["runtimeExecution"] = True
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return PushDelivery(payload, f"sha256={signature}")


def test_runtime_execution_flag_produces_a_complete_session_and_validated_status(
    tmp_path,
) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()

    collected = services.orchestration.process_push(
        _delivery("delivery-flag-on", runtime_execution=True)
    )

    assert collected["outcome"] == "ACCEPTED"
    assert collected["runtimeStatus"] == "CORROBORATED"
    assert collected["runtimeReasons"] == []

    with services.database.connection() as connection:
        row = connection.execute(
            "SELECT outcome FROM runtime_sessions WHERE repo = ? AND environment = ? "
            "AND artifact_digest = ?",
            ("payments-pipeline", "staging", "demo-digest-v2"),
        ).fetchone()
    assert row is not None
    assert row["outcome"] == "COMPLETE"

    bands = {edge["band"] for edge in collected["evidenceManifest"]["edges"]}
    assert "HIGH" in bands


def test_runtime_execution_absent_is_byte_identical_to_today(tmp_path) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()

    collected = services.orchestration.process_push(
        _delivery("delivery-flag-off", runtime_execution=False)
    )

    assert collected["outcome"] == "ACCEPTED"
    assert collected["runtimeStatus"] == "NOT_PROVIDED"
    assert collected["runtimeReasons"] == []
    assert collected["evidenceManifest"]["runtime"] == {"status": "NOT_PROVIDED"}

    with services.database.connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM runtime_sessions WHERE repo = ?",
            ("payments-pipeline",),
        ).fetchone()[0]
    assert count == 0


def test_runtime_failure_degrades_with_reason_and_leaves_sca_untouched(tmp_path) -> None:
    from lineage_api.dependencies import build_services
    from lineage_api.domain.errors import DomainError

    services = build_services(_settings(tmp_path))
    services.reset()

    baseline = services.orchestration.process_push(
        _delivery("delivery-baseline-compare", runtime_execution=False)
    )

    def _deny_production(**kwargs):
        raise DomainError(
            "RUNTIME_PRODUCTION_DENIED",
            "Runtime collection sessions are denied for production targets",
            "runtime-grant",
        )

    services.orchestration._runtime.grant_session = _deny_production

    collected = services.orchestration.process_push(
        _delivery("delivery-flag-denied", digest="demo-digest-v3", runtime_execution=True)
    )

    assert collected["outcome"] == "ACCEPTED"
    assert collected["runtimeReasons"] == ["session-denied"]

    with services.database.connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM runtime_sessions WHERE repo = ?",
            ("payments-pipeline",),
        ).fetchone()[0]
    assert count == 0

    baseline_edges = [
        {k: v for k, v in edge.items() if k != "version"}
        for edge in baseline["evidenceManifest"]["edges"]
    ]
    denied_edges = [
        {k: v for k, v in edge.items() if k != "version"}
        for edge in collected["evidenceManifest"]["edges"]
    ]
    assert denied_edges == baseline_edges
    assert baseline["analysis"]["status"] == collected["analysis"]["status"]
    assert baseline["analysis"]["edgeCount"] == collected["analysis"]["edgeCount"]
