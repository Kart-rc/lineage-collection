"""The three boundaries that stopped the real Spring Petclinic microservices resolving.

Measured by `scripts/measure_real_petclinic.py` against an unmodified checkout at
`305a1f13`: every module returned INTEGRATION_REQUIRED with zero edges, blocked by
on-demand framework imports, multi-module Maven build evidence, and MySQL DDL the
ruleset rejected. Each is closed here with the same discipline as the rest of the cell —
provable or residue, never guessed.
"""

from lineage_api.services import java_spring_sca as spring_sca
from lineage_api.services.java_spring_sca import JavaSpringScaAnalyzer, JavaSpringSource


def _source(path: str, text: str, dialect: str | None = None) -> JavaSpringSource:
    return JavaSpringSource(path, text.encode(), dialect)


def _facts(result, kind: str):
    return tuple(fact for fact in result.facts if fact.kind == kind)


def _residue_codes(result) -> set[str]:
    return {item.code for item in result.residue}


_MAVEN = """<?xml version="1.0" encoding="UTF-8"?>
<project>
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.5.5</version>
  </parent>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-data-jpa</artifactId>
    </dependency>
  </dependencies>
</project>
"""


# --- A. on-demand (wildcard) framework imports ----------------------------------------


def test_a_single_approved_wildcard_package_binds_a_framework_symbol() -> None:
    """`import jakarta.persistence.*` is provable: one approved package supplies it."""
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _MAVEN),
            _source(
                "src/main/java/example/Owner.java",
                "package example;\n"
                "import jakarta.persistence.*;\n"
                "@Entity\n"
                '@Table(name = "owners")\n'
                "class Owner {\n"
                '  @Column(name = "last_name")\n'
                "  private String lastName;\n"
                "}\n",
            ),
        )
    )

    tables = _facts(result, "spring.entity-table")
    assert [fact.attribute("table") for fact in tables] == ["owners"]
    assert "wildcard-framework-symbol" not in _residue_codes(result)


def test_a_wildcard_column_override_still_resolves() -> None:
    """Element-level fields need a schema to ground them, so one is supplied here."""
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _MAVEN),
            _source(
                "src/main/resources/db/postgres/schema.sql",
                "create table owners (id integer primary key, last_name varchar(30));",
                "postgres",
            ),
            _source(
                "src/main/java/example/Owner.java",
                "package example;\n"
                "import jakarta.persistence.*;\n"
                "@Entity\n"
                '@Table(name = "owners")\n'
                "class Owner {\n"
                '  @Column(name = "last_name")\n'
                "  private String lastName;\n"
                "}\n",
            ),
        )
    )

    fields = _facts(result, "spring.entity-field")
    assert ("lastName", "last_name") in {
        (fact.attribute("field"), fact.attribute("column")) for fact in fields
    }
    assert "wildcard-framework-symbol" not in _residue_codes(result)


def test_two_approved_wildcard_packages_supplying_one_name_stay_residue() -> None:
    """`Repository` is declared by two approved packages, so a wildcard cannot prove it."""
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _MAVEN),
            _source(
                "src/main/java/example/Thing.java",
                "package example;\n"
                "import org.springframework.data.repository.*;\n"
                "import org.springframework.stereotype.*;\n"
                "@Repository\n"
                "class Thing {}\n",
            ),
        )
    )

    assert "wildcard-framework-symbol" in _residue_codes(result)


def test_a_wildcard_that_is_not_an_approved_package_proves_nothing() -> None:
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _MAVEN),
            _source(
                "src/main/java/example/Owner.java",
                "package example;\n"
                "import com.vendor.orm.*;\n"
                "@Entity\n"
                '@Table(name = "owners")\n'
                "class Owner {}\n",
            ),
        )
    )

    assert _facts(result, "spring.entity-table") == ()
    assert "wildcard-framework-symbol" in _residue_codes(result)


# --- B. multi-module Maven build evidence ---------------------------------------------


