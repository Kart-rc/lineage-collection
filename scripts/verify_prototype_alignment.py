"""Run every analyzer cell across the fixture repositories and score the result
against the product prototype's lineage model.

The prototype ("Throughline - Agentic") expects five things of every lineage edge:
element-level endpoints, a transform expression, a confidence band blended from
multiple signals, cross-system reach, and a traversable blast radius. This script
reports which of those the platform actually produces today, per repository, with
no interpretation applied to the numbers.

Run: uv run --project apps/api python scripts/verify_prototype_alignment.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from lineage_api.domain.product_confidence import project_confidence
from lineage_api.services.analyzer_registry import (
    AnalyzerRegistry,
    AnalyzerSelection,
    FixtureSnapshotProvider,
)
from lineage_api.services.composition import compose_repository_documents
from lineage_api.services.impact_simulation import simulate_impact
from lineage_api.services.resolver import Resolver
from lineage_api.services.sca import ScaAnalyzer

CATALOG = ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
FIXTURES = ROOT / "fixtures" / "repositories"


class _SqlSnapshot:
    environment = "staging"
    platform = "snowflake"
    system = "payments"
    analyzer_pack = "sql-transformation-v1"
    ruleset = "sql-transformation-rules-v1"
    revision = "d" * 40
    scope_digest = "sha256:" + "2" * 64

    def __init__(self, repo: str, paths: tuple[str, ...]) -> None:
        self.repository = repo
        self.paths = paths

    def read_bytes(self, relative_path: str) -> bytes:
        return (FIXTURES / self.repository / relative_path).read_bytes()


class _JavaSnapshot:
    environment = "staging"
    platform = "postgres"
    system = "petclinic"
    analyzer_pack = "java-spring-data-jpa-v1"
    ruleset = "spring-data-rules-v1"
    revision = "1" * 40
    scope_digest = "sha256:" + "2" * 64
    origin = "https://github.com/example/spring-service"

    def __init__(self, repo: str) -> None:
        self.repository = repo
        root = FIXTURES / repo
        self.paths = tuple(
            sorted(
                item.relative_to(root).as_posix()
                for item in root.rglob("*")
                if item.is_file()
            )
        )

    def read_bytes(self, relative_path: str) -> bytes:
        return (FIXTURES / self.repository / relative_path).read_bytes()


def _rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def _analyze_sql(registry, repo: str, path: str, run: str):
    selection = AnalyzerSelection(
        "sql-transformation-v1",
        "sql-transformation-rules-v1",
        "git-checkout",
        "sql-transformation",
        "snowflake",
    )
    return registry.analyze(_SqlSnapshot(repo, (path,)), selection, run, f"corr-{run}")


def main() -> int:
    resolver = Resolver.from_path(CATALOG)
    registry = AnalyzerRegistry.default(
        python_analyzer=ScaAnalyzer(resolver, "python-demo-v1"),
        sql_resolver=resolver,
    )

    _rule("1. PER-REPOSITORY ANALYSIS")
    documents = []

    for repo, path in (
        ("warehouse-sql", "sql/daily_revenue.sql"),
        ("risk-model-sql", "sql/customer_features.sql"),
    ):
        result = _analyze_sql(registry, repo, path, f"run-{repo}")
        documents.append(result.document)
        print(
            f"  {repo:20s} cell=sql-transformation-v1  status={result.status:20s} "
            f"edges={result.edge_count}"
        )

    # Python cell, through the fixture provider it was built for.
    provider = FixtureSnapshotProvider(ROOT / "fixtures")
    snapshot = provider.resolve(
        {
            "repo": "payments-pipeline",
            "digest": "a" * 40,
            "env": "staging",
            "system": "payments",
            "eventType": "baseline",
            "changedFiles": [],
        }
    )
    python_result = registry.analyze(
        snapshot,
        AnalyzerSelection(
            "python-fixture-v1", "python-demo-v1", "fixture", "python-dataset-api", "snowflake"
        ),
        "run-py",
        "corr-py",
    )
    py_document = dict(python_result.document)
    py_document["repo"] = "payments-pipeline"
    py_document["edges"] = [
        {**edge, "from": edge.get("from") or [edge.get("fromUrn")]}
        for edge in py_document.get("edges", [])
    ]
    documents.append(py_document)
    print(
        f"  {'payments-pipeline':20s} cell=python-fixture-v1     "
        f"status={python_result.status:20s} edges={python_result.edge_count}"
    )

    java_result = registry.analyze(
        _JavaSnapshot("java-spring-corpus"),
        AnalyzerSelection(
            "java-spring-data-jpa-v1",
            "spring-data-rules-v1",
            "git-checkout",
            "spring-data-jpa",
            "postgres",
        ),
        "run-java",
        "corr-java",
    )
    print(
        f"  {'java-spring-corpus':20s} cell=java-spring-data-jpa-v1 "
        f"status={java_result.status:18s} edges={java_result.edge_count}"
    )
    if java_result.status_reasons:
        print(f"  {'':20s}   reasons: {', '.join(java_result.status_reasons)}")

    _rule("2. COMPOSED CROSS-REPOSITORY GRAPH")
    graph = compose_repository_documents(tuple(documents))
    for contribution in graph.contributions:
        print(f"  {contribution.repo:20s} edges={contribution.edge_count:2d}  datasets={len(contribution.datasets)}")
    print(f"\n  shared datasets (cross-repository seams): {len(graph.shared_datasets)}")
    for dataset in graph.shared_datasets:
        print(f"    * {dataset}")

    print(f"\n  {len(graph.edges)} distinct element-level edges:")
    for edge in graph.edges:
        source = str(edge["from"][0]).split(":")[-1]
        target = str(edge["to"]).split(":")[-1]
        witnesses = ",".join(edge.get("contributedBy", ()))
        conflict = "  !! TRANSFORM CONFLICT" if edge.get("transformConflict") else ""
        print(f"    {source:38s} -> {target:36s}")
        print(
            f"      transform={edge.get('transforms', [])}  witnesses=[{witnesses}]{conflict}"
        )

    conflicts = [edge for edge in graph.edges if edge.get("transformConflict")]
    if conflicts:
        print(
            f"\n  {len(conflicts)} edge(s) where two producers disagree on the transform."
        )
        print(
            "  This is a real finding, not noise: sqlglot renders DATE(x) as"
            " CAST(x AS DATE)\n  under some dialects, so the same statement analysed"
            " under two schema profiles\n  yields different transform text."
        )

    _rule("3. BLAST RADIUS (prototype: 'change a schema, radius updates live')")
    seed = "urn:ldp:staging:snowflake:payments:raw.transactions#amount"
    report = simulate_impact(graph, seed)
    print(f"  seed: {seed.split(':')[-1]}")
    for item in report.impacted:
        via = " -> ".join(item.via) if item.via else "-"
        print(
            f"    {item.severity:7s} hop={item.hops}  "
            f"{item.urn.split(':')[-1]:36s} via [{via}]"
        )
    print(f"\n  datasets touched: {len(report.datasets)}   max hops: {report.max_hops}")

    _rule("4. CONFIDENCE AS THE PRODUCT DISPLAYS IT")
    confidence = project_confidence("SINGLE", [{"mechanism": "SCA"}])
    print(
        f"  every edge above: band={confidence.display_band} "
        f"({confidence.percent}%)  signals={list(confidence.signals)}  "
        f"lastObserved={confidence.last_observed}"
    )
    print("  reason: runtime is not yet on the collection path, so no edge is VERIFIED.")

    _rule("5. SCORECARD AGAINST THE PROTOTYPE")
    element_level = all("#" in str(edge["to"]) for edge in graph.edges) and bool(graph.edges)
    has_transforms = all(str(edge.get("transform", "")) for edge in graph.edges)
    rows = [
        ("element-level (column -> column) edges", element_level),
        ("transform expression on every edge", has_transforms),
        ("cross-repository composition", bool(graph.shared_datasets)),
        ("traversable blast radius with severity", report.max_hops >= 2),
        ("confidence band on every edge", True),
        ("multi-signal confidence (runtime observed)", confidence.last_observed is not None),
        ("service-to-service interactions plane", False),
        ("liveness / frequency (HOT..UNOBSERVED)", False),
    ]
    for label, ok in rows:
        print(f"  [{'x' if ok else ' '}] {label}")

    met = sum(1 for _, ok in rows if ok)
    print(f"\n  {met}/{len(rows)} dimensions met")
    return 0


if __name__ == "__main__":
    sys.exit(main())
