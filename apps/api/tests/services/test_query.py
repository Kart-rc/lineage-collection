from __future__ import annotations

from pathlib import Path

import pytest

from lineage_api.db import Database
from lineage_api.domain.errors import DomainError


SOURCE = "urn:ldp:staging:snowflake:payments:raw.transactions#amount"
MIDDLE = "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue"
TARGET = "urn:ldp:staging:snowflake:payments:risk.customer_features#lifetime_value"
EDGE_ONE = {
    "schemaVersion": "1.0.0",
    "edgeKey": "edge-one",
    "version": 2,
    "from": [SOURCE],
    "to": MIDDLE,
    "edgeType": "DERIVES",
    "band": "HIGH",
    "corroboration": "ELEMENT",
    "status": "PUBLISHED",
    "transform": "SUM(amount)",
    "provenance": [{"provenanceId": "prov-1", "mechanism": "SCA"}],
    "autoPublishable": True,
    "system": "payments",
    "updatedAt": "2026-08-04T18:00:00Z",
}
EDGE_TWO = {
    **EDGE_ONE,
    "edgeKey": "edge-two",
    "from": [MIDDLE],
    "to": TARGET,
    "band": "MEDIUM",
    "corroboration": "NONE",
    "transform": "gross_revenue",
}


def _query_type():
    try:
        from lineage_api.services.query import QueryService
    except ModuleNotFoundError:
        pytest.fail("Query service is not implemented")
    return QueryService


@pytest.fixture
def query(tmp_path: Path):
    database = Database(tmp_path / "lineage.db")
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO graph_versions(env, version, state, checksum, created_at)
            VALUES ('staging', 'v1', 'PRIOR', ?, '2026-08-04T12:00:00Z')
            """,
            ("0" * 64,),
        )
        connection.execute(
            """
            INSERT INTO graph_versions(env, version, state, checksum, created_at)
            VALUES ('staging', 'v2', 'ACTIVE', ?, '2026-08-04T18:00:00Z')
            """,
            ("1" * 64,),
        )
        for edge in (EDGE_ONE, EDGE_TWO):
            connection.execute(
                """
                INSERT INTO graph_edges(env, version, edge_key, payload_json)
                VALUES ('staging', 'v2', ?, ?)
                """,
                (edge["edgeKey"], __import__("json").dumps(edge, sort_keys=True)),
            )
        connection.execute(
            """
            INSERT INTO pointers(env, active_version, fencing_token, updated_at)
            VALUES ('staging', 'v2', 1, '2026-08-04T18:00:00Z')
            """
        )
    return _query_type()(database, env="staging")


def test_lineage_reads_through_active_pointer_and_supports_history_pin(query) -> None:
    active = query.lineage(SOURCE, direction="down", depth=2)
    historical = query.lineage(SOURCE, direction="down", depth=2, version="v1")

    assert active["namespaceVersion"] == "v2"
    assert [edge["edgeKey"] for edge in active["edges"]] == ["edge-one", "edge-two"]
    assert {node["urn"] for node in active["nodes"]} == {SOURCE, MIDDLE, TARGET}
    assert historical["namespaceVersion"] == "v1"
    assert historical["edges"] == []


def test_edge_detail_always_exposes_band_and_provenance(query) -> None:
    detail = query.edge_detail("edge-one")

    assert detail["band"] == "HIGH"
    assert detail["corroboration"] == "ELEMENT"
    assert detail["provenance"][0]["mechanism"] == "SCA"


def test_impact_uses_path_confidence_and_returns_l11_response(query) -> None:
    impact = query.impact(SOURCE, "COLUMN_DROP", depth=5)

    assert impact["subject"] == SOURCE
    assert impact["changeType"] == "COLUMN_DROP"
    assert impact["namespaceVersion"] == "v2"
    assert impact["depthSearched"] == 5
    assert impact["truncated"] is False
    assert [(item["urn"], item["severity"], item["pathLength"]) for item in impact["affected"]] == [
        (MIDDLE, "BLOCK", 1),
        (TARGET, "WARN", 2),
    ]
    assert impact["summary"] == {"block": 1, "warn": 1, "info": 0}


def test_depth_above_five_is_rejected(query) -> None:
    with pytest.raises(DomainError) as captured:
        query.lineage(SOURCE, direction="down", depth=6)

    assert captured.value.code == "DEPTH_EXCEEDED"
