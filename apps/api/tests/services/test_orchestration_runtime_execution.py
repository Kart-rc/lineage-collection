from __future__ import annotations

import hashlib
import hmac
import json
import shutil
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
    assert "runtimeReasons" not in collected
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
    assert collected["runtimeStatus"] == "NOT_PROVIDED"
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


def test_partial_seam_failure_keeps_verdict_when_the_other_seam_completes_a_session(
    tmp_path, monkeypatch
) -> None:
    from lineage_api.dependencies import build_services
    from lineage_api.services.intake import PushDelivery

    # A private copy of the fixture tree so a dummy .java path can be added without
    # touching the checked-in fixtures other tests (e.g. baseline scope) depend on.
    fixture_root = tmp_path / "fixtures"
    shutil.copytree(PROJECT_ROOT / "fixtures", fixture_root)
    (fixture_root / "repositories" / "payments-pipeline" / "Dummy.java").write_text(
        "// dummy source; java_home_or_none is patched unavailable before this compiles\n"
    )

    settings = Settings(
        project_root=PROJECT_ROOT,
        data_directory=tmp_path / "data",
        fixture_directory=fixture_root,
        database_path=tmp_path / "data" / "lineage.db",
        object_directory=tmp_path / "data" / "objects",
        webhook_secret=SECRET,
    )
    services = build_services(settings)
    services.reset()

    import lineage_api.application.java_runtime_stage as java_runtime_stage

    monkeypatch.setattr(java_runtime_stage, "java_home_or_none", lambda: None)

    payload = {
        "eventId": "delivery-mixed-seam",
        "eventType": "repo.push",
        "repo": "payments-pipeline",
        "digest": "demo-digest-mixed-seam",
        "env": "staging",
        "system": "payments",
        "changedFiles": ["pipeline.py", "Dummy.java"],
        "receivedAt": "2026-08-04T16:00:00Z",
        "runtimeExecution": True,
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()

    collected = services.orchestration.process_push(
        PushDelivery(payload, f"sha256={signature}")
    )

    assert collected["runtimeStatus"] == "CORROBORATED"
    assert collected["runtimeReasons"] == ["jvm-unavailable"]

    with services.database.connection() as connection:
        row = connection.execute(
            "SELECT outcome FROM runtime_sessions WHERE repo = ? AND environment = ? "
            "AND artifact_digest = ?",
            ("payments-pipeline", "staging", "demo-digest-mixed-seam"),
        ).fetchone()
    assert row is not None
    assert row["outcome"] == "COMPLETE"
