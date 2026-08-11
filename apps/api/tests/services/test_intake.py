from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
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
def database(tmp_path: Path) -> Database:
    value = Database(tmp_path / "lineage.db")
    value.initialize()
    return value


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 4, 14, 15, tzinfo=UTC)


@pytest.fixture
def intake(database: Database):
    intake_service, _ = _intake_types()
    from lineage_api.infrastructure.local_broker import SQLiteOutbox
    from lineage_api.infrastructure.sqlite_control import (
        SQLiteCommandStore,
        SQLiteIntakeUnitOfWork,
    )

    clock = FixedClock()
    unit_of_work = SQLiteIntakeUnitOfWork(
        database,
        SQLiteCommandStore(database, clock),
        SQLiteOutbox(database, clock),
    )
    return intake_service(
        database,
        webhook_secret=SECRET,
        unit_of_work=unit_of_work,
        clock=clock,
    )


def _delivery(payload: dict[str, object], signature: str | None = None):
    _, push_delivery = _intake_types()
    return push_delivery(payload=payload, signature=signature or _signature(payload))


def test_valid_push_is_normalized_and_durably_enqueued_before_return(
    intake, database: Database
) -> None:
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
    assert result.command.command_id.startswith("command-")
    assert result.command.workflow_kind == "INCREMENTAL"
    assert result.command.input_ref == "event://delivery-001"
    assert result.outbox.payload_ref == f"command://{result.command.command_id}"
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0] == 1


def test_duplicate_delivery_returns_the_original_durable_command(intake) -> None:
    delivery = _delivery(_payload())

    first = intake.accept(delivery)
    second = intake.accept(delivery)

    assert first.outcome == "ACCEPTED"
    assert second.outcome == "DUPLICATE"
    assert first.event_id == second.event_id
    assert second.envelope == first.envelope
    assert second.command == first.command
    assert second.outbox == first.outbox
    assert intake.event_count("delivery-001") == 1
    assert intake.run_count_for("delivery-001") == 0


def test_failure_between_receipt_and_command_rolls_back_all_durable_state(
    database: Database,
) -> None:
    intake_service, _ = _intake_types()
    from lineage_api.infrastructure.local_broker import SQLiteOutbox
    from lineage_api.infrastructure.sqlite_control import (
        SQLiteCommandStore,
        SQLiteIntakeUnitOfWork,
    )

    def fail_after_receipt() -> None:
        raise RuntimeError("injected intake crash")

    clock = FixedClock()
    service = intake_service(
        database,
        webhook_secret=SECRET,
        unit_of_work=SQLiteIntakeUnitOfWork(
            database,
            SQLiteCommandStore(database, clock),
            SQLiteOutbox(database, clock),
            after_receipt=fail_after_receipt,
        ),
        clock=clock,
    )

    with pytest.raises(RuntimeError, match="injected intake crash"):
        service.accept(_delivery(_payload()))

    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0] == 0


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
def test_malformed_delivery_is_quarantined(
    intake, database: Database, overrides: dict[str, object], reason: str
) -> None:
    payload = _payload(**overrides)

    result = intake.accept(_delivery(payload))

    assert result.outcome == "QUARANTINED"
    assert result.reason == reason
    assert result.command is None
    assert result.outbox is None
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0] == 0


def test_repository_source_descriptor_is_closed_and_secret_safe(intake) -> None:
    source = {
        "sourceKind": "git-checkout",
        "origin": "https://example.com/acme/payments-pipeline",
        "revision": "1" * 40,
        "scopeDigest": "sha256:" + "2" * 64,
        "scopeDispositionDigest": "sha256:" + "4" * 64,
        "analyzerPack": "java-spring-data-jpa-v1",
        "ruleset": "spring-data-rules-v1",
        "framework": "spring-data-jpa",
        "schemaProfile": "postgres",
        "platform": "postgres",
        "accessToken": "must-not-be-persisted",
    }
    payload = _payload(repositorySource=source)

    result = intake.accept(_delivery(payload))

    assert result.outcome == "QUARANTINED"
    assert result.reason == "INVALID_REPOSITORY_SOURCE"
    quarantine = intake.quarantines()[0]
    assert "accessToken" not in quarantine["raw_json"]
    assert "must-not-be-persisted" not in quarantine["raw_json"]
