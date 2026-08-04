from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parents[3]
FIXTURE_ROOT = PROJECT_ROOT / "fixtures"


def _seed_types():
    try:
        from lineage_api.db import Database
        from lineage_api.seed import reset_demo
    except ModuleNotFoundError:
        pytest.fail("Demo seed behavior is not implemented")
    return Database, reset_demo


def test_catalog_and_repository_fixtures_are_contract_aligned() -> None:
    catalog_path = FIXTURE_ROOT / "catalog" / "catalog-snapshot-v1.json"
    repository_path = FIXTURE_ROOT / "repositories" / "payments-pipeline" / "pipeline.py"
    expected_path = FIXTURE_ROOT / "repositories" / "payments-pipeline" / "expected-lineage.json"

    assert catalog_path.is_file()
    assert repository_path.is_file()
    assert expected_path.is_file()

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    dataset_names = {dataset["name"] for dataset in catalog["datasets"]}
    assert dataset_names >= {"raw.transactions", "analytics.daily_revenue"}
    assert "dynamic_dataset_name" in repository_path.read_text(encoding="utf-8")


def test_demo_reset_is_deterministic_and_pins_catalog_snapshot(tmp_path: Path) -> None:
    database_type, reset_demo = _seed_types()
    database = database_type(tmp_path / "lineage.db")
    database.initialize()

    first = reset_demo(database, FIXTURE_ROOT)
    first_state = database.snapshot(
        ["catalog_snapshots", "graph_versions", "graph_edges", "pointers"]
    )
    second = reset_demo(database, FIXTURE_ROOT)
    second_state = database.snapshot(
        ["catalog_snapshots", "graph_versions", "graph_edges", "pointers"]
    )

    catalog_bytes = (FIXTURE_ROOT / "catalog" / "catalog-snapshot-v1.json").read_bytes()
    assert first == second
    assert first.catalog_digest == hashlib.sha256(catalog_bytes).hexdigest()
    assert first.baseline_version == "v1"
    assert first_state == second_state
    assert second_state["pointers"][0]["active_version"] == "v1"
