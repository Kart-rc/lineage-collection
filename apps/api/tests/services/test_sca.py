from __future__ import annotations

import json
from pathlib import Path

import pytest

from lineage_api.services.resolver import ResolveContext, Resolver


PROJECT_ROOT = Path(__file__).parents[4]
REPOSITORY_ROOT = PROJECT_ROOT / "fixtures" / "repositories" / "payments-pipeline"
CATALOG_PATH = PROJECT_ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json"


def _analyzer_type():
    try:
        from lineage_api.services.sca import ScaAnalyzer
    except ModuleNotFoundError:
        pytest.fail("SCA analyzer is not implemented")
    return ScaAnalyzer


@pytest.fixture
def analyzer():
    analyzer_type = _analyzer_type()
    return analyzer_type(Resolver.from_path(CATALOG_PATH), ruleset_version="python-demo-v1")


@pytest.fixture
def context() -> ResolveContext:
    return ResolveContext(
        env="staging",
        platform="snowflake",
        system="payments",
        repo="payments-pipeline",
        digest="demo-digest-v2",
        config={},
        snapshot_id="catalog-demo-v1",
    )


def _analyze(analyzer, context):
    return analyzer.analyze(
        repository_root=REPOSITORY_ROOT,
        repo="payments-pipeline",
        digest="demo-digest-v2",
        scope_paths=("pipeline.py",),
        resolver_context=context,
        run_id="run-delivery-001",
        correlation_id="corr-delivery-001",
    )


def test_seeded_python_analysis_matches_expected_lineage(analyzer, context) -> None:
    result = _analyze(analyzer, context)
    expected = json.loads((REPOSITORY_ROOT / "expected-lineage.json").read_text(encoding="utf-8"))

    assert [edge.lineage_tuple for edge in result.edges] == [
        (edge["from"], edge["to"], edge["type"], edge["transform"])
        for edge in expected["edges"]
    ]
    assert result.datasets_seen == (
        "urn:ldp:staging:snowflake:payments:analytics.daily_revenue",
        "urn:ldp:staging:snowflake:payments:raw.transactions",
    )


def test_analysis_is_byte_deterministic_and_every_edge_has_exact_evidence(analyzer, context) -> None:
    first = _analyze(analyzer, context)
    second = _analyze(analyzer, context)

    assert first.to_bytes() == second.to_bytes()
    assert first.schema_version == "1.0.0"
    assert first.ruleset_version == "python-demo-v1"
    assert len(first.edges) == 3
    for edge in first.edges:
        assert edge.mechanism == "SCA"
        assert edge.exact is True
        assert edge.file == "pipeline.py"
        assert edge.line > 0
        assert edge.ast_path.startswith("Module.body[")
        assert edge.provenance_id.startswith("prov-")


def test_dynamic_names_are_explicit_residue_not_silent_skips(analyzer, context) -> None:
    result = _analyze(analyzer, context)

    assert len(result.residue) == 1
    residue = result.residue[0]
    assert residue.file == "pipeline.py"
    assert residue.line > 0
    assert residue.reason == "dynamic-name"
    assert residue.symbol == "dynamic_dataset_name"
    assert set(residue.known_urns) == set(result.datasets_seen)
    assert result.stats == {
        "filesAnalyzed": 1,
        "edgesEmitted": 3,
        "residueCount": 1,
        "quarantinedCount": 0,
    }
