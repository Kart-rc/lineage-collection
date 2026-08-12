from pathlib import Path

import pytest

from lineage_api.application.runtime_stage import (
    RuntimeStageResult,
    catalog_element_resolver,
    run_runtime_stage,
)
from lineage_api.services.resolver import ResolveContext, Resolver
from lineage_api.services.runtime_verification import StaticEdge

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
PIPELINE = ROOT / "fixtures" / "repositories" / "payments-pipeline" / "pipeline.py"

RAW = "urn:ldp:staging:snowflake:payments:raw.transactions"
DAILY = "urn:ldp:staging:snowflake:payments:analytics.daily_revenue"

STATIC_EDGES = (
    StaticEdge(f"{RAW}#customer_id", f"{DAILY}#customer_id", "DERIVES", "customer_id"),
    StaticEdge(f"{RAW}#amount", f"{DAILY}#gross_revenue", "DERIVES", "SUM(amount)"),
    StaticEdge(f"{RAW}#occurred_at", f"{DAILY}#revenue_date", "DERIVES", "DATE(occurred_at)"),
)


def _resolve():
    resolver = Resolver.from_path(CATALOG)
    return catalog_element_resolver(
        resolver,
        ResolveContext(
            env="staging",
            platform="snowflake",
            system="payments",
            repo="payments-pipeline",
            digest="a" * 40,
            config={},
            snapshot_id=resolver.snapshot_id,
        ),
    )


def _run(**kwargs):
    return run_runtime_stage(
        module_path="pipeline.py",
        module_source=PIPELINE.read_text(),
        static_edges=STATIC_EDGES,
        resolve=_resolve(),
        observed_at="2026-08-12T10:00:00Z",
        **kwargs,
    )


def test_the_stage_corroborates_every_static_edge_on_a_real_run() -> None:
    result = _run(allow_execution=True)

    assert isinstance(result, RuntimeStageResult)
    assert result.verdict == "CORROBORATED"
    assert result.corroborated == 3
    assert result.static_only == 0
    assert result.runtime_only == 0


def test_the_stage_refuses_to_execute_unless_explicitly_enabled() -> None:
    with pytest.raises(Exception, match="explicitly enabled"):
        _run(allow_execution=False)


def test_every_corroborated_edge_produces_a_runtime_assertion() -> None:
    result = _run(allow_execution=True)

    assert len(result.observations) == 3
    for observation in result.observations:
        assert observation["mechanism"] == "RUNTIME"
        assert observation["runtimeScope"] == "ELEMENT"
        assert observation["sessionComplete"] is True
        assert observation["observedAt"] == "2026-08-12T10:00:00Z"


def test_a_corroborated_edge_becomes_verified_in_the_product_projection() -> None:
    from lineage_api.domain.confidence import derive_band
    from lineage_api.domain.product_confidence import project_confidence

    result = _run(allow_execution=True)
    provenance = [{"mechanism": "SCA"}, *result.observations]
    band = derive_band({"SCA", "RUNTIME"})

    confidence = project_confidence(band, provenance)

    assert confidence.display_band == "VERIFIED"
    assert confidence.signals == ("RUNTIME", "SCA")
    assert confidence.last_observed == "2026-08-12T10:00:00Z"


def test_observed_edges_carry_real_liveness() -> None:
    result = _run(allow_execution=True)

    bands = {item.band for item in result.liveness}
    assert bands == {"COLD"}
    assert all(item.observations >= 1 for item in result.liveness)
    assert all(item.session_complete for item in result.liveness)


def test_an_edge_the_run_never_exercised_is_reported_unobserved() -> None:
    extra = StaticEdge(
        f"{RAW}#customer_id",
        "urn:ldp:staging:snowflake:payments:risk.customer_features#lifetime_value",
        "DERIVES",
        "never_runs",
    )
    result = run_runtime_stage(
        module_path="pipeline.py",
        module_source=PIPELINE.read_text(),
        static_edges=(*STATIC_EDGES, extra),
        resolve=_resolve(),
        observed_at="2026-08-12T10:00:00Z",
        allow_execution=True,
    )

    assert result.verdict == "PARTIALLY_CORROBORATED"
    assert result.static_only == 1
    unobserved = [item for item in result.liveness if item.band == "UNOBSERVED"]
    assert len(unobserved) == 1
    assert unobserved[0].session_complete is True


def test_runtime_never_invents_an_edge() -> None:
    result = run_runtime_stage(
        module_path="pipeline.py",
        module_source=PIPELINE.read_text(),
        static_edges=(STATIC_EDGES[0],),
        resolve=_resolve(),
        observed_at="2026-08-12T10:00:00Z",
        allow_execution=True,
    )

    # The run witnesses three edges but only one was claimed statically; the other two
    # are reported as runtime-only and never become lineage.
    assert result.corroborated == 1
    assert result.runtime_only == 2
    assert len(result.observations) == 1
