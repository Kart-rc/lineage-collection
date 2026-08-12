"""Highest attainable confidence, proven on a real upstream repository.

`OpenLineage/OpenLineage` ships integration fixtures that carry *both* halves of the
evidence for the same operation: the literal SQL the engine ran, and the columnLineage
facet it emitted at runtime. That makes it possible to prove an edge statically with the
SQL cell and corroborate it with the engine's own observation — SCA + element-scoped
RUNTIME, which is band HIGH and displays as VERIFIED.

HIGHEST is not reachable: it requires an LLM assertion as well, and no LLM producer is
configured. VERIFIED is the ceiling and is what this test pins.
"""

import json
from pathlib import Path

import pytest

from lineage_api.domain.confidence import derive_band
from lineage_api.domain.product_confidence import project_confidence
from lineage_api.services.resolver import (
    RawName,
    ResolveContext,
    ResolvedName,
    Resolver,
)
from lineage_api.services.sql_transformation_sca import (
    SqlTransformationSource,
    analyze_sql_sources,
    compile_derivation_edges,
)

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
FIXTURE = Path(
    "/private/tmp/lineage-estate/OpenLineage/integration/hive/hive-openlineage-hook"
    "/integrations/container/cllSimpleInsertFromTableComplete.json"
)

pytestmark = pytest.mark.skipif(
    not FIXTURE.exists(), reason="OpenLineage checkout not present"
)


def _event() -> dict:
    return json.loads(FIXTURE.read_text())


def _resolver() -> Resolver:
    return Resolver.from_path(CATALOG)


def _context(resolver: Resolver) -> ResolveContext:
    return ResolveContext(
        env="staging", platform="hive", system="warehouse", repo="OpenLineage",
        digest="9" * 40, config={}, snapshot_id=resolver.snapshot_id,
    )


def _table_columns(resolver: Resolver, context: ResolveContext):
    """Ordered columns for a table, from the catalog — the authority on column order."""

    def lookup(table: str) -> tuple[str, ...] | None:
        result = resolver.resolve(RawName("dataset", table, "SCA", ()), context)
        if not isinstance(result, ResolvedName):
            return None
        for dataset in json.loads(CATALOG.read_text())["datasets"]:
            if str(result.catalog_ref) == str(dataset["catalogRef"]):
                return tuple(str(e["name"]) for e in dataset.get("elements", []))
        return None

    return lookup


def _sca_edges():
    """The static claim, from the SQL the engine actually ran."""
    resolver = _resolver()
    context = _context(resolver)
    query = _event()["job"]["facets"]["sql"]["query"]
    analysis = analyze_sql_sources(
        (SqlTransformationSource("hive/query.sql", query.encode(), "hive"),),
        table_columns=_table_columns(resolver, context),
    )
    return analysis, compile_derivation_edges(
        analysis, resolver, context,
        repo="OpenLineage", digest="9" * 40, run_id="run-1",
        correlation_id="corr-1", ruleset_version="sql-transformation-rules-v1",
    )


def _runtime_edges() -> set[tuple[str, str]]:
    """The engine's own observation, resolved the runtime way."""
    from lineage_api.services.consolidation import ConsolidationService

    service = ConsolidationService(database=None, resolver=_resolver())  # type: ignore[arg-type]
    event = _event()
    edges: set[tuple[str, str]] = set()
    for output in event["outputs"]:
        target = service._resolve_runtime_dataset(
            f"{output['namespace']}/{output['name']}", "staging"
        )
        for field, mapping in output["facets"]["columnLineage"]["fields"].items():
            for item in mapping["inputFields"]:
                source = service._resolve_runtime_dataset(
                    f"{item['namespace']}/{item['name']}", "staging"
                )
                edges.add((f"{source}#{item['field']}", f"{target}#{field}"))
    return edges


def test_the_real_sql_yields_column_level_static_edges() -> None:
    analysis, (edges, residue) = _sca_edges()

    assert analysis.residue == (), analysis.residue
    assert residue == (), residue
    assert {(e.from_urn.rsplit("#", 1)[-1], e.to_urn.rsplit("#", 1)[-1]) for e in edges} == {
        ("a", "a"),
        ("b", "b"),
    }


def test_the_engine_observation_names_the_same_elements() -> None:
    _, (edges, _) = _sca_edges()
    sca = {(e.from_urn, e.to_urn) for e in edges}

    runtime = _runtime_edges()

    assert runtime, "the fixture produced no runtime element edges"
    assert runtime == sca, f"runtime {runtime} != sca {sca}"


def test_the_corroborated_edges_reach_verified() -> None:
    _, (edges, _) = _sca_edges()
    corroborated = _runtime_edges() & {(e.from_urn, e.to_urn) for e in edges}
    assert len(corroborated) == 2

    band = derive_band({"SCA", "RUNTIME"})
    confidence = project_confidence(
        band,
        [{"mechanism": "SCA"},
         {"mechanism": "RUNTIME", "observedAt": "2026-08-12T10:00:00Z"}],
    )

    assert band == "HIGH"
    assert confidence.display_band == "VERIFIED"
    assert confidence.percent == 92
    assert confidence.signals == ("RUNTIME", "SCA")


def test_highest_remains_unreachable_and_is_not_faked() -> None:
    """HIGHEST needs an LLM assertion; no producer exists, so VERIFIED is the ceiling."""
    assert derive_band({"SCA", "RUNTIME"}) == "HIGH"
    assert derive_band({"SCA", "LLM", "RUNTIME"}) == "HIGHEST"
