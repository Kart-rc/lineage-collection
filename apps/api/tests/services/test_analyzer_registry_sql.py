from pathlib import Path

from lineage_api.services.analyzer_registry import (
    AnalyzerRegistry,
    AnalyzerSelection,
)
from lineage_api.services.resolver import Resolver

SQL_CATALOG = (
    Path(__file__).resolve().parents[4] / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
)

SQL_SELECTION = AnalyzerSelection(
    analyzer_pack="sql-transformation-v1",
    ruleset="sql-transformation-rules-v1",
    source_kind="git-checkout",
    framework="sql-transformation",
    schema_profile="snowflake",
)


class _Snapshot:
    repository = "warehouse-sql"
    revision = "d" * 40
    scope_digest = "sha256:" + "2" * 64
    environment = "staging"
    platform = "snowflake"
    system = "payments"
    analyzer_pack = "sql-transformation-v1"
    ruleset = "sql-transformation-rules-v1"
    paths = (
        "sql/daily_revenue.sql",
        "README.md",
        "expected-lineage.json",
        "scripts/seed.sql",
    )

    def read_bytes(self, relative_path: str) -> bytes:
        root = (
            Path(__file__).resolve().parents[4] / "fixtures" / "repositories" / "warehouse-sql"
        )
        return (root / relative_path).read_bytes()


def test_the_sql_transformation_pack_resolves_with_an_injected_resolver() -> None:
    registry = AnalyzerRegistry.default(sql_resolver=Resolver.from_path(SQL_CATALOG))

    definition = registry.resolve(SQL_SELECTION)

    assert definition.analyzer_pack == "sql-transformation-v1"
    assert definition.analyze is not None


def test_the_sql_pack_selects_only_sql_under_the_sql_directory() -> None:
    scope = AnalyzerRegistry.default().source_scope(_Snapshot(), SQL_SELECTION)

    assert scope.selected_scope == ("sql/daily_revenue.sql",)
    assert scope.skipped_scope == ("README.md", "expected-lineage.json")
    assert scope.unsupported_scope == ("scripts/seed.sql",)


def test_the_sql_pack_produces_derivation_edges_end_to_end() -> None:
    registry = AnalyzerRegistry.default(sql_resolver=Resolver.from_path(SQL_CATALOG))

    class _SelectedSnapshot(_Snapshot):
        paths = ("sql/daily_revenue.sql",)

    result = registry.analyze(_SelectedSnapshot(), SQL_SELECTION, "run-1", "corr-1")

    assert result.status == "COMPLETE"
    assert result.edge_count == 3
    assert {edge["edgeType"] for edge in result.document["edges"]} == {"DERIVES"}
    assert result.document["datasetsSeen"] == [
        "urn:ldp:staging:snowflake:payments:analytics.daily_revenue",
        "urn:ldp:staging:snowflake:payments:raw.transactions",
    ]


def test_the_existing_packs_are_unaffected() -> None:
    registry = AnalyzerRegistry.default()

    java = registry.resolve(
        AnalyzerSelection(
            "java-spring-data-jpa-v1",
            "spring-data-rules-v1",
            "git-checkout",
            "spring-data-jpa",
            "postgres",
        )
    )

    assert java.analyze is not None
