from __future__ import annotations

import json

from lineage_api.db import Database


class SQLitePrGateCheckStore:
    """The sole local write boundary for stable PR checks and their audit payload."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def upsert(self, result: dict[str, object]) -> dict[str, object]:
        payload = json.dumps(result, sort_keys=True, separators=(",", ":"))
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO pr_gate_checks(
                    check_id, repo, pr_number, head_sha, environment,
                    environment_version, verdict, policy_version, payload_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(check_id) DO UPDATE SET
                    environment_version = excluded.environment_version,
                    verdict = excluded.verdict,
                    policy_version = excluded.policy_version,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    result["checkId"],
                    result["repo"],
                    result["prNumber"],
                    result["headSha"],
                    result["environment"],
                    result["environmentVersion"],
                    result["verdict"],
                    result["policyVersion"],
                    payload,
                    result["evaluatedAt"],
                ),
            )
        return result
