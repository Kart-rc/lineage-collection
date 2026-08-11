from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from lineage_api.config import Settings
from lineage_api.services.intake import PushDelivery


PROJECT_ROOT = Path(__file__).parents[4]
SECRET = "runtime-reconciliation-secret"
ARTIFACT = "demo-digest-v2"
SOURCE_DATASET = "snowflake://payments/raw.transactions"
TARGET_DATASET = "snowflake://payments/analytics.daily_revenue"


def _settings(root: Path) -> Settings:
    return Settings(
        project_root=PROJECT_ROOT,
        data_directory=root,
        fixture_directory=PROJECT_ROOT / "fixtures",
        database_path=root / "lineage.db",
        object_directory=root / "objects",
        webhook_secret=SECRET,
    )


def _delivery(event_type: str, event_id: str) -> PushDelivery:
    payload = {
        "eventId": event_id,
        "eventType": event_type,
        "repo": "payments-pipeline",
        "digest": ARTIFACT,
        "env": "staging",
        "system": "payments",
        "changedFiles": [] if event_type == "baseline" else ["pipeline.py"],
        "receivedAt": "2026-08-06T12:00:00Z",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), canonical, hashlib.sha256).hexdigest()
    return PushDelivery(payload, f"sha256={signature}")


def _record_runtime(
    services,
    *,
    artifact_digest: str = ARTIFACT,
    complete: bool = True,
    observation_id: str = "runtime-sdk-1",
) -> dict[str, object]:
    grant = services.runtime.grant_session(
        repo="payments-pipeline",
        environment="staging",
        artifact_digest=artifact_digest,
        datasets=(SOURCE_DATASET, TARGET_DATASET),
        ttl_seconds=300,
        actor="runtime-reconciliation-test",
    )
    services.runtime.mark_ready(grant["sessionId"], grant["token"])
    services.runtime.observe(
        grant["sessionId"],
        grant["token"],
        "SDK",
        {
            "schemaVersion": "1.0.0",
            "observationId": observation_id,
            "sequence": 1,
            "artifactDigest": artifact_digest,
            "source": {"dataset": SOURCE_DATASET, "field": "amount"},
            "target": {"dataset": TARGET_DATASET, "field": "gross_revenue"},
            "edgeType": "DERIVES",
            "transform": "SUM(amount)",
            "observedAt": "2026-08-06T12:00:00Z",
        },
    )
    services.runtime.begin_drain(grant["sessionId"], grant["token"])
    return services.runtime.close(
        grant["sessionId"],
        grant["token"],
        expected_observations=1 if complete else 2,
        drained=complete,
    )


@pytest.mark.parametrize("event_type", ["repo.push", "baseline"])
def test_baseline_and_incremental_complete_without_runtime_and_record_missing_join(
    tmp_path: Path,
    event_type: str,
) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path / event_type.replace(".", "-")))
    services.reset()

    result = services.orchestration.process_push(
        _delivery(event_type, f"delivery-{event_type}-missing-runtime")
    )

    assert result["outcome"] == "ACCEPTED"
    assert result["evidenceManifest"]["runtime"] == {"status": "NOT_PROVIDED"}
    assert result["coverageManifest"]["runtimeEvidence"] == {
        "status": "NOT_PROVIDED",
        "sessionIds": [],
    }
    assert all(
        provenance["mechanism"] != "RUNTIME"
        for edge in result["proposal"]["diff"]["added"]
        for provenance in edge["provenance"]
    )


@pytest.mark.parametrize("event_type", ["repo.push", "baseline"])
def test_complete_exact_session_corroborates_at_element_granularity(
    tmp_path: Path,
    event_type: str,
) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path / event_type.replace(".", "-")))
    services.reset()
    manifest = _record_runtime(services)

    result = services.orchestration.process_push(
        _delivery(event_type, f"delivery-{event_type}-complete-runtime")
    )

    assert result["evidenceManifest"]["runtime"]["status"] == "VALIDATED"
    assert result["evidenceManifest"]["runtime"]["source"] == "SESSION"
    assert result["coverageManifest"]["runtimeEvidence"] == {
        "status": "VALIDATED",
        "sessionIds": [manifest["sessionId"]],
    }
    edge = next(
        edge
        for edge in result["proposal"]["diff"]["added"]
        if edge["to"].endswith("#gross_revenue") and edge["from"][0].endswith("#amount")
    )
    assert edge["corroboration"] == "ELEMENT"
    assert edge["band"] == "HIGH"
    runtime_provenance = [
        item for item in edge["provenance"] if item["mechanism"] == "RUNTIME"
    ]
    assert len(runtime_provenance) == 1
    assert runtime_provenance[0]["runtimeScope"] == "ELEMENT"


def test_incomplete_and_mismatched_sessions_never_promote_confidence(tmp_path: Path) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()
    incomplete = _record_runtime(services, complete=False)
    _record_runtime(
        services,
        artifact_digest="different-artifact",
        observation_id="runtime-other-artifact",
    )

    result = services.orchestration.process_push(
        _delivery("repo.push", "delivery-runtime-incomplete")
    )

    assert incomplete["outcome"] == "INCOMPLETE"
    assert result["evidenceManifest"]["runtime"] == {
        "status": "INCOMPLETE",
        "reason": "SESSION_INCOMPLETE",
        "sessionIds": [incomplete["sessionId"]],
    }
    assert result["coverageManifest"]["runtimeEvidence"] == {
        "status": "INCOMPLETE",
        "sessionIds": [incomplete["sessionId"]],
    }
    assert all(
        item["mechanism"] != "RUNTIME"
        for edge in result["proposal"]["diff"]["added"]
        for item in edge["provenance"]
    )


def test_late_complete_session_creates_one_normal_successor_proposal_and_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()
    initial = services.orchestration.process_push(
        _delivery("repo.push", "delivery-before-runtime")
    )
    manifest = _record_runtime(services)

    first = services.orchestration.reconcile_runtime_evidence(
        repo="payments-pipeline",
        environment="staging",
        artifact_digest=ARTIFACT,
        max_observations=100,
    )
    replay = services.orchestration.reconcile_runtime_evidence(
        repo="payments-pipeline",
        environment="staging",
        artifact_digest=ARTIFACT,
        max_observations=100,
    )

    assert first["status"] == "PROPOSED"
    assert first["proposal"]["state"] == "IN_REVIEW"
    assert first["proposal"]["proposalId"] != initial["proposal"]["proposalId"]
    assert replay["proposal"]["proposalId"] == first["proposal"]["proposalId"]
    assert first["sessionIds"] == [manifest["sessionId"]]
    affected = first["proposal"]["diff"]["added"]
    assert len(affected) == 1
    runtime_ids = [
        item["provenanceId"]
        for item in affected[0]["provenance"]
        if item["mechanism"] == "RUNTIME"
    ]
    assert runtime_ids == [f"runtime:{manifest['sessionId']}:runtime-sdk-1"]
    with services.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 2
        payloads = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT payload_json FROM edge_ledger WHERE edge_key = ? ORDER BY version",
                (affected[0]["edgeKey"],),
            )
        ]
    assert sum(
        item["provenanceId"] == runtime_ids[0]
        for payload in payloads
        for item in payload["provenance"]
    ) == 1
