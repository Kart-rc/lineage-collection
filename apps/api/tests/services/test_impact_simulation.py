from pathlib import Path

from lineage_api.services.analyzer_registry import AnalyzerRegistry, AnalyzerSelection
from lineage_api.services.composition import compose_repository_documents
from lineage_api.services.impact_simulation import ImpactReport, simulate_impact
from lineage_api.services.resolver import Resolver

A = "urn:ldp:staging:snowflake:payments:raw.transactions"
B = "urn:ldp:staging:snowflake:payments:analytics.daily_revenue"
C = "urn:ldp:staging:snowflake:payments:risk.customer_features"


def _graph(edges: list[tuple[str, str, str]]):
    return compose_repository_documents(
        (
            {
                "repo": "r",
                "edges": [
                    {
                        "from": [source],
                        "to": target,
                        "edgeType": "DERIVES",
                        "transform": transform,
                        "provenanceId": f"p{index}",
                    }
                    for index, (source, target, transform) in enumerate(edges)
                ],
            },
        )
    )


def test_a_directly_derived_element_is_a_break() -> None:
    graph = _graph([(f"{A}#amount", f"{B}#gross_revenue", "SUM(amount)")])

    report = simulate_impact(graph, f"{A}#amount")

    assert isinstance(report, ImpactReport)
    assert report.seed == f"{A}#amount"
    assert [(item.urn, item.severity, item.hops) for item in report.impacted] == [
        (f"{A}#amount", "SOURCE", 0),
        (f"{B}#gross_revenue", "BREAK", 1),
    ]


def test_a_transitively_reached_element_is_a_warn() -> None:
    graph = _graph(
        [
            (f"{A}#amount", f"{B}#gross_revenue", "SUM(amount)"),
            (f"{B}#gross_revenue", f"{C}#lifetime_value", "SUM(gross_revenue)"),
        ]
    )

    report = simulate_impact(graph, f"{A}#amount")

    severities = {item.urn: item.severity for item in report.impacted}
    assert severities[f"{B}#gross_revenue"] == "BREAK"
    assert severities[f"{C}#lifetime_value"] == "WARN"
    assert report.max_hops == 2


def test_the_path_transforms_explain_the_blast_radius() -> None:
    graph = _graph(
        [
            (f"{A}#amount", f"{B}#gross_revenue", "SUM(amount)"),
            (f"{B}#gross_revenue", f"{C}#lifetime_value", "SUM(gross_revenue)"),
        ]
    )

    report = simulate_impact(graph, f"{A}#amount")
    reached = next(i for i in report.impacted if i.urn == f"{C}#lifetime_value")

    assert reached.via == ("SUM(amount)", "SUM(gross_revenue)")


def test_an_element_with_no_downstream_impacts_only_itself() -> None:
    graph = _graph([(f"{A}#amount", f"{B}#gross_revenue", "SUM(amount)")])

    report = simulate_impact(graph, f"{A}#occurred_at")

    assert [item.urn for item in report.impacted] == [f"{A}#occurred_at"]
    assert report.datasets == (A,)


def test_a_cycle_terminates() -> None:
    graph = _graph([(f"{A}#x", f"{B}#y", "x"), (f"{B}#y", f"{A}#x", "y")])

    report = simulate_impact(graph, f"{A}#x")

    assert {item.urn for item in report.impacted} == {f"{A}#x", f"{B}#y"}


def test_impacted_datasets_are_reported_and_sorted() -> None:
    graph = _graph(
        [
            (f"{A}#amount", f"{B}#gross_revenue", "SUM(amount)"),
            (f"{B}#gross_revenue", f"{C}#lifetime_value", "SUM(gross_revenue)"),
        ]
    )

    report = simulate_impact(graph, f"{A}#amount")

    assert report.datasets == tuple(sorted({A, B, C}))


# --- Task 2: across real repositories -------------------------------------------------

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
SELECTION = AnalyzerSelection(
    "sql-transformation-v1",
    "sql-transformation-rules-v1",
    "git-checkout",
    "sql-transformation",
    "snowflake",
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


def _analyze(repo: str, path: str, run: str):
    registry = AnalyzerRegistry.default(sql_resolver=Resolver.from_path(CATALOG))
    return registry.analyze(_RepoSnapshot(repo, (path,)), SELECTION, run, f"corr-{run}")


def test_changing_a_source_column_breaks_across_two_repositories() -> None:
    warehouse = _analyze("warehouse-sql", "sql/daily_revenue.sql", "run-1")
    risk = _analyze("risk-model-sql", "sql/customer_features.sql", "run-2")
    graph = compose_repository_documents((warehouse.document, risk.document))

    report = simulate_impact(graph, f"{A}#amount")

    by_urn = {item.urn: item for item in report.impacted}
    assert by_urn[f"{B}#gross_revenue"].severity == "BREAK"
    assert by_urn[f"{C}#lifetime_value"].severity == "WARN"
    assert by_urn[f"{C}#lifetime_value"].via == ("SUM(amount)", "SUM(gross_revenue)")
    assert report.max_hops == 2


def test_a_single_repository_cannot_see_the_second_hop() -> None:
    """The cross-repository join is what makes the two-hop radius visible."""
    warehouse = _analyze("warehouse-sql", "sql/daily_revenue.sql", "run-1")
    graph = compose_repository_documents((warehouse.document,))

    report = simulate_impact(graph, f"{A}#amount")

    assert f"{C}#lifetime_value" not in {item.urn for item in report.impacted}
    assert report.max_hops == 1
