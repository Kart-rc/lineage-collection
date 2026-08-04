from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from lineage_api.db import Database


SECRET = "test-webhook-secret"
NOW = "2026-08-04T14:15:00Z"


def _intake_types():
    try:
        from lineage_api.services.intake import IntakeService, PushDelivery
    except ModuleNotFoundError:
        pytest.fail("Intake service is not implemented")
    return IntakeService, PushDelivery


def _signature(payload: dict[str, object]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "eventId": "delivery-001",
        "eventType": "repo.push",
        "repo": "payments-pipeline",
        "digest": "demo-digest-v2",
        "env": "staging",
        "system": "payments",
        "changedFiles": ["pipeline.py"],
        "receivedAt": NOW,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def intake(tmp_path: Path):
    intake_service, _ = _intake_types()
    database = Database(tmp_path / "lineage.db")
    database.initialize()
    return intake_service(database, webhook_secret=SECRET)


def _delivery(payload: dict[str, object], signature: str | None = None):
    _, push_delivery = _intake_types()
    return push_delivery(payload=payload, signature=signature or _signature(payload))


def test_valid_push_is_normalized_and_routed_to_events(intake) -> None:
    result = intake.accept(_delivery(_payload()))

    assert result.outcome == "ACCEPTED"
    assert result.envelope == {
        "schemaVersion": "1.0.0",
        "eventId": "delivery-001",
        "eventType": "repo.push",
        "correlationId": "corr-delivery-001",
        "repo": "payments-pipeline",
        "digest": "demo-digest-v2",
        "env": "staging",
        "system": "payments",
        "lane": "events",
        "changedFiles": ["pipeline.py"],
        "receivedAt": NOW,
    }


def test_duplicate_delivery_has_one_business_effect(intake) -> None:
    delivery = _delivery(_payload())

    first = intake.accept(delivery)
    second = intake.accept(delivery)

    assert first.outcome == "ACCEPTED"
    assert second.outcome == "DUPLICATE"
    assert first.event_id == second.event_id
    assert second.envelope == first.envelope
    assert intake.event_count("delivery-001") == 1
    assert intake.run_count_for("delivery-001") == 0


@pytest.mark.parametrize(
    ("event_type", "lane"),
    [
        ("pr.updated", "interactive"),
        ("repo.push", "events"),
        ("deploy", "events"),
        ("baseline", "bulk"),
        ("backfill", "bulk"),
        ("nightly", "bulk"),
        ("llm.batch", "bulk"),
    ],
)
def test_lane_policy_is_versioned_and_explicit(intake, event_type: str, lane: str) -> None:
    payload = _payload(eventId=f"delivery-{event_type}", eventType=event_type)

    result = intake.accept(_delivery(payload))

    assert result.outcome == "ACCEPTED"
    assert result.envelope["lane"] == lane
    assert result.policy_version == "1.0.0"


def test_invalid_signature_is_quarantined_without_persisting_payload(intake) -> None:
    result = intake.accept(_delivery(_payload(), signature="sha256=bad"))

    assert result.outcome == "QUARANTINED"
    assert result.reason == "BAD_SIGNATURE"
    assert intake.event_count("delivery-001") == 0
    quarantine = intake.quarantines()[0]
    assert quarantine["reason"] == "BAD_SIGNATURE"
    assert "changedFiles" not in quarantine["raw_json"]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"eventId": ""}, "MISSING_IDENTITY"),
        ({"digest": ""}, "MISSING_IDENTITY"),
        ({"eventType": "repository.deleted"}, "UNKNOWN_EVENT_TYPE"),
    ],
)
def test_malformed_delivery_is_quarantined(intake, overrides: dict[str, object], reason: str) -> None:
    payload = _payload(**overrides)

    result = intake.accept(_delivery(payload))

    assert result.outcome == "QUARANTINED"
    assert result.reason == reason
