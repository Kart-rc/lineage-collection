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


def _delivery(event_id: str = "delivery-001"):
    from lineage_api.services.intake import PushDelivery

    payload = {
        "eventId": event_id,
        "eventType": "repo.push",
        "repo": "payments-pipeline",
        "digest": "demo-digest-v2",
        "env": "staging",
        "system": "payments",
        "changedFiles": ["pipeline.py"],
        "receivedAt": "2026-08-04T16:00:00Z",
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return PushDelivery(payload, f"sha256={signature}")


def test_run_records_ordered_correlated_evidence_and_continues_after_approval(tmp_path) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()

    collected = services.orchestration.process_push(_delivery())

    assert collected["outcome"] == "ACCEPTED"
    assert collected["run"]["state"] == "IN_REVIEW"
    assert collected["proposal"]["state"] == "IN_REVIEW"
    assert [stage["stage"] for stage in collected["run"]["stages"]] == [
        "QUEUED",
        "CLASSIFYING",
        "ANALYZING",
        "RESOLVING",
        "STORING_EVIDENCE",
        "MERGING",
        "PROPOSING",
        "IN_REVIEW",
    ]
    assert {stage["correlationId"] for stage in collected["run"]["stages"]} == {
        "corr-delivery-001"
    }
    storing = collected["run"]["stages"][4]["detail"]
    assert storing["scaEvidenceRef"]["checksum"]
    assert storing["runtimeEvidenceRef"]["checksum"]

    proposal = collected["proposal"]
    approved = services.orchestration.approve(
        proposal["proposalId"],
        proposal["version"],
        actor="reviewer@example.test",
        rationale="Exact static evidence and complete runtime session agree.",
        expected_lock_version=proposal["lockVersion"],
    )

    assert approved["proposal"]["state"] == "FINALIZED"
    assert approved["pointer"]["activeVersion"] == "v2"
    assert approved["approvalRef"]["checksum"]
    assert approved["manifestRef"]["checksum"]
    assert approved["run"]["state"] == "PUBLISHED"
    assert [stage["stage"] for stage in approved["run"]["stages"][-2:]] == [
        "PUBLISHING",
        "PUBLISHED",
    ]


def test_duplicate_delivery_reuses_the_only_run(tmp_path) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()
    first = services.orchestration.process_push(_delivery())
    duplicate = services.orchestration.process_push(_delivery())

    assert duplicate["outcome"] == "DUPLICATE"
    assert duplicate["run"]["runId"] == first["run"]["runId"]
    assert len(services.orchestration.list_runs()) == 1


def test_unknown_classification_blocks_analysis(tmp_path) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()
    result = services.orchestration.process_push(
        _delivery("delivery-unknown"), classification_evidence=[]
    )

    assert result["outcome"] == "BLOCKED"
    assert result["reason"] == "UNKNOWN_CLASSIFICATION"
    assert result["run"]["state"] == "FAILED"
    assert result["run"]["failedStage"] == "CLASSIFYING"
    assert result["proposal"] is None
