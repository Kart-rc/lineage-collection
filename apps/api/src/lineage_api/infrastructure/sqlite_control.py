from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from lineage_api.application.models import (
    Command,
    DurableAcceptance,
    Lease,
    OutboxEvent,
    StageIdentity,
    StageResult,
    parse_utc,
)
from lineage_api.application.ports import ClockPort
from lineage_api.db import Database
from lineage_api.infrastructure.local_broker import SQLiteOutbox


class CommandStoreError(RuntimeError):
    """Base error for durable command state transitions."""


class CommandNotFoundError(CommandStoreError):
    pass


class CommandStateError(CommandStoreError):
    pass


class LeaseUnavailableError(CommandStoreError):
    pass


class StaleLeaseError(CommandStoreError):
    pass


class AttemptsExhaustedError(CommandStoreError):
    pass


class CommandDeadlineExceededError(CommandStoreError):
    pass


class IdempotencyConflictError(CommandStoreError):
    pass


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _command_from_row(row: sqlite3.Row) -> Command:
    return Command(
        command_id=row["command_id"],
        idempotency_key=row["idempotency_key"],
        workflow_kind=row["workflow_kind"],
        workflow_version=row["workflow_version"],
        scope=row["scope"],
        artifact_digest=row["artifact_digest"],
        determinant_digest=row["determinant_digest"],
        status=row["status"],
        attempt=row["attempt"],
        max_attempts=row["max_attempts"],
        input_ref=row["input_ref"],
        output_ref=row["output_ref"],
        correlation_id=row["correlation_id"],
        causation_id=row["causation_id"],
        created_at=parse_utc(row["created_at"]),
        deadline_at=parse_utc(row["deadline_at"]),
    )


def _submission_identity(command: Command) -> tuple[object, ...]:
    return (
        command.workflow_kind,
        command.workflow_version,
        command.scope,
        command.artifact_digest,
        command.determinant_digest,
        command.input_ref,
    )


