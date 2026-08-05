from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lineage_api.application.models import OutboxEvent
from lineage_api.application.outbox import OutboxDispatcher
from lineage_api.application.ports import LaneBrokerPort, OutboxPort
from lineage_api.db import Database
from lineage_api.infrastructure.local_broker import LocalLaneBroker, SQLiteOutbox


class AdjustableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 5, 12, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, *, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> AdjustableClock:
    return AdjustableClock()


@pytest.fixture
def database(tmp_path: Path) -> Database:
    value = Database(tmp_path / "lineage.db")
    value.initialize()
    return value


def outbox_event(clock: AdjustableClock) -> OutboxEvent:
    return OutboxEvent(
        outbox_id="outbox-001",
        topic="INCREMENTAL",
        partition_key="repo:payments-pipeline",
        payload_ref="object://commands/command-001",
        status="PENDING",
        attempts=0,
        available_at=clock.now(),
        correlation_id="correlation-001",
        created_at=clock.now(),
    )


def test_state_and_outbox_append_commit_or_roll_back_together(
    database: Database, clock: AdjustableClock
) -> None:
    outbox = SQLiteOutbox(database, clock)
    event = outbox_event(clock)

    with pytest.raises(RuntimeError, match="injected crash"):
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO events(event_id, payload_json, outcome, created_at) VALUES (?, ?, ?, ?)",
                ("delivery-001", "{}", "ACCEPTED", "2026-08-05T12:00:00Z"),
            )
            outbox.append(event, connection=connection)
            raise RuntimeError("injected crash")

    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0] == 0


def test_dispatch_replays_after_publish_before_ack_without_duplicate_message(
    database: Database, clock: AdjustableClock
) -> None:
    outbox = SQLiteOutbox(database, clock)
    broker = LocalLaneBroker(database, clock)
    event = outbox.append(outbox_event(clock))
    assert isinstance(outbox, OutboxPort)
    assert isinstance(broker, LaneBrokerPort)

    def crash_after_publish(_: OutboxEvent) -> None:
        raise RuntimeError("injected crash after publish")

    crashing = OutboxDispatcher(outbox, broker, clock, after_publish=crash_after_publish)
    with pytest.raises(RuntimeError, match="after publish"):
        crashing.dispatch(limit=10)

    assert tuple(outbox.pending(limit=10)) == (event,)
    assert broker.message_count(event.outbox_id) == 1

    delivered = OutboxDispatcher(outbox, broker, clock).dispatch(limit=10)

    assert delivered == 1
    assert tuple(outbox.pending(limit=10)) == ()
    assert broker.message_count(event.outbox_id) == 1
