from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from lineage_api.db import Database


SEED_TIMESTAMP = "2026-08-04T12:00:00Z"


@dataclass(frozen=True, slots=True)
class SeedSummary:
    catalog_digest: str
    baseline_version: str
    catalog_dataset_count: int


def reset_demo(database: Database, fixture_root: Path) -> SeedSummary:
    catalog_path = fixture_root / "catalog" / "catalog-snapshot-v1.json"
    catalog_bytes = catalog_path.read_bytes()
    catalog = json.loads(catalog_bytes)
    catalog_digest = hashlib.sha256(catalog_bytes).hexdigest()

    delete_order = (
        "stage_results",
        "command_attempts",
        "lane_messages",
        "lane_group_state",
        "outbox_events",
        "coverage_manifests",
        "commands",
        "run_stages",
        "runs",
        "events",
        "classification_decisions",
        "quarantines",
        "edge_ledger",
        "proposals",
        "graph_edges",
        "publish_reservations",
        "pointers",
        "graph_versions",
        "evidence_objects",
        "audit_events",
        "catalog_snapshots",
    )

    with database.transaction() as connection:
        for table_name in delete_order:
            connection.execute(f'DELETE FROM "{table_name}"')
        connection.execute(
            """
            INSERT INTO catalog_snapshots(snapshot_id, checksum, payload_json, activated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                catalog["snapshotId"],
                catalog_digest,
                json.dumps(catalog, sort_keys=True, separators=(",", ":")),
                SEED_TIMESTAMP,
            ),
        )
        connection.execute(
            """
            INSERT INTO graph_versions(env, version, state, manifest_ref, checksum, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "staging",
                "v1",
                "ACTIVE",
                "seed://baseline-v1",
                hashlib.sha256(b"[]").hexdigest(),
                SEED_TIMESTAMP,
            ),
        )
        connection.execute(
            """
            INSERT INTO pointers(env, active_version, fencing_token, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            ("staging", "v1", 0, SEED_TIMESTAMP),
        )

    return SeedSummary(
        catalog_digest=catalog_digest,
        baseline_version="v1",
        catalog_dataset_count=len(catalog["datasets"]),
    )
