from __future__ import annotations

import sqlite3
from dataclasses import dataclass


LATEST_SCHEMA_VERSION = 4


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    sql: str


DURABLE_CONTROL_SQL = """
CREATE TABLE IF NOT EXISTS commands (
    command_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    workflow_kind TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    scope TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    determinant_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
    max_attempts INTEGER NOT NULL CHECK(max_attempts >= 1),
    input_ref TEXT NOT NULL,
    output_ref TEXT,
    correlation_id TEXT NOT NULL,
    causation_id TEXT,
    created_at TEXT NOT NULL,
    deadline_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_epoch INTEGER NOT NULL DEFAULT 0 CHECK(lease_epoch >= 0),
    lease_expires_at TEXT,
    last_error_code TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS command_attempts (
    command_id TEXT NOT NULL REFERENCES commands(command_id),
    attempt INTEGER NOT NULL CHECK(attempt >= 1),
    lease_epoch INTEGER NOT NULL CHECK(lease_epoch >= 1),
    owner TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error_code TEXT,
    PRIMARY KEY(command_id, attempt, lease_epoch)
);

CREATE TABLE IF NOT EXISTS stage_results (
    idempotency_key TEXT PRIMARY KEY,
    command_id TEXT NOT NULL REFERENCES commands(command_id),
    output_ref TEXT NOT NULL,
    output_checksum TEXT NOT NULL,
    lease_epoch INTEGER NOT NULL CHECK(lease_epoch >= 1),
    completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coverage_manifests (
    manifest_id TEXT PRIMARY KEY,
    workflow_kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    determinant_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox_events (
    outbox_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    partition_key TEXT NOT NULL,
    payload_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    available_at TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_commands_due
    ON commands(status, deadline_at, created_at);
CREATE INDEX IF NOT EXISTS idx_commands_expired_lease
    ON commands(status, lease_expires_at, lease_epoch);
CREATE INDEX IF NOT EXISTS idx_outbox_pending
    ON outbox_events(status, available_at, created_at);
CREATE INDEX IF NOT EXISTS idx_coverage_workflow_scope
    ON coverage_manifests(workflow_kind, scope, state, updated_at);
"""


LOCAL_LANE_BROKER_SQL = """
CREATE TABLE IF NOT EXISTS lane_messages (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    lane TEXT NOT NULL,
    group_key TEXT NOT NULL,
    payload_ref TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    status TEXT NOT NULL,
    available_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    max_attempts INTEGER NOT NULL CHECK(max_attempts >= 1),
    delivery_epoch INTEGER NOT NULL DEFAULT 0 CHECK(delivery_epoch >= 0),
    lease_owner TEXT,
    lease_expires_at TEXT,
    supersession_key TEXT,
    published_at TEXT NOT NULL,
    acknowledged_at TEXT,
    dead_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS lane_group_state (
    lane TEXT NOT NULL,
    group_key TEXT NOT NULL,
    last_claim_order INTEGER NOT NULL CHECK(last_claim_order >= 1),
    PRIMARY KEY(lane, group_key)
);

CREATE INDEX IF NOT EXISTS idx_lane_messages_eligible
    ON lane_messages(lane, status, available_at, sequence);
CREATE INDEX IF NOT EXISTS idx_lane_messages_group
    ON lane_messages(lane, group_key, status, sequence);
CREATE INDEX IF NOT EXISTS idx_lane_messages_supersession
    ON lane_messages(lane, supersession_key, status, sequence);
"""


PR_GATE_SQL = """
CREATE TABLE IF NOT EXISTS pr_gate_checks (
    check_id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    pr_number INTEGER NOT NULL CHECK(pr_number >= 1),
    head_sha TEXT NOT NULL,
    environment TEXT NOT NULL,
    environment_version TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK(verdict IN ('PASS', 'WARN', 'BLOCK')),
    policy_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pr_gate_head
    ON pr_gate_checks(repo, pr_number, head_sha, updated_at);
"""


MIGRATIONS = (
    Migration(2, DURABLE_CONTROL_SQL),
    Migration(3, LOCAL_LANE_BROKER_SQL),
    Migration(4, PR_GATE_SQL),
)


def current_schema_version(connection: sqlite3.Connection) -> int:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if table is None:
        return 0
    row = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    return int(row[0])


def apply_migrations(
    connection: sqlite3.Connection,
    legacy_schema_sql: str,
    target_version: int | None = None,
) -> int:
    target = LATEST_SCHEMA_VERSION if target_version is None else target_version
    if target < 1 or target > LATEST_SCHEMA_VERSION:
        raise ValueError(f"target schema version must be between 1 and {LATEST_SCHEMA_VERSION}")

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    current = current_schema_version(connection)
    if current > target:
        raise ValueError(f"database schema {current} is newer than requested target {target}")

    if current < 1:
        connection.executescript(legacy_schema_sql)
        connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")
        current = 1

    for migration in MIGRATIONS:
        if current < migration.version <= target:
            connection.executescript(migration.sql)
            connection.execute(
                "INSERT INTO schema_migrations(version) VALUES (?)", (migration.version,)
            )
            current = migration.version
    return current