_AGGREGATOR = """<?xml version="1.0" encoding="UTF-8"?>
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>org.springframework.samples</groupId>
  <artifactId>petclinic-microservices</artifactId>
  <version>4.0.1</version>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.5.5</version>
  </parent>
  <modules>
    <module>customers-service</module>
  </modules>
</project>
"""

_MODULE = """<?xml version="1.0" encoding="UTF-8"?>
<project>
  <modelVersion>4.0.0</modelVersion>
  <artifactId>customers-service</artifactId>
  <parent>
    <groupId>org.springframework.samples</groupId>
    <artifactId>petclinic-microservices</artifactId>
    <version>4.0.1</version>
  </parent>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-data-jpa</artifactId>
    </dependency>
  </dependencies>
</project>
"""


def test_a_module_inherits_boot_evidence_from_its_declared_aggregator() -> None:
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _AGGREGATOR),
            _source("customers-service/pom.xml", _MODULE),
            _source(
                "customers-service/src/main/java/example/Owner.java",
                "package example;\n"
                "import jakarta.persistence.Entity;\n"
                "import jakarta.persistence.Table;\n"
                "@Entity\n"
                '@Table(name = "owners")\n'
                "class Owner {}\n",
            ),
        )
    )

    assert result.framework.status == "supported"
    assert result.framework.framework == "spring-data-jpa"


def test_an_aggregator_that_does_not_match_the_declared_parent_is_not_inherited() -> None:
    other = _AGGREGATOR.replace("petclinic-microservices", "something-else")
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", other),
            _source("customers-service/pom.xml", _MODULE),
            _source(
                "customers-service/src/main/java/example/A.java",
                "package example; class A {}",
            ),
        )
    )

    assert result.framework.status != "supported"


# --- C. MySQL DDL -----------------------------------------------------------------


_MYSQL_SCHEMA = """CREATE DATABASE IF NOT EXISTS petclinic;

USE petclinic;

CREATE TABLE IF NOT EXISTS owners (
  id INT(4) UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  first_name VARCHAR(30),
  last_name VARCHAR(30),
  INDEX(last_name)
) engine=InnoDB;
"""


def test_mysql_create_table_if_not_exists_yields_table_and_column_facts() -> None:
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _MAVEN),
            _source("src/main/resources/db/mysql/schema.sql", _MYSQL_SCHEMA, "mysql"),
        )
    )

    assert [fact.subject for fact in _facts(result, "sql.table")] == ["owners"]
    columns = {fact.attribute("column") for fact in _facts(result, "sql.column")}
    assert {"id", "first_name", "last_name"} <= columns
    assert "unsupported-sql" not in _residue_codes(result)


def test_mysql_session_statements_are_ignored_not_rejected() -> None:
    """CREATE DATABASE and USE carry no lineage; they must not fail the file."""
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _MAVEN),
            _source(
                "src/main/resources/db/mysql/schema.sql",
                "CREATE DATABASE IF NOT EXISTS petclinic;\nUSE petclinic;\n",
                "mysql",
            ),
        )
    )

    assert _facts(result, "sql.table") == ()
    assert "unsupported-sql" not in _residue_codes(result)


# --- D. multi-module profile schemas ---------------------------------------------------


def test_each_module_may_own_its_profile_schema() -> None:
    """A microservices repository has one schema per service, not one per repository."""
    from lineage_api.services.analyzer_registry import _is_profile_schema_path

    assert _is_profile_schema_path(
        "customers-service/src/main/resources/db/mysql/schema.sql", "mysql"
    )
    assert _is_profile_schema_path("src/main/resources/db/mysql/schema.sql", "mysql")
    assert not _is_profile_schema_path(
        "customers-service/src/main/resources/db/h2/schema.sql", "mysql"
    )
    assert not _is_profile_schema_path("db/mysql/schema.sql", "mysql")


# --- E. @Query targeting another entity ------------------------------------------------

_TWO_ENTITY_SOURCES = (
    ("pom.xml", _MAVEN, None),
    (
        "src/main/resources/db/postgres/schema.sql",
        "create table pets (id integer primary key);\n"
        "create table types (id integer primary key);",
        "postgres",
    ),
    (
        "src/main/java/example/Pet.java",
        "package example;\nimport jakarta.persistence.*;\n"
        '@Entity @Table(name = "pets") class Pet {}\n',
        None,
    ),
    (
        "src/main/java/example/PetType.java",
        "package example;\nimport jakarta.persistence.*;\n"
        '@Entity @Table(name = "types") class PetType {}\n',
        None,
    ),
)


