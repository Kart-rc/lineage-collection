from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from lineage_api.application.models import LaneMessage, OutboxEvent, parse_utc
from lineage_api.application.ports import ClockPort
from lineage_api.db import Database


class OutboxStateError(RuntimeError):
    pass


class MessageStateError(RuntimeError):
    pass


class MessageConflictError(RuntimeError):
    pass


class StaleDeliveryError(RuntimeError):
    pass


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _outbox_event_from_row(row: sqlite3.Row) -> OutboxEvent:
    delivered_at = row["delivered_at"]
    return OutboxEvent(
        outbox_id=row["outbox_id"],
        topic=row["topic"],
        partition_key=row["partition_key"],
        payload_ref=row["payload_ref"],
        status=row["status"],
        attempts=row["attempts"],
        available_at=parse_utc(row["available_at"]),
        correlation_id=row["correlation_id"],
        created_at=parse_utc(row["created_at"]),
        delivered_at=None if delivered_at is None else parse_utc(delivered_at),
    )


def _outbox_identity(event: OutboxEvent) -> tuple[str, ...]:
    return (
        event.topic,
        event.partition_key,
        event.payload_ref,
        event.correlation_id,
    )


class SQLiteOutbox:
    """Outbox adapter that can join an existing SQLite state transaction."""

    def __init__(self, database: Database, clock: ClockPort) -> None:
        self.database = database
        self.clock = clock

    def append(
        self,
        event: OutboxEvent,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> OutboxEvent:
        if event.status != "PENDING" or event.attempts != 0:
            raise OutboxStateError("new outbox events must be PENDING at attempt zero")
        if connection is not None:
            return self._append(connection, event)
        with self.database.transaction() as owned_connection:
            return self._append(owned_connection, event)

    @staticmethod
    def _append(connection: sqlite3.Connection, event: OutboxEvent) -> OutboxEvent:
        row = connection.execute(
            "SELECT * FROM outbox_events WHERE outbox_id = ?", (event.outbox_id,)
        ).fetchone()
        if row is not None:
            existing = _outbox_event_from_row(row)
            if _outbox_identity(existing) != _outbox_identity(event):
                raise MessageConflictError(
                    f"outbox id {event.outbox_id} refers to different immutable content"
                )
            return existing
        connection.execute(
            """
            INSERT INTO outbox_events(
                outbox_id, topic, partition_key, payload_ref, status, attempts,
                available_at, correlation_id, created_at, delivered_at, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (
                event.outbox_id,
                event.topic,
                event.partition_key,
                event.payload_ref,
                event.status,
                event.attempts,
                _utc_text(event.available_at),
                event.correlation_id,
                _utc_text(event.created_at),
            ),
        )
        return event

    def pending(self, limit: int) -> Iterable[OutboxEvent]:
        if limit < 1:
            raise ValueError("pending limit must be positive")
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM outbox_events
                WHERE status = 'PENDING' AND available_at <= ?
                ORDER BY available_at, created_at, outbox_id
                LIMIT ?
                """,
                (_utc_text(self.clock.now()), limit),
            ).fetchall()
        return tuple(_outbox_event_from_row(row) for row in rows)

    def mark_delivered(self, outbox_id: str, delivered_at: datetime) -> None:
        delivered = _utc_text(delivered_at)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM outbox_events WHERE outbox_id = ?", (outbox_id,)
            ).fetchone()
            if row is None:
                raise OutboxStateError(f"outbox event {outbox_id} does not exist")
            if row["status"] == "DELIVERED":
                return
            if row["status"] != "PENDING":
                raise OutboxStateError(
                    f"outbox event {outbox_id} cannot be delivered from {row['status']}"
                )
            connection.execute(
                """
                UPDATE outbox_events
                SET status = 'DELIVERED', attempts = attempts + 1, delivered_at = ?
                WHERE outbox_id = ? AND status = 'PENDING'
                """,
                (delivered, outbox_id),
            )


def _lane_message_from_row(row: sqlite3.Row) -> LaneMessage:
    return LaneMessage(
        message_id=row["message_id"],
        lane=row["lane"],
        group_key=row["group_key"],
        payload_ref=row["payload_ref"],
        correlation_id=row["correlation_id"],
        attempt=row["attempts"],
        max_attempts=row["max_attempts"],
        delivery_epoch=row["delivery_epoch"],
        owner=row["lease_owner"],
        lease_expires_at=parse_utc(row["lease_expires_at"]),
        supersession_key=row["supersession_key"],
    )


