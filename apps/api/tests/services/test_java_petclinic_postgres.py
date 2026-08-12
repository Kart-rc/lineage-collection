"""The Petclinic-shaped Java repository must actually produce lineage.

`java-spring-corpus` deliberately exercises the H2-compatibility path and therefore
yields no edges — an H2-profile schema is not trusted as the production schema, and an
unqualified `@Table` is never guessed into `public`. Those are safety rules worth
keeping, but they meant no Java repository contributed to the graph at all.

This fixture is the same shape with a schema that genuinely resolves: a postgres-profile
schema whose table identities match the mappings. It is the Java repository the estate
graph is built from.
"""

from pathlib import Path

from lineage_api.services.analyzer_registry import AnalyzerRegistry, AnalyzerSelection
from lineage_api.services.composition import compose_repository_documents
from lineage_api.services.java_interaction_sca import analyze_java_interactions
from lineage_api.services.resolver import Resolver

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
JAVA_REPO = ROOT / "fixtures" / "repositories" / "java-petclinic-postgres"

OWNERS = "urn:ldp:staging:postgres:petclinic:owners"
VISITS = "urn:ldp:staging:postgres:petclinic:visits"
LTV = "urn:ldp:staging:postgres:petclinic:analytics.owner_ltv"


class _JavaSnapshot:
    environment = "staging"
    platform = "postgres"
    system = "petclinic"
    analyzer_pack = "java-spring-data-jpa-v1"
    ruleset = "spring-data-rules-v1"
    revision = "1" * 40
    scope_digest = "sha256:" + "2" * 64
    origin = "https://github.com/example/petclinic"
    repository = "java-petclinic-postgres"

    def __init__(self) -> None:
        self.paths = tuple(
            sorted(
                item.relative_to(JAVA_REPO).as_posix()
                for item in JAVA_REPO.rglob("*")
                if item.is_file()
            )
        )

    def read_bytes(self, relative_path: str) -> bytes:
        return (JAVA_REPO / relative_path).read_bytes()


class _SqlSnapshot:
    environment = "staging"
    platform = "postgres"
    system = "petclinic"
    analyzer_pack = "sql-transformation-v1"
    ruleset = "sql-transformation-rules-v1"
    revision = "d" * 40
    scope_digest = "sha256:" + "2" * 64
    repository = "petclinic-analytics-sql"
    paths = ("sql/owner_ltv.sql",)

    def read_bytes(self, relative_path: str) -> bytes:
        return (
            ROOT / "fixtures" / "repositories" / self.repository / relative_path
        ).read_bytes()


def _java():
    return AnalyzerRegistry.default().analyze(
        _JavaSnapshot(),
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


def _sql():
    registry = AnalyzerRegistry.default(sql_resolver=Resolver.from_path(CATALOG))
    return registry.analyze(
        _SqlSnapshot(),
        AnalyzerSelection(
            "sql-transformation-v1",
            "sql-transformation-rules-v1",
            "git-checkout",
            "sql-transformation",
            "postgres",
        ),
        "run-sql",
        "corr-sql",
    )


def test_the_petclinic_java_repository_completes_with_no_residue() -> None:
    result = _java()

    assert result.status == "COMPLETE"
    assert result.status_reasons == ()
    assert result.document["residue"] == []


def test_it_produces_reads_and_writes_over_both_tables() -> None:
    result = _java()

    assert result.edge_count == 4
    assert result.read_count == 2
    assert result.write_count == 2
    assert sorted(result.document["datasetsSeen"]) == [OWNERS, VISITS]


def test_derived_query_methods_resolve_to_their_table() -> None:
    result = _java()

    transforms = {edge["transform"] for edge in result.document["edges"]}
    assert "OwnerRepository.findByLastName -> owners" in transforms


def test_the_java_service_and_the_sql_pipeline_join_on_owners() -> None:
    graph = compose_repository_documents((_java().document, _sql().document))

    assert OWNERS in graph.shared_datasets
    assert {c.repo for c in graph.contributions} == {
        "java-petclinic-postgres",
        "petclinic-analytics-sql",
    }


def test_the_same_repository_exposes_rest_endpoints() -> None:
    sources = {
        item.relative_to(JAVA_REPO).as_posix(): item.read_text()
        for item in sorted(JAVA_REPO.rglob("*.java"))
    }

    interactions = analyze_java_interactions(sources, service="petclinic-service")

    operations = [item.operation for item in interactions.inbound]
    assert operations == [
        "GET /owners/{id}",
        "GET /owners/search",
        "POST /owners",
        "POST /owners/visits",
    ]
    assert interactions.residue == ()
