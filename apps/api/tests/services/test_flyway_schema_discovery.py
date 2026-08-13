"""A repository whose schema exists only as Flyway migrations must still resolve."""

from pathlib import Path

from lineage_api.services.analyzer_registry import AnalyzerRegistry, AnalyzerSelection

ROOT = Path(__file__).resolve().parents[4]
REPO = ROOT / "fixtures" / "repositories" / "java-flyway-service"

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
    system = "shop"
    analyzer_pack = "java-spring-data-jpa-v1"
    ruleset = "spring-data-rules-v1"
    revision = "1" * 40
    scope_digest = "sha256:" + "2" * 64
    origin = "https://github.com/example/shop"
    repository = "java-flyway-service"

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


def test_a_flyway_only_repository_completes() -> None:
    result = _analyze()

    assert result.status == "COMPLETE"
    assert result.status_reasons == ()


def test_edges_resolve_against_the_migrated_table() -> None:
    result = _analyze()

    assert result.edge_count == 3
    assert sorted(result.document["datasetsSeen"]) == [
        "urn:ldp:staging:postgres:shop:customers",
        "urn:ldp:staging:postgres:shop:customers#city",
    ]


def test_a_table_dropped_by_a_later_migration_is_not_in_the_schema() -> None:
    result = _analyze()

    assert all("legacy_audit" not in urn for urn in result.document["datasetsSeen"])


def test_vendor_specific_migrations_only_apply_to_their_own_profile() -> None:
    """Flyway supports db/migration/<vendor>/; replaying another vendor's would be wrong."""
    from lineage_api.services.analyzer_registry import _is_migration_for_profile

    assert _is_migration_for_profile("db/migration/V1__init.sql", "postgres")
    assert _is_migration_for_profile("db/migration/postgres/V1__init.sql", "postgres")
    assert not _is_migration_for_profile("db/migration/mysql/V1__init.sql", "postgres")
    assert not _is_migration_for_profile("db/migration/h2/V1__init.sql", "postgres")
    # An unrecognised subdirectory is not a vendor directory, so it still applies.
    assert _is_migration_for_profile("db/migration/common/V1__init.sql", "postgres")
