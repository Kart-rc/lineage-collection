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

    assert result.edge_count == 3
    assert sorted(result.document["datasetsSeen"]) == [
        "urn:ldp:staging:postgres:billing:invoices",
        "urn:ldp:staging:postgres:billing:invoices#currency",
    ]


def test_a_column_added_by_a_later_changeset_is_in_the_schema() -> None:
    """`currency` exists only because 002 added it, and the entity maps to it."""
    result = _analyze()

    transforms = {edge["transform"] for edge in result.document["edges"]}
    assert "InvoiceRepository.findByCurrency -> invoices" in transforms


class _SnapshotWithUnmodelledChange(_Snapshot):
    """The fixture repo, with one changelog change type the replay cannot model."""

    _TARGET = "src/main/resources/db/changelog/changes/002-add-currency.xml"
    _EXTENDED = b"""<databaseChangeLog xmlns="http://www.liquibase.org/xml/ns/dbchangelog">
  <changeSet id="2" author="billing">
    <addColumn tableName="invoices">
      <column name="currency" type="char(3)"/>
    </addColumn>
  </changeSet>
  <changeSet id="3" author="billing">
    <dropColumn tableName="invoices" columnName="draft_note"/>
  </changeSet>
  <changeSet id="4" author="billing">
    <addForeignKeyConstraint baseTableName="invoices" baseColumnNames="account_id"
      referencedTableName="accounts" referencedColumnNames="id"
      constraintName="fk_invoices_account"/>
  </changeSet>
</databaseChangeLog>
"""

    def read_bytes(self, relative_path: str) -> bytes:
        if relative_path == self._TARGET:
            return self._EXTENDED
        return super().read_bytes(relative_path)


def test_an_unmodelled_changelog_change_is_residue_not_a_crash() -> None:
    """schema_migrations residue codes must be forwardable into the cell's residue.

    Real-world changelogs (e.g. jhipster's) routinely contain change types the replay
    does not model. That must surface as honest residue forcing INTEGRATION_REQUIRED,
    never as a JavaSpringAnalysisError crash that fails the whole collection.
    """
    result = AnalyzerRegistry.default().analyze(
        _SnapshotWithUnmodelledChange(), SELECTION, "run", "corr"
    )

    assert result.status == "INTEGRATION_REQUIRED"
    assert "unmodelled-changelog-change" in result.status_reasons
