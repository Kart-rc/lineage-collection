from __future__ import annotations

import json
from collections.abc import Mapping

from lineage_api.application.collections import TERMINAL_COMMAND_STATUSES
from lineage_api.db import Database


MAX_DOCUMENT_BYTES = 256 * 1024


class SQLiteCollectionStore:
    """Durable, idempotent projection of submitted repository collections."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def record(self, document: Mapping[str, object]) -> None:
        command_id = document.get("commandId")
        if not isinstance(command_id, str) or not command_id:
            raise ValueError("a durable command identifier is required")
        payload = json.dumps(document, sort_keys=True, separators=(",", ":"))
        if len(payload.encode()) > MAX_DOCUMENT_BYTES:
            raise ValueError("collection projection exceeds its closed bound")
        command_status = str(document.get("commandStatus") or "")
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO repository_collections(
                    command_id, origin, repository, revision, environment, system,
                    source_type, command_status, terminal, document_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(command_id) DO UPDATE SET
                    command_status = excluded.command_status,
                    terminal = excluded.terminal,
                    document_json = excluded.document_json
                """,
                (
                    command_id,
                    str(document.get("origin") or ""),
                    str(document.get("repository") or ""),
                    str(document.get("revision") or ""),
                    str(document.get("environment") or ""),
                    str(document.get("system") or ""),
                    str(document.get("sourceType") or ""),
                    command_status,
                    1 if command_status in TERMINAL_COMMAND_STATUSES else 0,
                    payload,
                ),
            )

    def get(self, command_id: str) -> Mapping[str, object] | None:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT document_json FROM repository_collections WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        if row is None:
            return None
        document = json.loads(row["document_json"])
        if not isinstance(document, dict):
            raise ValueError("stored collection projection is not an object")
        return document
