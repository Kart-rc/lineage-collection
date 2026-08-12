"""A real Spark OpenLineage event must corroborate the Spark cell's static claim.

This is the payoff for making object-store identity catalog-owned. OpenLineage names the
dataset by bucket (`s3://raw-lake` + `raw/events/orders`); the analyzer names it by
owning system (`lakehouse`). If those do not converge on one URN, `merge_runtime_
observation` — which matches on exact URN equality — silently never corroborates, and
every Spark edge is stuck at PROBABLE forever.
"""

import json
from pathlib import Path

from lineage_api.domain.confidence import derive_band
from lineage_api.domain.product_confidence import project_confidence
from lineage_api.services.consolidation import ConsolidationService
from lineage_api.services.resolver import (
    RawName,
    ResolveContext,
    ResolvedName,
    Resolver,
)
from lineage_api.services.spark_sca import analyze_spark_sources

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
EVENT = ROOT / "fixtures" / "runtime" / "openlineage" / "spark-orders-curation.json"
REPO = ROOT / "fixtures" / "repositories" / "spark-orders-etl"


def _resolver() -> Resolver:
    return Resolver.from_path(CATALOG)


def _sca_element_edges() -> set[tuple[str, str]]:
    """The static claim, as element URN pairs."""
    resolver = _resolver()
    analysis = analyze_spark_sources(
        {p.relative_to(REPO).as_posix(): p.read_text() for p in sorted(REPO.rglob("*.java"))}
    )
    context = ResolveContext(
        env="staging", platform="s3", system="lakehouse", repo="spark-orders-etl",
        digest="1" * 40, config={}, snapshot_id=resolver.snapshot_id,
    )
    edges: set[tuple[str, str]] = set()
    for job in analysis.jobs:
        if not job.mappings or "://" not in job.source:
            continue
        source = resolver.resolve(RawName("dataset", job.source, "SCA", ()), context)
        target = resolver.resolve(RawName("dataset", job.target, "SCA", ()), context)
        if not isinstance(source, ResolvedName) or not isinstance(target, ResolvedName):
            continue
        for mapping in job.mappings:
            for column in mapping.source_columns:
                edges.add((f"{source.urn}#{column}", f"{target.urn}#{mapping.target_column}"))
    return edges


def _runtime_element_edges() -> set[tuple[str, str]]:
    """The same claim as OpenLineage reports it, resolved the runtime way."""
    service = ConsolidationService(database=None, resolver=_resolver())  # type: ignore[arg-type]
    event = json.loads(EVENT.read_text())
    edges: set[tuple[str, str]] = set()
    for output in event["outputs"]:
        target = service._resolve_runtime_dataset(
            f"{output['namespace']}/{output['name']}", "staging"
        )
        fields = output["facets"]["columnLineage"]["fields"]
        for target_field, mapping in fields.items():
            for item in mapping["inputFields"]:
                source = service._resolve_runtime_dataset(
                    f"{item['namespace']}/{item['name']}", "staging"
                )
                edges.add((f"{source}#{item['field']}", f"{target}#{target_field}"))
    return edges


def test_the_runtime_event_names_the_same_elements_as_the_analyzer() -> None:
    runtime = _runtime_element_edges()

    assert runtime, "the OpenLineage event produced no element edges"
    assert runtime <= _sca_element_edges(), (
        "runtime named elements the analyzer never claimed; they would be discarded "
        "as runtime-only"
    )


def test_a_corroborated_spark_column_edge_reaches_verified() -> None:
    corroborated = _runtime_element_edges() & _sca_element_edges()
    assert corroborated

    band = derive_band({"SCA", "RUNTIME"})
    confidence = project_confidence(
        band,
        [{"mechanism": "SCA"},
         {"mechanism": "RUNTIME", "observedAt": "2026-08-12T10:00:00Z"}],
    )

    assert band == "HIGH"
    assert confidence.display_band == "VERIFIED"
    assert confidence.percent == 92
    assert confidence.last_observed == "2026-08-12T10:00:00Z"


def test_the_bucket_never_leaks_into_the_urn() -> None:
    """`raw-lake` is a bucket; `lakehouse` is the owning system that publication gates."""
    for source, target in _runtime_element_edges():
        assert ":lakehouse:" in source and ":lakehouse:" in target
        assert "raw-lake" not in source and "raw-lake" not in target
