from pathlib import Path

from lineage_api.services.analyzer_registry import AnalyzerRegistry, AnalyzerSelection
from lineage_api.services.composition import (
    ComposedGraph,
    compose_repository_documents,
)
from lineage_api.services.resolver import Resolver

A = "urn:ldp:staging:snowflake:payments:raw.transactions"
B = "urn:ldp:staging:snowflake:payments:analytics.daily_revenue"
C = "urn:ldp:staging:snowflake:payments:risk.customer_features"

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json"

SELECTION = AnalyzerSelection(
    "sql-transformation-v1",
    "sql-transformation-rules-v1",
    "git-checkout",
    "sql-transformation",
    "snowflake",
)


def _document(repo: str, edges: list[tuple[str, str]]) -> dict:
    return {
        "repo": repo,
        "edges": [
            {
                "from": [source],
                "to": target,
                "edgeType": "DERIVES",
                "transform": "copy",
                "provenanceId": f"prov-{repo}-{index}",
            }
            for index, (source, target) in enumerate(edges)
        ],
    }


def test_two_repositories_sharing_a_dataset_compose_into_one_graph() -> None:
    warehouse = _document("warehouse-sql", [(f"{A}#amount", f"{B}#gross_revenue")])
    risk = _document("risk-model", [(f"{B}#gross_revenue", f"{C}#lifetime_value")])

    graph = compose_repository_documents((warehouse, risk))

    assert isinstance(graph, ComposedGraph)
    assert len(graph.edges) == 2
    assert graph.shared_datasets == (B,)
    assert [c.repo for c in graph.contributions] == ["risk-model", "warehouse-sql"]


def test_repositories_with_no_common_dataset_report_no_seam() -> None:
    one = _document("a", [(f"{A}#amount", f"{B}#gross_revenue")])
    two = _document("b", [(f"{C}#customer_id", f"{C}#lifetime_value")])

    graph = compose_repository_documents((one, two))

    assert graph.shared_datasets == ()


def test_a_chain_across_repositories_is_traversable() -> None:
    graph = compose_repository_documents(
        (
            _document("r1", [(f"{A}#amount", f"{B}#gross_revenue")]),
            _document("r2", [(f"{B}#gross_revenue", f"{C}#lifetime_value")]),
        )
    )

    downstream = {edge["to"] for edge in graph.edges}
    upstream = {edge["from"][0] for edge in graph.edges}
    assert f"{B}#gross_revenue" in downstream
    assert f"{B}#gross_revenue" in upstream


def test_composition_is_order_independent() -> None:
    one = _document("a", [(f"{A}#amount", f"{B}#gross_revenue")])
    two = _document("b", [(f"{B}#gross_revenue", f"{C}#lifetime_value")])

    assert compose_repository_documents((one, two)) == compose_repository_documents(
        (two, one)
    )


class _RepoSnapshot:
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
        return (
            ROOT / "fixtures" / "repositories" / self.repository / relative_path
        ).read_bytes()


def test_two_real_sql_repositories_compose_through_a_shared_dataset() -> None:
    registry = AnalyzerRegistry.default(sql_resolver=Resolver.from_path(CATALOG))

    warehouse = registry.analyze(
        _RepoSnapshot("warehouse-sql", ("sql/daily_revenue.sql",)),
        SELECTION,
        "run-1",
        "corr-1",
    )
    risk = registry.analyze(
        _RepoSnapshot("risk-model-sql", ("sql/customer_features.sql",)),
        SELECTION,
        "run-2",
        "corr-2",
    )

    assert warehouse.status == "COMPLETE"
    assert risk.status == "COMPLETE"

    graph = compose_repository_documents((warehouse.document, risk.document))

    assert graph.shared_datasets == (B,)
    assert len(graph.edges) == warehouse.edge_count + risk.edge_count
