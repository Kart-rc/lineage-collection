from __future__ import annotations

import sqlite3
from dataclasses import dataclass


LATEST_SCHEMA_VERSION = 8


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


DEPLOYMENT_SQL = """
CREATE TABLE IF NOT EXISTS lineage_packages (
    package_digest TEXT PRIMARY KEY,
    system TEXT NOT NULL,
    environment TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    graph_version TEXT NOT NULL,
    graph_checksum TEXT NOT NULL,
    manifest_ref TEXT NOT NULL,
    approval_ref TEXT NOT NULL,
    approved INTEGER NOT NULL CHECK(approved = 1),
    created_at TEXT NOT NULL,
    UNIQUE(system, environment, artifact_digest)
);

CREATE TABLE IF NOT EXISTS deployment_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL CHECK(event_type IN ('DEPLOYMENT', 'ROLLBACK')),
    provider TEXT NOT NULL,
    provider_sequence INTEGER NOT NULL CHECK(provider_sequence >= 1),
    attempt INTEGER NOT NULL CHECK(attempt >= 1),
    system TEXT NOT NULL,
    environment TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('SUCCEEDED', 'FAILED')),
    artifact_digest TEXT,
    correlation_id TEXT NOT NULL,
    audit_ref TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(provider, system, environment, provider_sequence, attempt)
);

CREATE TABLE IF NOT EXISTS deployment_state (
    system TEXT NOT NULL,
    environment TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_sequence INTEGER NOT NULL CHECK(provider_sequence >= 1),
    attempt INTEGER NOT NULL CHECK(attempt >= 1),
    event_id TEXT NOT NULL,
    deployed_artifact_digest TEXT,
    lineage_package_digest TEXT,
    graph_version TEXT,
    state TEXT NOT NULL,
    audit_ref TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(system, environment),
    FOREIGN KEY(event_id) REFERENCES deployment_events(event_id),
    FOREIGN KEY(lineage_package_digest) REFERENCES lineage_packages(package_digest)
);

CREATE INDEX IF NOT EXISTS idx_lineage_package_artifact
    ON lineage_packages(system, environment, artifact_digest);
CREATE INDEX IF NOT EXISTS idx_deployment_event_order
    ON deployment_events(system, environment, provider_sequence, attempt);
CREATE INDEX IF NOT EXISTS idx_deployment_state_status
    ON deployment_state(state, updated_at);
"""


RUNTIME_SQL = """
CREATE TABLE IF NOT EXISTS runtime_sessions (
    session_id TEXT PRIMARY KEY,
    token_digest TEXT NOT NULL,
    repo TEXT NOT NULL,
    environment TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    datasets_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('GRANT', 'READY', 'OBSERVING', 'DRAIN', 'CLOSED')),
    outcome TEXT CHECK(outcome IN ('COMPLETE', 'INCOMPLETE', 'EXPIRED', 'REVOKED')),
    attempted INTEGER NOT NULL DEFAULT 0 CHECK(attempted >= 0),
    accepted INTEGER NOT NULL DEFAULT 0 CHECK(accepted >= 0),
    rejected INTEGER NOT NULL DEFAULT 0 CHECK(rejected >= 0),
    duplicates INTEGER NOT NULL DEFAULT 0 CHECK(duplicates >= 0),
    expires_at TEXT NOT NULL,
    manifest_json TEXT,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_observations (
    session_id TEXT NOT NULL REFERENCES runtime_sessions(session_id),
    observation_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence >= 0),
    mechanism TEXT NOT NULL CHECK(mechanism IN ('OPENLINEAGE', 'SDK', 'OTEL')),
    granularity TEXT NOT NULL CHECK(granularity IN ('DATASET', 'ELEMENT', 'CONNECTIVITY')),
    datasets_json TEXT NOT NULL,
    payload_checksum TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(session_id, observation_id)
);

CREATE INDEX IF NOT EXISTS idx_runtime_sessions_artifact
    ON runtime_sessions(repo, environment, artifact_digest, outcome, updated_at);
CREATE INDEX IF NOT EXISTS idx_runtime_observation_sequence
    ON runtime_observations(session_id, sequence, observation_id);
"""