class LocalLaneBroker:
    """Persistent queue simulator with FIFO groups, fairness, fencing, and a logical DLQ."""

    def __init__(self, database: Database, clock: ClockPort) -> None:
        self.database = database
        self.clock = clock

    def publish(
        self,
        lane: str,
        group_key: str,
        payload_ref: str,
        correlation_id: str,
        *,
        message_id: str,
        max_attempts: int = 5,
        supersession_key: str | None = None,
    ) -> str:
        if not all((lane, group_key, payload_ref, correlation_id, message_id)):
            raise ValueError("lane message identity fields must not be empty")
        if max_attempts < 1:
            raise ValueError("max attempts must be positive")
        now = _utc_text(self.clock.now())
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM lane_messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if existing is not None:
                identity = (
                    existing["lane"],
                    existing["group_key"],
                    existing["payload_ref"],
                    existing["correlation_id"],
                    existing["supersession_key"],
                    existing["max_attempts"],
                )
                requested = (
                    lane,
                    group_key,
                    payload_ref,
                    correlation_id,
                    supersession_key,
                    max_attempts,
                )
                if identity != requested:
                    raise MessageConflictError(
                        f"message id {message_id} refers to different immutable content"
                    )
                return message_id

            if supersession_key is not None:
                connection.execute(
                    """
                    UPDATE lane_messages
                    SET status = 'SUPERSEDED', lease_owner = NULL, lease_expires_at = NULL,
                        last_error = 'SUPERSEDED_BY_NEWER_HEAD'
                    WHERE lane = ? AND supersession_key = ?
                      AND status IN ('AVAILABLE', 'IN_FLIGHT')
                    """,
                    (lane, supersession_key),
                )
            connection.execute(
                """
                INSERT INTO lane_messages(
                    message_id, lane, group_key, payload_ref, correlation_id, status,
                    available_at, attempts, max_attempts, delivery_epoch, lease_owner,
                    lease_expires_at, supersession_key, published_at
                ) VALUES (?, ?, ?, ?, ?, 'AVAILABLE', ?, 0, ?, 0, NULL, NULL, ?, ?)
                """,
                (
                    message_id,
                    lane,
                    group_key,
                    payload_ref,
                    correlation_id,
                    now,
                    max_attempts,
                    supersession_key,
                    now,
                ),
            )
        return message_id

    def claim(
        self,
        lane: str,
        owner: str,
        visibility_timeout_seconds: int = 30,
    ) -> LaneMessage | None:
        if not lane or not owner:
            raise ValueError("lane and owner must not be empty")
        if visibility_timeout_seconds < 1:
            raise ValueError("visibility timeout must be positive")
        now_value = self.clock.now()
        now = _utc_text(now_value)
        expires_at = _utc_text(
            now_value + timedelta(seconds=visibility_timeout_seconds)
        )
        with self.database.transaction() as connection:
            self._recover_expired(connection, lane, now)
            row = connection.execute(
                """
                SELECT m.*
                FROM lane_messages AS m
                LEFT JOIN lane_group_state AS g
                  ON g.lane = m.lane AND g.group_key = m.group_key
                WHERE m.lane = ?
                  AND m.status = 'AVAILABLE'
                  AND m.available_at <= ?
                  AND NOT EXISTS (
                    SELECT 1
                    FROM lane_messages AS older
                    WHERE older.lane = m.lane
                      AND older.group_key = m.group_key
                      AND older.sequence < m.sequence
                      AND older.status IN ('AVAILABLE', 'IN_FLIGHT')
                  )
                ORDER BY
                  CASE WHEN g.last_claim_order IS NULL THEN 0 ELSE 1 END,
                  g.last_claim_order,
                  m.sequence
                LIMIT 1
                """,
                (lane, now),
            ).fetchone()
            if row is None:
                return None

            next_epoch = row["delivery_epoch"] + 1
            next_attempt = row["attempts"] + 1
            changed = connection.execute(
                """
                UPDATE lane_messages
                SET status = 'IN_FLIGHT', attempts = ?, delivery_epoch = ?,
                    lease_owner = ?, lease_expires_at = ?
                WHERE sequence = ? AND status = 'AVAILABLE' AND delivery_epoch = ?
                """,
                (
                    next_attempt,
                    next_epoch,
                    owner,
                    expires_at,
                    row["sequence"],
                    row["delivery_epoch"],
                ),
            )
            if changed.rowcount != 1:
                raise MessageStateError(f"message {row['message_id']} was claimed concurrently")

            claim_order = connection.execute(
                "SELECT COALESCE(MAX(last_claim_order), 0) + 1 FROM lane_group_state"
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO lane_group_state(lane, group_key, last_claim_order)
                VALUES (?, ?, ?)
                ON CONFLICT(lane, group_key)
                DO UPDATE SET last_claim_order = excluded.last_claim_order
                """,
                (lane, row["group_key"], claim_order),
            )
            claimed = connection.execute(
                "SELECT * FROM lane_messages WHERE sequence = ?", (row["sequence"],)
            ).fetchone()
            return _lane_message_from_row(claimed)

    def acknowledge(self, message: LaneMessage) -> None:
        now_value = self.clock.now()
        now = _utc_text(now_value)
        with self.database.transaction() as connection:
            row = self._required_message(connection, message.message_id)
            self._assert_active_delivery(row, message, now_value)
            changed = connection.execute(
                """
                UPDATE lane_messages
                SET status = 'ACKED', lease_owner = NULL, lease_expires_at = NULL,
                    acknowledged_at = ?
                WHERE message_id = ? AND status = 'IN_FLIGHT'
                  AND lease_owner = ? AND delivery_epoch = ?
                """,
                (now, message.message_id, message.owner, message.delivery_epoch),
            )
            if changed.rowcount != 1:
                raise StaleDeliveryError(f"stale delivery for message {message.message_id}")

    def retry(
        self,
        message: LaneMessage,
        available_at: datetime,
        error_code: str,
    ) -> None:
        if not error_code:
            raise ValueError("error code must not be empty")
        available = _utc_text(available_at)
        now_value = self.clock.now()
        now = _utc_text(now_value)
        with self.database.transaction() as connection:
            row = self._required_message(connection, message.message_id)
            self._assert_active_delivery(row, message, now_value)
            exhausted = row["attempts"] >= row["max_attempts"]
            status = "DEAD" if exhausted else "AVAILABLE"
            changed = connection.execute(
                """
                UPDATE lane_messages
                SET status = ?, available_at = ?, lease_owner = NULL,
                    lease_expires_at = NULL, last_error = ?,
                    dead_at = CASE WHEN ? THEN ? ELSE NULL END
                WHERE message_id = ? AND status = 'IN_FLIGHT'
                  AND lease_owner = ? AND delivery_epoch = ?
                """,
                (
                    status,
                    available,
                    error_code,
                    exhausted,
                    now,
                    message.message_id,
                    message.owner,
                    message.delivery_epoch,
                ),
            )
            if changed.rowcount != 1:
                raise StaleDeliveryError(f"stale delivery for message {message.message_id}")

    def redrive(self, message_id: str, available_at: datetime) -> None:
        available = _utc_text(available_at)
        with self.database.transaction() as connection:
            row = self._required_message(connection, message_id)
            if row["status"] != "DEAD":
                raise MessageStateError(
                    f"message {message_id} cannot be redriven from {row['status']}"
                )
            connection.execute(
                """
                UPDATE lane_messages
                SET status = 'AVAILABLE', available_at = ?, attempts = 0,
                    lease_owner = NULL, lease_expires_at = NULL, dead_at = NULL,
                    last_error = NULL
                WHERE message_id = ? AND status = 'DEAD'
                """,
                (available, message_id),
            )

    def dead_letters(self, lane: str) -> tuple[str, ...]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT message_id FROM lane_messages
                WHERE lane = ? AND status = 'DEAD'
                ORDER BY sequence
                """,
                (lane,),
            ).fetchall()
        return tuple(row["message_id"] for row in rows)

    def message_status(self, message_id: str) -> str | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT status FROM lane_messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        return None if row is None else row["status"]

    def message_count(self, message_id: str) -> int:
        with self.database.connection() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM lane_messages WHERE message_id = ?", (message_id,)
            ).fetchone()[0]

    @staticmethod
    def _recover_expired(
        connection: sqlite3.Connection,
        lane: str,
        now: str,
    ) -> None:
        connection.execute(
            """
            UPDATE lane_messages
            SET status = CASE WHEN attempts >= max_attempts THEN 'DEAD' ELSE 'AVAILABLE' END,
                available_at = ?, lease_owner = NULL, lease_expires_at = NULL,
                dead_at = CASE WHEN attempts >= max_attempts THEN ? ELSE NULL END,
                last_error = 'VISIBILITY_TIMEOUT'
            WHERE lane = ? AND status = 'IN_FLIGHT' AND lease_expires_at <= ?
            """,
            (now, now, lane, now),
        )

    @staticmethod
    def _required_message(
        connection: sqlite3.Connection, message_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM lane_messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        if row is None:
            raise MessageStateError(f"message {message_id} does not exist")
        return row

    @staticmethod
    def _assert_active_delivery(
        row: sqlite3.Row,
        message: LaneMessage,
        now: datetime,
    ) -> None:
        expiry = row["lease_expires_at"]
        if (
            row["status"] != "IN_FLIGHT"
            or row["lease_owner"] != message.owner
            or row["delivery_epoch"] != message.delivery_epoch
            or expiry is None
            or now >= parse_utc(expiry)
        ):
            raise StaleDeliveryError(f"stale delivery for message {message.message_id}")