def _pet_repository(body: str):
    return JavaSpringScaAnalyzer().analyze(
        tuple(_source(*item) for item in _TWO_ENTITY_SOURCES)
        + (
            _source(
                "src/main/java/example/PetRepository.java",
                "package example;\n"
                "import java.util.List;\n"
                "import org.springframework.data.jpa.repository.JpaRepository;\n"
                "import org.springframework.data.jpa.repository.Query;\n"
                "public interface PetRepository extends JpaRepository<Pet, Integer> {\n"
                f"{body}\n"
                "}\n",
            ),
            _source(
                "src/main/java/example/PetResource.java",
                "package example;\n"
                "import java.util.List;\n"
                "class PetResource {\n"
                "  private final PetRepository pets;\n"
                "  PetResource(PetRepository pets) { this.pets = pets; }\n"
                "  List<PetType> types() { return pets.findPetTypes(); }\n"
                "}\n",
            ),
        )
    )


def _compile(result, profile: str = "postgres"):
    context = spring_sca.JavaSpringEvidenceContext(
        origin="https://github.com/example/x",
        repository="x",
        revision="1" * 40,
        scope_digest="sha256:" + "2" * 64,
        environment="staging",
        platform=profile,
        system="petclinic",
        analyzer_pack="java-spring-data-jpa-v1",
        ruleset_version="spring-data-rules-v1",
        resolver_version="schema-resolver-v1",
        schema_profile=profile,
        scope_complete=True,
    )
    return spring_sca.JavaSpringEvidenceCompiler().compile(result, context)


def test_a_query_naming_another_entity_resolves_to_that_entitys_table() -> None:
    """`@Query("... FROM PetType ...")` on a Pet repository reads `types`, not `pets`."""
    analysis = _pet_repository(
        '  @Query("SELECT ptype FROM PetType ptype ORDER BY ptype.name")\n'
        "  List<PetType> findPetTypes();"
    )

    evidence = _compile(analysis)

    assert [edge.edge_type for edge in evidence.edges] == ["READS"]
    assert evidence.edges[0].dataset_urn.endswith(":types")
    assert "query-entity-conflict" not in evidence.status_reasons


def test_jpql_without_a_select_clause_is_supported() -> None:
    """`FROM X WHERE ...` is valid JPQL shorthand and reads X."""
    analysis = _pet_repository(
        '  @Query("FROM PetType ptype WHERE ptype.id = :typeId")\n'
        "  List<PetType> findPetTypes();"
    )

    evidence = _compile(analysis)

    assert [edge.edge_type for edge in evidence.edges] == ["READS"]
    assert evidence.edges[0].dataset_urn.endswith(":types")
    assert "unsupported-query" not in evidence.status_reasons


def test_a_query_naming_an_entity_outside_the_scope_stays_a_conflict() -> None:
    analysis = _pet_repository(
        '  @Query("SELECT o FROM Nowhere o")\n  List<PetType> findPetTypes();'
    )

    evidence = _compile(analysis)

    assert evidence.edges == ()
    assert "query-entity-conflict" in evidence.status_reasons


def test_a_schema_for_another_profile_is_skipped_not_unsupported() -> None:
    """Shipping an HSQLDB dev schema must not make a repository unanalysable."""
    from lineage_api.services.analyzer_registry import _is_policy_skipped_sql

    assert _is_policy_skipped_sql("customers/src/main/resources/db/hsqldb/schema.sql")
    assert _is_policy_skipped_sql("src/main/resources/db/h2/schema.sql")
    assert _is_policy_skipped_sql("src/main/resources/db/oracle/schema.sql")
    # Not a profile schema at all: still unsupported, so nothing is silently ignored.
    assert not _is_policy_skipped_sql("src/main/resources/migrations/V1__init.sql")