RESUMABLE_PUBLICATION_SQL = """
CREATE TABLE IF NOT EXISTS publication_operations (
    operation_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    proposal_version INTEGER NOT NULL CHECK(proposal_version >= 1),
    proposal_digest TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    approval_ref TEXT NOT NULL,
    env TEXT NOT NULL,
    expected_prior TEXT NOT NULL,
    package_digest TEXT NOT NULL,
    stage TEXT NOT NULL,
    token INTEGER CHECK(token >= 1),
    namespace_version TEXT,
    manifest_ref TEXT,
    edge_checksum TEXT NOT NULL,
    edge_count INTEGER NOT NULL CHECK(edge_count >= 0),
    next_edge_index INTEGER NOT NULL DEFAULT 0 CHECK(next_edge_index >= 0),
    failure_policy TEXT NOT NULL CHECK(failure_policy IN ('RETAIN', 'DISCARD')),
    result_json TEXT,
    terminal_outcome TEXT,
    correlation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    terminal_at TEXT,
    UNIQUE(env, proposal_id, proposal_version, approval_id)
);

CREATE INDEX IF NOT EXISTS idx_publication_stage
    ON publication_operations(stage, updated_at);
"""


RUNTIME_POLICY_SQL = """
CREATE TABLE IF NOT EXISTS runtime_profiles (
    profile_id TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    profile_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('DISABLED', 'ENABLED')),
    policy_epoch INTEGER NOT NULL DEFAULT 0 CHECK(policy_epoch >= 0),
    actor TEXT NOT NULL,
    enabled_by TEXT,
    enabled_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_version)
);

CREATE TABLE IF NOT EXISTS runtime_kill_switches (
    scope_type TEXT NOT NULL CHECK(scope_type IN ('GLOBAL', 'ENVIRONMENT', 'WORKLOAD', 'MECHANISM', 'PROFILE', 'DATASET')),
    scope_value TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(scope_type, scope_value)
);

CREATE TABLE IF NOT EXISTS runtime_artifact_attestations (
    profile_id TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('APPROVED', 'REVOKED')),
    evidence_ref TEXT NOT NULL,
    actor TEXT NOT NULL,
    revoked_reason TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_version, artifact_digest),
    FOREIGN KEY(profile_id, profile_version) REFERENCES runtime_profiles(profile_id, profile_version)
);

CREATE TABLE IF NOT EXISTS runtime_leases (
    lease_id TEXT PRIMARY KEY,
    token_digest TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    profile_digest TEXT NOT NULL,
    workload_id TEXT NOT NULL,
    repo TEXT NOT NULL,
    environment TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    mechanism TEXT NOT NULL CHECK(mechanism IN ('OPENLINEAGE', 'SDK', 'OTEL', 'DASK')),
    datasets_json TEXT NOT NULL,
    permitted_granularity_json TEXT NOT NULL,
    workload_identity TEXT NOT NULL,
    window_id TEXT NOT NULL,
    policy_epoch INTEGER NOT NULL CHECK(policy_epoch >= 1),
    state TEXT NOT NULL CHECK(state IN ('ACTIVE', 'REVOKED', 'EXPIRED', 'DISABLED')),
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    renewed_at TEXT,
    revoked_reason TEXT,
    actor TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(profile_id, profile_version) REFERENCES runtime_profiles(profile_id, profile_version)
);

CREATE INDEX IF NOT EXISTS idx_runtime_profiles_state
    ON runtime_profiles(state, profile_id, profile_version);
CREATE INDEX IF NOT EXISTS idx_runtime_leases_scope
    ON runtime_leases(state, environment, workload_id, mechanism, expires_at);
CREATE INDEX IF NOT EXISTS idx_runtime_artifact_attestation
    ON runtime_artifact_attestations(state, profile_id, profile_version, artifact_digest);
CREATE INDEX IF NOT EXISTS idx_runtime_kill_switch_active
    ON runtime_kill_switches(active, scope_type, scope_value);
"""


MIGRATIONS = (
    Migration(2, DURABLE_CONTROL_SQL),
    Migration(3, LOCAL_LANE_BROKER_SQL),
    Migration(4, PR_GATE_SQL),
    Migration(5, DEPLOYMENT_SQL),
    Migration(6, RUNTIME_SQL),
    Migration(7, RESUMABLE_PUBLICATION_SQL),
    Migration(8, RUNTIME_POLICY_SQL),
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
