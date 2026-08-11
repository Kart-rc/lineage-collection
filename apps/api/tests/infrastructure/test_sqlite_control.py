from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lineage_api.application.models import Command, Lease, StageIdentity, StageResult
from lineage_api.application.ports import CommandStorePort
from lineage_api.db import Database
from lineage_api.infrastructure.sqlite_control import (
    AttemptsExhaustedError,
    IdempotencyConflictError,
    LeaseUnavailableError,
    SQLiteCommandStore,
    StaleLeaseError,
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
def database(tmp_path: Path) -> Database:
    value = Database(tmp_path / "lineage.db")
    value.initialize()
    return value


@pytest.fixture
def store(database: Database, clock: AdjustableClock) -> SQLiteCommandStore:
    return SQLiteCommandStore(database, clock)


def command_fixture(clock: AdjustableClock, **changes: object) -> Command:
    command = Command(
        command_id="command-001",
        idempotency_key="command:incremental:payments:source-v2",
        workflow_kind="INCREMENTAL",
        workflow_version="1.0.0",
        scope="repo:payments-pipeline",
        artifact_digest="sha256:source-v2",
        determinant_digest="sha256:determinants-v1",
        status="QUEUED",
        attempt=0,
        max_attempts=3,
        input_ref="object://inputs/source-v2",
        correlation_id="correlation-001",
        created_at=clock.now(),
        deadline_at=clock.now() + timedelta(minutes=10),
    )
    return replace(command, **changes)


def result_fixture(command: Command, lease_epoch: int, clock: AdjustableClock) -> StageResult:
    return StageResult(
        identity=StageIdentity(
            workflow_kind=command.workflow_kind,
            scope=command.scope,
            artifact_digest=command.artifact_digest,
            stage_name="ANALYZE",
            determinant_digest=command.determinant_digest,
            schema_version="1.0.0",
        ),
        command_id=command.command_id,
        output_ref="object://outputs/analyze-v2",
        output_checksum="sha256:output-v2",
        lease_epoch=lease_epoch,
        completed_at=clock.now(),
    )


def test_submission_is_unique_by_idempotency_key_and_rejects_changed_identity(
    store: SQLiteCommandStore, clock: AdjustableClock
) -> None:
    submitted = store.submit(command_fixture(clock))

    duplicate = store.submit(command_fixture(clock, command_id="command-duplicate"))

    assert duplicate == submitted
    with pytest.raises(IdempotencyConflictError, match="command idempotency key"):
        store.submit(
            command_fixture(
                clock,
                command_id="command-conflict",
                scope="repo:orders-pipeline",
            )
        )


def test_claim_is_exclusive_and_renewal_keeps_the_fencing_epoch(
    database: Database, store: SQLiteCommandStore, clock: AdjustableClock
) -> None:
    command = store.submit(command_fixture(clock))
    lease = store.claim(command.command_id, "worker-a", lease_seconds=30)

    competing_store = SQLiteCommandStore(database, clock)
    with pytest.raises(LeaseUnavailableError, match="worker-a"):
        competing_store.claim(command.command_id, "worker-b", lease_seconds=30)

    clock.advance(seconds=20)
    renewed = store.renew(lease, lease_seconds=30)

    assert renewed.owner == lease.owner
    assert renewed.epoch == lease.epoch
    assert renewed.expires_at == clock.now() + timedelta(seconds=30)


def test_two_workers_contending_for_a_command_produce_exactly_one_lease(
    database: Database, store: SQLiteCommandStore, clock: AdjustableClock
) -> None:
    command = store.submit(command_fixture(clock))

    def claim(owner: str) -> Lease | None:
        try:
            return SQLiteCommandStore(database, clock).claim(
                command.command_id, owner, lease_seconds=30
            )
        except LeaseUnavailableError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(claim, ("worker-a", "worker-b")))

    leases = tuple(result for result in results if result is not None)
    assert len(leases) == 1
    assert leases[0].epoch == 1
    assert store.get(command.command_id).attempt == 1
    assert isinstance(store, CommandStorePort)


def test_stale_worker_cannot_complete_after_expired_lease_is_stolen(
    store: SQLiteCommandStore, clock: AdjustableClock
) -> None:
    command = store.submit(command_fixture(clock))
    first = store.claim(command.command_id, "worker-a", lease_seconds=30)
    clock.advance(seconds=31)
    second = store.claim(command.command_id, "worker-b", lease_seconds=30)

    assert second.epoch > first.epoch
    with pytest.raises(StaleLeaseError, match="stale lease"):
        store.complete(first, result_fixture(command, first.epoch, clock))

    completed = store.complete(second, result_fixture(command, second.epoch, clock))
    assert completed.status == "COMPLETED"


def test_completed_stage_is_immutable_and_reused_after_lost_acknowledgement(
    store: SQLiteCommandStore, clock: AdjustableClock
) -> None:
    command = store.submit(command_fixture(clock))
    lease = store.claim(command.command_id, "worker-a", lease_seconds=30)
    result = result_fixture(command, lease.epoch, clock)

    completed = store.complete(lease, result)
    replayed = store.complete(
        lease,
        replace(result, output_ref="object://outputs/duplicate-location"),
    )

    assert completed.status == "COMPLETED"
    assert replayed.output_ref == result.output_ref
    assert store.completed_stage(result.identity) == result


def test_completed_stage_rejects_a_different_checksum(
    store: SQLiteCommandStore, clock: AdjustableClock
) -> None:
    command = store.submit(command_fixture(clock))
    lease = store.claim(command.command_id, "worker-a", lease_seconds=30)
    result = result_fixture(command, lease.epoch, clock)
    store.complete(lease, result)

    with pytest.raises(IdempotencyConflictError, match="checksum"):
        store.complete(lease, replace(result, output_checksum="sha256:different"))


def test_intermediate_checkpoint_is_immutable_and_rejects_a_stale_lease(
    store: SQLiteCommandStore, clock: AdjustableClock
) -> None:
    command = store.submit(command_fixture(clock))
    first = store.claim(command.command_id, "worker-a", lease_seconds=30)
    first_result = result_fixture(command, first.epoch, clock)

    recorded = store.record_stage(first, first_result)

    assert store.completed_stage(first_result.identity) == recorded
    with pytest.raises(IdempotencyConflictError, match="checksum"):
        store.record_stage(
            first,
            replace(first_result, output_checksum="sha256:different"),
        )

    clock.advance(seconds=31)
    store.claim(command.command_id, "worker-b", lease_seconds=30)
    stale_result = replace(
        first_result,
        identity=replace(first_result.identity, stage_name="RESOLVE"),
    )
    with pytest.raises(StaleLeaseError, match="stale lease"):
        store.record_stage(first, stale_result)


def test_retryable_failures_stop_at_the_configured_attempt_bound(
    store: SQLiteCommandStore, clock: AdjustableClock
) -> None:
    command = store.submit(command_fixture(clock, max_attempts=2))

    first = store.claim(command.command_id, "worker-a", lease_seconds=30)
    retryable = store.fail(first, "UPSTREAM_TIMEOUT", retryable=True)
    second = store.claim(command.command_id, "worker-b", lease_seconds=30)
    terminal = store.fail(second, "UPSTREAM_TIMEOUT", retryable=True)

    assert retryable.status == "FAILED_REDRIVABLE"
    assert terminal.status == "FAILED_TERMINAL"
    assert terminal.attempt == 2
    with pytest.raises(AttemptsExhaustedError, match="2 attempts"):
        store.claim(command.command_id, "worker-c", lease_seconds=30)
