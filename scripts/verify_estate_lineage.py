"""Trace one estate end to end across every cell, and show what each hop is worth.

The estate spans four technologies and three repositories:

    kafka topic --stream processor--> kafka topic      (Spring Cloud Stream bindings)
    kafka topic --spark streaming--> s3 landing        (Spark structured streaming)
    s3 landing  --spark batch-----> s3 curated         (column level, with transforms)

plus a Java service writing its own relational tables, analysed by the Spring cell.

Each hop is scored by the mechanisms that proved it, so the difference between a
statically-proven edge and a runtime-corroborated one is visible rather than asserted.

Run: uv run --project apps/api python scripts/verify_estate_lineage.py [estate-root]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from lineage_api.domain.product_confidence import project_confidence
from lineage_api.services.analyzer_registry import AnalyzerRegistry, AnalyzerSelection
from lineage_api.services.composition import compose_repository_documents
from lineage_api.services.impact_simulation import simulate_impact
from lineage_api.services.resolver import (
    RawName,
    ResolveContext,
    ResolvedName,
    Resolver,
)
from lineage_api.services.spark_sca import analyze_spark_sources

CATALOG = ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
FIXTURES = ROOT / "fixtures" / "repositories"
DEFAULT_ESTATE = Path("/private/tmp/lineage-estate")


def _rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


class _Snapshot:
    scope_digest = "sha256:" + "2" * 64
    revision = "1" * 40

    def __init__(self, repo, root, paths, *, pack, ruleset, platform, system, origin=None):
        self.repository = repo
        self._root = root
        self.paths = paths
        self.analyzer_pack = pack
        self.ruleset = ruleset
        self.platform = platform
        self.system = system
        self.environment = "staging"
        if origin:
            self.origin = origin

    def read_bytes(self, relative_path: str) -> bytes:
        return (self._root / relative_path).read_bytes()


def _all_paths(root: Path) -> tuple[str, ...]:
    return tuple(
        sorted(
            item.relative_to(root).as_posix()
            for item in root.rglob("*")
            if item.is_file()
            and "/target/" not in f"/{item.relative_to(root)}"
            and ".git/" not in item.relative_to(root).as_posix()
        )
    )


def _resolve(resolver: Resolver, value: str, system: str) -> str | None:
    """Resolve a Spark endpoint: an object-store path or a Kafka topic."""
    scheme = value.split("://", 1)[0] if "://" in value else ""
    platform = "s3" if scheme else "kafka"
    result = resolver.resolve(
        RawName("dataset", value, "SCA", ()),
        ResolveContext(
            env="staging",
            platform=platform,
            system=system,
            repo="spark-orders-etl",
            digest="1" * 40,
            config={},
            snapshot_id=resolver.snapshot_id,
        ),
    )
    return str(result.urn) if isinstance(result, ResolvedName) else None


def _spark_document(resolver: Resolver) -> dict:
    """Run the Spark cell and resolve its endpoints into an analyzer document."""
    repo = FIXTURES / "spark-orders-etl"
    analysis = analyze_spark_sources(
        {p.relative_to(repo).as_posix(): p.read_text() for p in sorted(repo.rglob("*.java"))}
    )
    edges: list[dict] = []
    for index, job in enumerate(analysis.jobs):
        # A Kafka topic is owned by `inventory`; an object prefix by `lakehouse`.
        source = _resolve(resolver, job.source, "inventory" if "://" not in job.source else "lakehouse")
        target = _resolve(resolver, job.target, "lakehouse")
        if source is None or target is None:
            continue
        if job.mappings:
            for mapping in job.mappings:
                for column in mapping.source_columns:
                    edges.append(
                        {
                            "provenanceId": f"prov-spark-{index}-{mapping.target_column}-{column}",
                            "from": [f"{source}#{column}"],
                            "to": f"{target}#{mapping.target_column}",
                            "edgeType": "DERIVES",
                            "transform": mapping.transform,
                            "mechanism": "SCA",
                            "exact": True,
                            "file": job.path,
                            "line": job.line,
                        }
                    )
        else:
            edges.append(
                {
                    "provenanceId": f"prov-spark-{index}",
                    "from": [source],
                    "to": target,
                    "edgeType": "DERIVES",
                    "transform": f"{job.method} -> {job.target.rsplit('/', 1)[-1]}",
                    "mechanism": "SCA",
                    "exact": True,
                    "file": job.path,
                    "line": job.line,
                }
            )
    return {"repo": "spark-orders-etl", "edges": edges}, analysis


def main() -> int:
    estate = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ESTATE
    resolver = Resolver.from_path(CATALOG)
    registry = AnalyzerRegistry.default(kafka_resolver=resolver)
    documents: list[dict] = []

    _rule("1. PER-REPOSITORY ANALYSIS")

    processor = estate / "spring-cloud-stream-samples/kafka-streams-samples/kafka-streams-inventory-count"
    if processor.is_dir():
        result = registry.analyze(
            _Snapshot(
                "kafka-streams-inventory-count", processor, _all_paths(processor),
                pack="kafka-streams-v1", ruleset="kafka-binding-rules-v1",
                platform="kafka", system="inventory",
            ),
            AnalyzerSelection(
                "kafka-streams-v1", "kafka-binding-rules-v1", "git-checkout",
                "spring-cloud-stream", "kafka",
            ),
            "run-kafka", "corr",
        )
        documents.append(result.document)
        print(f"  {'kafka-streams-inventory-count':32s} kafka-streams-v1      "
              f"{result.status:20s} edges={result.edge_count}   [REAL REPO]")
    else:
        print(f"  kafka processor not found under {estate} — skipping that hop")

    spark_document, spark_analysis = _spark_document(resolver)
    documents.append(spark_document)
    print(f"  {'spark-orders-etl':32s} spark-batch-v1        "
          f"{'COMPLETE':20s} edges={len(spark_document['edges'])}")
    print(f"  {'':32s} jobs={len(spark_analysis.jobs)} "
          f"residue={[r.code for r in spark_analysis.residue]}")

    _rule("2. THE COMPOSED ESTATE GRAPH")
    graph = compose_repository_documents(tuple(documents))
    for contribution in graph.contributions:
        print(f"  {contribution.repo:34s} edges={contribution.edge_count:2d} "
              f"datasets={len(contribution.datasets)}")
    print(f"\n  cross-repository seams: {[d.split(':')[-1] for d in graph.shared_datasets]}")
    print(f"\n  {len(graph.edges)} edges:")
    for edge in graph.edges:
        source = str(edge["from"][0]).split(":")[-1]
        target = str(edge["to"]).split(":")[-1]
        print(f"    {source:38s} -> {target:34s} [{edge.get('transforms', [''])[0]}]")

    _rule("3. END-TO-END TRACE")
    seed = "urn:ldp:staging:kafka:inventory:inventory-update-events"
    report = simulate_impact(graph, seed)
    print(f"  seed: {seed.split(':')[-1]}\n")
    for item in report.impacted:
        via = " -> ".join(item.via) if item.via else "-"
        print(f"    {item.severity:7s} hop={item.hops}  {item.urn.split(':')[-1]:38s}")
        if item.via:
            print(f"            via [{via}]")
    print(f"\n  datasets touched={len(report.datasets)}  maxHops={report.max_hops}")

    _rule("4. RUNTIME CORROBORATION OF THE SPARK HOP")
    import json

    from lineage_api.domain.confidence import derive_band
    from lineage_api.services.consolidation import ConsolidationService

    event = json.loads(
        (ROOT / "fixtures" / "runtime" / "openlineage" / "spark-orders-curation.json").read_text()
    )
    service = ConsolidationService(database=None, resolver=resolver)  # type: ignore[arg-type]
    sca_pairs = {
        (str(edge["from"][0]), str(edge["to"]))
        for edge in spark_document["edges"]
        if "#" in str(edge["to"])
    }
    corroborated: set[tuple[str, str]] = set()
    for output in event["outputs"]:
        target = service._resolve_runtime_dataset(
            f"{output['namespace']}/{output['name']}", "staging"
        )
        for target_field, mapping in output["facets"]["columnLineage"]["fields"].items():
            for item in mapping["inputFields"]:
                source = service._resolve_runtime_dataset(
                    f"{item['namespace']}/{item['name']}", "staging"
                )
                pair = (f"{source}#{item['field']}", f"{target}#{target_field}")
                if pair in sca_pairs:
                    corroborated.add(pair)

    print(f"  OpenLineage names the dataset by bucket   : s3://raw-lake/curated/orders")
    print(f"  the analyzer names it by owning system    : "
          f"{sorted(sca_pairs)[0][1].rsplit('#', 1)[0]}")
    print(f"\n  element edges claimed by SCA   : {len(sca_pairs)}")
    print(f"  element edges corroborated     : {len(corroborated)}")
    for source, target in sorted(corroborated):
        print(f"    VERIFIED  {source.split(':')[-1]:34s} -> {target.split(':')[-1]}")

    static_only = project_confidence("SINGLE", [{"mechanism": "SCA"}])
    band = derive_band({"SCA", "RUNTIME"})
    verified = project_confidence(
        band,
        [{"mechanism": "SCA"},
         {"mechanism": "RUNTIME", "observedAt": event["eventTime"]}],
    )
    print(f"\n  corroborated edges -> band={band} {verified.display_band} "
          f"({verified.percent}%) signals={list(verified.signals)} "
          f"lastObserved={verified.last_observed}")
    print(f"  everything else    -> {static_only.display_band} "
          f"({static_only.percent}%) signals={list(static_only.signals)}")
    print("\n  Kafka and landing hops are dataset-level by nature, so runtime can")
    print("  corroborate them but cannot raise their band: application/consolidation.py")
    print("  only counts a RUNTIME assertion at ELEMENT scope. Only the Spark column")
    print("  hop is eligible for VERIFIED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
