from __future__ import annotations

from pathlib import Path

import pytest


EXPECTED_TABLES = {
    "audit_events",
    "catalog_snapshots",
    "classification_decisions",
    "edge_ledger",
    "events",
    "evidence_objects",
    "graph_edges",
    "graph_versions",
    "pointers",
    "proposals",
    "publish_reservations",
    "quarantines",
    "run_stages",
    "runs",
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
