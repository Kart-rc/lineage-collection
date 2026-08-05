from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lineage_api.db import Database
from lineage_api.infrastructure.local_broker import (
    LocalLaneBroker,
    StaleDeliveryError,
)


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
def broker(tmp_path: Path, clock: AdjustableClock) -> LocalLaneBroker:
    database = Database(tmp_path / "lineage.db")
    database.initialize()
    return LocalLaneBroker(database, clock)


def publish(
    broker: LocalLaneBroker,
    message_id: str,
    group_key: str,
    *,
    lane: str = "BULK",
    max_attempts: int = 3,
    supersession_key: str | None = None,
) -> str:
    return broker.publish(
        lane,
        group_key,
        f"object://commands/{message_id}",
        f"correlation-{message_id}",
        message_id=message_id,
        max_attempts=max_attempts,
        supersession_key=supersession_key,
    )


def test_fifo_within_a_group_allows_other_groups_to_run_in_parallel(
    broker: LocalLaneBroker,
) -> None:
    publish(broker, "a-1", "repo:a")
    publish(broker, "a-2", "repo:a")
    publish(broker, "b-1", "repo:b")

    first_a = broker.claim("BULK", "worker-a", visibility_timeout_seconds=30)
    first_b = broker.claim("BULK", "worker-b", visibility_timeout_seconds=30)

    assert first_a.message_id == "a-1"
    assert first_b.message_id == "b-1"
    broker.acknowledge(first_b)
    assert broker.claim("BULK", "worker-c", visibility_timeout_seconds=30) is None
    broker.acknowledge(first_a)
    assert broker.claim("BULK", "worker-c", visibility_timeout_seconds=30).message_id == "a-2"


def test_bulk_groups_are_claimed_round_robin_instead_of_starving_later_groups(
    broker: LocalLaneBroker,
) -> None:
    for message_id, group in (
        ("a-1", "repo:a"),
        ("a-2", "repo:a"),
        ("b-1", "repo:b"),
        ("b-2", "repo:b"),
    ):
        publish(broker, message_id, group)

    claimed: list[str] = []
    for worker in ("worker-1", "worker-2", "worker-3", "worker-4"):
        message = broker.claim("BULK", worker, visibility_timeout_seconds=30)
        claimed.append(message.message_id)
        broker.acknowledge(message)

    assert claimed == ["a-1", "b-1", "a-2", "b-2"]


def test_duplicate_publish_is_deduplicated_and_new_pr_head_supersedes_old_work(
    broker: LocalLaneBroker,
) -> None:
    publish(
        broker,
        "pr-old",
        "repo:payments#pr:42",
        lane="PR_GATE",
        supersession_key="repo:payments#pr:42",
    )
    duplicate = publish(
        broker,
        "pr-old",
        "repo:payments#pr:42",
        lane="PR_GATE",
        supersession_key="repo:payments#pr:42",
    )
    publish(
        broker,
        "pr-new",
        "repo:payments#pr:42",
        lane="PR_GATE",
        supersession_key="repo:payments#pr:42",
    )

    assert duplicate == "pr-old"
    assert broker.message_count("pr-old") == 1
    assert broker.message_status("pr-old") == "SUPERSEDED"
    assert (
        broker.claim("PR_GATE", "worker-a", visibility_timeout_seconds=30).message_id
        == "pr-new"
    )


def test_visibility_timeout_redelivers_with_a_new_receipt_and_rejects_stale_ack(
    broker: LocalLaneBroker, clock: AdjustableClock
) -> None:
    publish(broker, "runtime-1", "service:payments", lane="RUNTIME")
    first = broker.claim("RUNTIME", "worker-a", visibility_timeout_seconds=30)
    clock.advance(seconds=31)
    second = broker.claim("RUNTIME", "worker-b", visibility_timeout_seconds=30)

    assert second.message_id == first.message_id
    assert second.attempt == 2
    assert second.delivery_epoch > first.delivery_epoch
    with pytest.raises(StaleDeliveryError, match="stale delivery"):
        broker.acknowledge(first)
    broker.acknowledge(second)


def test_retry_bound_moves_message_to_dlq_and_redrive_resets_attempts(
    broker: LocalLaneBroker, clock: AdjustableClock
) -> None:
    publish(broker, "bulk-1", "system:payments", max_attempts=2)

    first = broker.claim("BULK", "worker-a", visibility_timeout_seconds=30)
    broker.retry(first, available_at=clock.now(), error_code="UPSTREAM_TIMEOUT")
    second = broker.claim("BULK", "worker-b", visibility_timeout_seconds=30)
    broker.retry(second, available_at=clock.now(), error_code="UPSTREAM_TIMEOUT")

    assert broker.message_status("bulk-1") == "DEAD"
    assert broker.dead_letters("BULK") == ("bulk-1",)
    broker.redrive("bulk-1", available_at=clock.now())
    redriven = broker.claim("BULK", "worker-c", visibility_timeout_seconds=30)
    assert redriven.message_id == "bulk-1"
    assert redriven.attempt == 1
