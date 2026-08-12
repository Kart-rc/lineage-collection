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
