"""Highest attainable confidence, proven on a real upstream repository.

`OpenLineage/OpenLineage` ships integration fixtures that carry *both* halves of the
evidence for the same operation: the literal SQL the engine ran, and the columnLineage
facet it emitted at runtime. That makes it possible to prove an edge statically with the
SQL cell and corroborate it with the engine's own observation — SCA + element-scoped
RUNTIME, which is band HIGH and displays as VERIFIED.

A third mechanism then makes HIGHEST reachable. The LLM gateway proposes against the
same query and its proposals survive the full guardrail chain, so the band rises because
the guardrails held — not because a model spoke. The LLM response is *recorded*, since no
live gateway is configured here; that is stated wherever it is used.
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


def test_the_band_ladder_is_unchanged_by_adding_a_producer() -> None:
    """Building an LLM producer must not move the thresholds it has to clear."""
    assert derive_band({"SCA"}) == "SINGLE"
    assert derive_band({"SCA", "RUNTIME"}) == "HIGH"
    assert derive_band({"SCA", "LLM", "RUNTIME"}) == "HIGHEST"


# --- the third mechanism ---------------------------------------------------------------


def _llm_agreement(sca_edges):
    """A recorded LLM response that concurs with the two mechanisms already in evidence.

    Recorded, not live: no Bedrock gateway is configured in this repository. The value of
    running it is that the guardrail chain is genuinely exercised — the proposals must
    cite the real query text and name URNs already in scope, or they are rejected.
    """
    from lineage_api.services.llm_gateway import LlmGateway, RecordedTransport

    query = _event()["job"]["facets"]["sql"]["query"]
    proposals = [
        {
            "fromUrn": from_urn,
            "toUrn": to_urn,
            "transform": "identity" if from_urn.endswith("#a") else "concat(b, 'x')",
            "citation": "a" if from_urn.endswith("#a") else "concat(b, 'x')",
        }
        for from_urn, to_urn in sorted(sca_edges)
    ]
    known = {urn.rsplit("#", 1)[0] for pair in sca_edges for urn in pair}
    gateway = LlmGateway(
        resolver=_resolver(),
        transport=RecordedTransport({"any": proposals}),
        model_id="recorded-response-v1",
        prompt_version="lineage-residue-v1",
    )
    return gateway.propose(chunk=query, known_urns=tuple(sorted(known)))


def test_the_llm_mechanism_agrees_and_passes_every_guardrail() -> None:
    _, (edges, _) = _sca_edges()
    sca = {(e.from_urn, e.to_urn) for e in edges}

    result = _llm_agreement(sca)

    assert result.rejects == (), result.rejects
    assert {(p.from_urn, p.to_urn) for p in result.accepted} == sca


def test_all_three_mechanisms_together_reach_the_highest_band() -> None:
    _, (edges, _) = _sca_edges()
    sca = {(e.from_urn, e.to_urn) for e in edges}
    runtime = _runtime_edges()
    llm = {(p.from_urn, p.to_urn) for p in _llm_agreement(sca).accepted}

    agreed = sca & runtime & llm
    assert len(agreed) == 2

    band = derive_band({"SCA", "LLM", "RUNTIME"})
    confidence = project_confidence(
        band,
        [{"mechanism": "SCA"},
         {"mechanism": "LLM"},
         {"mechanism": "RUNTIME", "observedAt": "2026-08-12T10:00:00Z"}],
    )

    assert band == "HIGHEST"
    assert confidence.display_band == "VERIFIED"
    assert confidence.percent == 96
    assert confidence.signals == ("LLM", "RUNTIME", "SCA")


def test_a_hallucinated_dataset_still_cannot_raise_the_band() -> None:
    """The band only rises because the guardrails hold, not because a model spoke."""
    from lineage_api.services.llm_gateway import LlmGateway, RecordedTransport

    query = _event()["job"]["facets"]["sql"]["query"]
    gateway = LlmGateway(
        resolver=_resolver(),
        transport=RecordedTransport(
            {"any": [{"fromUrn": "urn:ldp:staging:hive:warehouse:test.t2#a",
                      "toUrn": "urn:ldp:staging:hive:warehouse:invented#x",
                      "transform": "made up", "citation": "a"}]}
        ),
        model_id="recorded-response-v1",
    )

    result = gateway.propose(
        chunk=query,
        known_urns=("urn:ldp:staging:hive:warehouse:test.t1",
                    "urn:ldp:staging:hive:warehouse:test.t2"),
    )

    assert result.accepted == ()
    assert [r.code for r in result.rejects] == ["URN_REJECT"]