class SQLiteCommandStore:
    """Durable local command store with lease fencing and immutable stage results."""

    def __init__(self, database: Database, clock: ClockPort) -> None:
        self.database = database
        self.clock = clock

    def submit(
        self,
        command: Command,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> Command:
        if command.status != "QUEUED" or command.attempt != 0:
            raise CommandStateError("new commands must be QUEUED at attempt zero")
        if connection is not None:
            return self._submit(connection, command)
        with self.database.transaction() as owned_connection:
            return self._submit(owned_connection, command)

    def _submit(self, connection: sqlite3.Connection, command: Command) -> Command:
        now = _utc_text(self.clock.now())
        row = connection.execute(
            "SELECT * FROM commands WHERE idempotency_key = ?",
            (command.idempotency_key,),
        ).fetchone()
        if row is not None:
            existing = _command_from_row(row)
            if _submission_identity(existing) != _submission_identity(command):
                raise IdempotencyConflictError(
                    "command idempotency key refers to a different immutable identity"
                )
            return existing

        connection.execute(
            """
            INSERT INTO commands(
                command_id, idempotency_key, workflow_kind, workflow_version, scope,
                artifact_digest, determinant_digest, status, attempt, max_attempts,
                input_ref, output_ref, correlation_id, causation_id, created_at,
                deadline_at, lease_owner, lease_epoch, lease_expires_at, last_error_code,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, NULL, NULL, ?)
            """,
            (
                command.command_id,
                command.idempotency_key,
                command.workflow_kind,
                command.workflow_version,
                command.scope,
                command.artifact_digest,
                command.determinant_digest,
                command.status,
                command.attempt,
                command.max_attempts,
                command.input_ref,
                command.output_ref,
                command.correlation_id,
                command.causation_id,
                _utc_text(command.created_at),
                _utc_text(command.deadline_at),
                now,
            ),
        )
        return command

    def get(self, command_id: str) -> Command | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM commands WHERE command_id = ?", (command_id,)
            ).fetchone()
        return None if row is None else _command_from_row(row)

    def claim(self, command_id: str, owner: str, lease_seconds: int) -> Lease:
        if not owner:
            raise ValueError("lease owner must not be empty")
        if lease_seconds < 1:
            raise ValueError("lease seconds must be positive")
        now_value = self.clock.now()
        now = _utc_text(now_value)
        expires_at = now_value + timedelta(seconds=lease_seconds)
        deferred_error: CommandStoreError | None = None
        claimed: Lease | None = None

        with self.database.transaction() as connection:
            row = self._required_row(connection, command_id)
            command = _command_from_row(row)

            if command.attempt >= command.max_attempts:
                if command.status != "FAILED_TERMINAL":
                    connection.execute(
                        """
                        UPDATE commands
                        SET status = 'FAILED_TERMINAL', lease_owner = NULL,
                            lease_expires_at = NULL, last_error_code = 'ATTEMPTS_EXHAUSTED',
                            updated_at = ?
                        WHERE command_id = ?
                        """,
                        (now, command_id),
                    )
                deferred_error = AttemptsExhaustedError(
                    f"command {command_id} has exhausted {command.max_attempts} attempts"
                )
            elif now_value >= command.deadline_at:
                connection.execute(
                    """
                    UPDATE commands
                    SET status = 'FAILED_TERMINAL', lease_owner = NULL,
                        lease_expires_at = NULL, last_error_code = 'DEADLINE_EXCEEDED',
                        updated_at = ?
                    WHERE command_id = ?
                    """,
                    (now, command_id),
                )
                deferred_error = CommandDeadlineExceededError(
                    f"command {command_id} exceeded its deadline"
                )
            elif command.status == "RUNNING":
                current_expiry = parse_utc(row["lease_expires_at"])
                if now_value < current_expiry:
                    raise LeaseUnavailableError(
                        f"command {command_id} is leased by {row['lease_owner']}"
                    )
                self._finish_attempt(
                    connection,
                    command,
                    row["lease_epoch"],
                    status="LEASE_EXPIRED",
                    completed_at=now,
                    error_code="LEASE_EXPIRED",
                )
            elif command.status not in {"QUEUED", "RETRY_WAIT", "FAILED_REDRIVABLE"}:
                raise CommandStateError(
                    f"command {command_id} cannot be claimed from {command.status}"
                )

            if deferred_error is None:
                next_attempt = command.attempt + 1
                next_epoch = row["lease_epoch"] + 1
                changed = connection.execute(
                    """
                    UPDATE commands
                    SET status = 'RUNNING', attempt = ?, lease_owner = ?, lease_epoch = ?,
                        lease_expires_at = ?, last_error_code = NULL, updated_at = ?
                    WHERE command_id = ? AND status = ? AND lease_epoch = ?
                    """,
                    (
                        next_attempt,
                        owner,
                        next_epoch,
                        _utc_text(expires_at),
                        now,
                        command_id,
                        command.status,
                        row["lease_epoch"],
                    ),
                )
                if changed.rowcount != 1:
                    raise LeaseUnavailableError(f"command {command_id} was claimed concurrently")
                connection.execute(
                    """
                    INSERT INTO command_attempts(
                        command_id, attempt, lease_epoch, owner, status, started_at
                    ) VALUES (?, ?, ?, ?, 'RUNNING', ?)
                    """,
                    (command_id, next_attempt, next_epoch, owner, now),
                )
                claimed = Lease(command_id, owner, next_epoch, expires_at)

        if deferred_error is not None:
            raise deferred_error
        if claimed is None:  # pragma: no cover - defensive invariant
            raise CommandStoreError(f"command {command_id} was not claimed")
        return claimed

    def renew(self, lease: Lease, lease_seconds: int) -> Lease:
        if lease_seconds < 1:
            raise ValueError("lease seconds must be positive")
        now_value = self.clock.now()
        now = _utc_text(now_value)
        renewed = Lease(
            lease.command_id,
            lease.owner,
            lease.epoch,
            now_value + timedelta(seconds=lease_seconds),
        )
        with self.database.transaction() as connection:
            row = self._required_row(connection, lease.command_id)
            self._assert_active_lease(row, lease, now_value)
            changed = connection.execute(
                """
                UPDATE commands
                SET lease_expires_at = ?, updated_at = ?
                WHERE command_id = ? AND status = 'RUNNING'
                  AND lease_owner = ? AND lease_epoch = ?
                """,
                (
                    _utc_text(renewed.expires_at),
                    now,
                    lease.command_id,
                    lease.owner,
                    lease.epoch,
                ),
            )
            if changed.rowcount != 1:
                raise StaleLeaseError(f"stale lease for command {lease.command_id}")
        return renewed

    def complete(self, lease: Lease, result: StageResult) -> Command:
        self._assert_result_matches_lease(lease, result)
        now_value = self.clock.now()
        now = _utc_text(now_value)
        key = result.identity.idempotency_key()
        with self.database.transaction() as connection:
            row = self._required_row(connection, lease.command_id)
            command = _command_from_row(row)
            self._assert_result_matches_command(command, result)
            stored = connection.execute(
                "SELECT * FROM stage_results WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if stored is not None and stored["output_checksum"] != result.output_checksum:
                raise IdempotencyConflictError(
                    f"completed stage {key} has a different checksum"
                )

            if command.status == "COMPLETED" and stored is not None:
                return command

            self._assert_active_lease(row, lease, now_value)
            output_ref = result.output_ref
            if stored is None:
                connection.execute(
                    """
                    INSERT INTO stage_results(
                        idempotency_key, command_id, output_ref, output_checksum,
                        lease_epoch, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        result.command_id,
                        result.output_ref,
                        result.output_checksum,
                        result.lease_epoch,
                        _utc_text(result.completed_at),
                    ),
                )
            else:
                output_ref = stored["output_ref"]

            changed = connection.execute(
                """
                UPDATE commands
                SET status = 'COMPLETED', output_ref = ?, lease_owner = NULL,
                    lease_expires_at = NULL, last_error_code = NULL, updated_at = ?
                WHERE command_id = ? AND status = 'RUNNING'
                  AND lease_owner = ? AND lease_epoch = ?
                """,
                (output_ref, now, lease.command_id, lease.owner, lease.epoch),
            )
            if changed.rowcount != 1:
                raise StaleLeaseError(f"stale lease for command {lease.command_id}")
            self._finish_attempt(
                connection,
                command,
                lease.epoch,
                status="COMPLETED",
                completed_at=now,
            )
            completed_row = self._required_row(connection, lease.command_id)
            return _command_from_row(completed_row)

    def fail(self, lease: Lease, error_code: str, retryable: bool) -> Command:
        if not error_code:
            raise ValueError("error code must not be empty")
        now_value = self.clock.now()
        now = _utc_text(now_value)
        with self.database.transaction() as connection:
            row = self._required_row(connection, lease.command_id)
            command = _command_from_row(row)
            self._assert_active_lease(row, lease, now_value)
            can_retry = (
                retryable
                and command.attempt < command.max_attempts
                and now_value < command.deadline_at
            )
            status = "FAILED_REDRIVABLE" if can_retry else "FAILED_TERMINAL"
            changed = connection.execute(
                """
                UPDATE commands
                SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                    last_error_code = ?, updated_at = ?
                WHERE command_id = ? AND status = 'RUNNING'
                  AND lease_owner = ? AND lease_epoch = ?
                """,
                (status, error_code, now, lease.command_id, lease.owner, lease.epoch),
            )
            if changed.rowcount != 1:
                raise StaleLeaseError(f"stale lease for command {lease.command_id}")
            self._finish_attempt(
                connection,
                command,
                lease.epoch,
                status="FAILED",
                completed_at=now,
                error_code=error_code,
            )
            failed_row = self._required_row(connection, lease.command_id)
            return _command_from_row(failed_row)

    def completed_stage(self, identity: StageIdentity) -> StageResult | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM stage_results WHERE idempotency_key = ?",
                (identity.idempotency_key(),),
            ).fetchone()
        if row is None:
            return None
        return StageResult(
            identity=identity,
            command_id=row["command_id"],
            output_ref=row["output_ref"],
            output_checksum=row["output_checksum"],
            lease_epoch=row["lease_epoch"],
            completed_at=parse_utc(row["completed_at"]),
        )

    def record_stage(self, lease: Lease, result: StageResult) -> StageResult:
        self._assert_result_matches_lease(lease, result)
        now_value = self.clock.now()
        key = result.identity.idempotency_key()
        with self.database.transaction() as connection:
            row = self._required_row(connection, lease.command_id)
            command = _command_from_row(row)
            self._assert_result_matches_command(command, result)
            stored = connection.execute(
                "SELECT * FROM stage_results WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if stored is not None:
                if stored["output_checksum"] != result.output_checksum:
                    raise IdempotencyConflictError(
                        f"completed stage {key} has a different checksum"
                    )
                return StageResult(
                    identity=result.identity,
                    command_id=stored["command_id"],
                    output_ref=stored["output_ref"],
                    output_checksum=stored["output_checksum"],
                    lease_epoch=stored["lease_epoch"],
                    completed_at=parse_utc(stored["completed_at"]),
                )
            self._assert_active_lease(row, lease, now_value)
            connection.execute(
                """
                INSERT INTO stage_results(
                    idempotency_key, command_id, output_ref, output_checksum,
                    lease_epoch, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    result.command_id,
                    result.output_ref,
                    result.output_checksum,
                    result.lease_epoch,
                    _utc_text(result.completed_at),
                ),
            )
        return result

    @staticmethod
    def _required_row(connection: sqlite3.Connection, command_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM commands WHERE command_id = ?", (command_id,)
        ).fetchone()
        if row is None:
            raise CommandNotFoundError(f"command {command_id} does not exist")
        return row

    @staticmethod
    def _assert_active_lease(row: sqlite3.Row, lease: Lease, now: datetime) -> None:
        expiry = row["lease_expires_at"]
        if (
            row["status"] != "RUNNING"
            or row["lease_owner"] != lease.owner
            or row["lease_epoch"] != lease.epoch
            or expiry is None
            or now >= parse_utc(expiry)
        ):
            raise StaleLeaseError(f"stale lease for command {lease.command_id}")

    @staticmethod
    def _assert_result_matches_lease(lease: Lease, result: StageResult) -> None:
        if result.command_id != lease.command_id or result.lease_epoch != lease.epoch:
            raise StaleLeaseError(f"stale lease for command {lease.command_id}")

    @staticmethod
    def _assert_result_matches_command(command: Command, result: StageResult) -> None:
        identity = result.identity
        if (
            identity.workflow_kind != command.workflow_kind
            or identity.scope != command.scope
            or identity.artifact_digest != command.artifact_digest
            or identity.determinant_digest != command.determinant_digest
        ):
            raise IdempotencyConflictError(
                "stage identity does not match the command immutable identity"
            )

    @staticmethod
    def _finish_attempt(
        connection: sqlite3.Connection,
        command: Command,
        lease_epoch: int,
        *,
        status: str,
        completed_at: str,
        error_code: str | None = None,
    ) -> None:
        connection.execute(
            """
            UPDATE command_attempts
            SET status = ?, completed_at = ?, error_code = ?
            WHERE command_id = ? AND attempt = ? AND lease_epoch = ?
            """,
            (
                status,
                completed_at,
                error_code,
                command.command_id,
                command.attempt,
                lease_epoch,
            ),
        )


class SQLiteIntakeUnitOfWork:
    """Commits an accepted receipt, command, and outbox event as one durable fact."""

    def __init__(
        self,
        database: Database,
        commands: SQLiteCommandStore,
        outbox: SQLiteOutbox,
        *,
        after_receipt: Callable[[], None] | None = None,
    ) -> None:
        self.database = database
        self.commands = commands
        self.outbox = outbox
        self.after_receipt = after_receipt

    def accept(
        self,
        event_id: str,
        envelope_json: str,
        created_at: datetime,
        command: Command,
        outbox: OutboxEvent,
    ) -> DurableAcceptance:
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
            created = existing is None
            if existing is not None and existing["payload_json"] != envelope_json:
                raise IdempotencyConflictError(
                    "event identity refers to a different immutable envelope"
                )
            stored_envelope = envelope_json if created else existing["payload_json"]
            if created:
                connection.execute(
                    """
                    INSERT INTO events(event_id, payload_json, outcome, created_at)
                    VALUES (?, ?, 'ACCEPTED', ?)
                    """,
                    (event_id, envelope_json, _utc_text(created_at)),
                )
                if self.after_receipt is not None:
                    self.after_receipt()

            stored_command = self.commands.submit(command, connection=connection)
            stored_outbox = self.outbox.append(outbox, connection=connection)
            return DurableAcceptance(
                created=created,
                envelope_json=stored_envelope,
                command=stored_command,
                outbox=stored_outbox,
            )
