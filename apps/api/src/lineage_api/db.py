from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any


DDL = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    outcome TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(event_id),
    repo TEXT NOT NULL,
    digest TEXT NOT NULL,
    env TEXT NOT NULL,
    system TEXT NOT NULL,
    state TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    failed_stage TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    sequence INTEGER NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(run_id, sequence)
);

CREATE TABLE IF NOT EXISTS classification_decisions (
    decision_id TEXT PRIMARY KEY,
    repo_or_path TEXT NOT NULL,
    repository_class TEXT NOT NULL,
    baseline_treatment TEXT NOT NULL,
    on_change_treatment TEXT NOT NULL,
    evidence_level INTEGER NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    status TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    supersedes TEXT
);

CREATE TABLE IF NOT EXISTS catalog_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    checksum TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    activated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quarantines (
    quarantine_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    reason TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS evidence_objects (
    kind TEXT NOT NULL,
    object_key TEXT NOT NULL,
    checksum TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(kind, object_key)
);

CREATE TABLE IF NOT EXISTS edge_ledger (
    edge_key TEXT NOT NULL,
    version INTEGER NOT NULL,
    system TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(edge_key, version)
);

CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    system TEXT NOT NULL,
    state TEXT NOT NULL,
    expected_base_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    lock_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(proposal_id, version)
);

CREATE TABLE IF NOT EXISTS graph_versions (
    env TEXT NOT NULL,
    version TEXT NOT NULL,
    state TEXT NOT NULL,
    manifest_ref TEXT,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(env, version)
);

CREATE TABLE IF NOT EXISTS graph_edges (
    env TEXT NOT NULL,
    version TEXT NOT NULL,
    edge_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(env, version, edge_key),
    FOREIGN KEY(env, version) REFERENCES graph_versions(env, version)
);

CREATE TABLE IF NOT EXISTS pointers (
    env TEXT PRIMARY KEY,
    active_version TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publish_reservations (
    env TEXT PRIMARY KEY,
    token INTEGER NOT NULL,
    expected_prior TEXT NOT NULL,
    status TEXT NOT NULL,
    reserved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    audit_id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_state ON runs(state, updated_at);
CREATE INDEX IF NOT EXISTS idx_run_stages_run ON run_stages(run_id, sequence);
CREATE INDEX IF NOT EXISTS idx_classification_status ON classification_decisions(status, decided_at);
CREATE INDEX IF NOT EXISTS idx_quarantine_reason ON quarantines(reason, created_at);
CREATE INDEX IF NOT EXISTS idx_edge_ledger_system ON edge_ledger(system, status);
CREATE INDEX IF NOT EXISTS idx_proposals_queue ON proposals(system, state, created_at);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(DDL)
            connection.commit()

    def snapshot(self, table_names: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        with self.connection() as connection:
            known_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            for table_name in table_names:
                if table_name not in known_tables:
                    raise ValueError(f"Unknown table: {table_name}")
                columns = [
                    row[1]
                    for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                ]
                order_clause = ", ".join(f'"{column}"' for column in columns)
                rows = connection.execute(
                    f'SELECT * FROM "{table_name}" ORDER BY {order_clause}'
                ).fetchall()
                result[table_name] = [dict(row) for row in rows]
        return result
