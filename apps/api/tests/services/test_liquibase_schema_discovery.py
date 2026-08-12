"""A repository whose schema exists only as Liquibase changelogs must still resolve."""

from pathlib import Path

from lineage_api.services.analyzer_registry import AnalyzerRegistry, AnalyzerSelection

ROOT = Path(__file__).resolve().parents[4]
REPO = ROOT / "fixtures" / "repositories" / "java-liquibase-service"

SELECTION = AnalyzerSelection(
    "java-spring-data-jpa-v1",
    "spring-data-rules-v1",
    "git-checkout",
    "spring-data-jpa",
    "postgres",
)


class _Snapshot:
    environment = "staging"
    platform = "postgres"
    system = "billing"
    analyzer_pack = "java-spring-data-jpa-v1"
    ruleset = "spring-data-rules-v1"
    revision = "1" * 40
    scope_digest = "sha256:" + "2" * 64
    origin = "https://github.com/example/billing"
    repository = "java-liquibase-service"

    def __init__(self) -> None:
        self.paths = tuple(
            sorted(
                item.relative_to(REPO).as_posix()
                for item in REPO.rglob("*")
                if item.is_file()
            )
        )

    def read_bytes(self, relative_path: str) -> bytes:
        return (REPO / relative_path).read_bytes()


def _analyze():
    return AnalyzerRegistry.default().analyze(_Snapshot(), SELECTION, "run", "corr")


def test_a_liquibase_only_repository_completes() -> None:
    result = _analyze()

    assert result.status == "COMPLETE"
    assert result.status_reasons == ()


def test_edges_resolve_against_the_changelog_table() -> None:
    result = _analyze()

    assert result.edge_count == 2
    assert result.document["datasetsSeen"] == [
        "urn:ldp:staging:postgres:billing:invoices"
    ]


def test_a_column_added_by_a_later_changeset_is_in_the_schema() -> None:
    """`currency` exists only because 002 added it, and the entity maps to it."""
    result = _analyze()

    transforms = {edge["transform"] for edge in result.document["edges"]}
    assert "InvoiceRepository.findByCurrency -> invoices" in transforms
