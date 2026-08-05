from __future__ import annotations

from pathlib import Path

import pytest


EXPECTED_TABLES = {
    "audit_events",
    "catalog_snapshots",
    "classification_decisions",
    "command_attempts",
    "commands",
    "coverage_manifests",
    "edge_ledger",
    "events",
    "evidence_objects",
    "graph_edges",
    "graph_versions",
    "outbox_events",
    "pointers",
    "proposals",
    "publish_reservations",
    "quarantines",
    "run_stages",
    "runs",
    "schema_migrations",
    "stage_results",
}


def _database_type():
    try:
        from lineage_api.db import Database
    except ModuleNotFoundError:
        pytest.fail("Database is not implemented")
    return Database


def test_database_enables_integrity_pragmas_and_creates_platform_tables(tmp_path: Path) -> None:
    database_type = _database_type()
    database = database_type(tmp_path / "lineage.db")

    database.initialize()

    with database.connection() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert tables >= EXPECTED_TABLES
    assert database.schema_version() == 2


def test_transactions_roll_back_on_failure(tmp_path: Path) -> None:
    database_type = _database_type()
    database = database_type(tmp_path / "lineage.db")
    database.initialize()

    with pytest.raises(RuntimeError, match="stop"):
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO events(event_id, payload_json, outcome, created_at) VALUES (?, ?, ?, ?)",
                ("delivery-1", "{}", "ACCEPTED", "2026-08-04T12:00:00Z"),
            )
            raise RuntimeError("stop")

    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_migration_upgrades_legacy_schema_without_losing_control_or_graph_data(
    tmp_path: Path,
) -> None:
    database_type = _database_type()
    database = database_type(tmp_path / "lineage.db")
    database.initialize(target_version=1)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO events(event_id, payload_json, outcome, created_at) VALUES (?, ?, ?, ?)",
            ("delivery-legacy", "{}", "ACCEPTED", "2026-08-04T12:00:00Z"),
        )
        connection.execute(
            """
            INSERT INTO graph_versions(env, version, state, checksum, created_at)
            VALUES ('staging', 'v1', 'ACTIVE', ?, '2026-08-04T12:00:00Z')
            """,
            ("0" * 64,),
        )
        connection.execute(
            """
            INSERT INTO pointers(env, active_version, fencing_token, updated_at)
            VALUES ('staging', 'v1', 7, '2026-08-04T12:00:00Z')
            """
        )

    assert database.schema_version() == 1
    database.initialize()

    assert database.schema_version() == 2
    with database.connection() as connection:
        event = connection.execute(
            "SELECT event_id, outcome FROM events WHERE event_id = 'delivery-legacy'"
        ).fetchone()
        pointer = connection.execute(
            "SELECT active_version, fencing_token FROM pointers WHERE env = 'staging'"
        ).fetchone()
    assert dict(event) == {"event_id": "delivery-legacy", "outcome": "ACCEPTED"}
    assert dict(pointer) == {"active_version": "v1", "fencing_token": 7}


def test_durable_control_schema_enforces_identity_and_has_due_work_indexes(tmp_path: Path) -> None:
    database_type = _database_type()
    database = database_type(tmp_path / "lineage.db")
    database.initialize()

    with database.connection() as connection:
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        command_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(commands)").fetchall()
        }

    assert indexes >= {
        "idx_commands_due",
        "idx_commands_expired_lease",
        "idx_outbox_pending",
        "idx_coverage_workflow_scope",
    }
    assert command_columns >= {
        "command_id",
        "idempotency_key",
        "lease_owner",
        "lease_epoch",
        "lease_expires_at",
        "deadline_at",
    }
