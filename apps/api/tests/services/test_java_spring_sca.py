from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from lineage_api.services.java_spring_sca import (
    JavaSpringAnalysisError,
    JavaSpringScaAnalyzer,
    JavaSpringScaLimits,
    JavaSpringSource,
)


CORPUS_ROOT = (
    Path(__file__).resolve().parents[4]
    / "fixtures"
    / "repositories"
    / "java-spring-corpus"
)


def _source(path: str, text: str, *, dialect: str | None = None) -> JavaSpringSource:
    return JavaSpringSource(path=path, content=text.encode(), sql_dialect=dialect)


def _corpus_sources() -> tuple[JavaSpringSource, ...]:
    sources = []
    for path in sorted(item for item in CORPUS_ROOT.rglob("*") if item.is_file()):
        relative_path = path.relative_to(CORPUS_ROOT).as_posix()
        dialect = "h2" if relative_path.endswith("schema.sql") else None
        sources.append(
            JavaSpringSource(relative_path, path.read_bytes(), sql_dialect=dialect)
        )
    return tuple(sources)


def _facts(result, kind: str):
    return tuple(fact for fact in result.facts if fact.kind == kind)


def _maven_build(
    version: str = "3.5.5",
    *,
    dependency_version: str | None = None,
) -> str:
    dependency_version_xml = (
        f"<version>{dependency_version}</version>" if dependency_version is not None else ""
    )
    return f"""<project>
      <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>{version}</version>
      </parent>
      <dependencies><dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-data-jpa</artifactId>
        {dependency_version_xml}
      </dependency></dependencies>
    </project>"""


def _gradle_build(version: str = "3.5.5") -> str:
    return f'''plugins {{
      id "org.springframework.boot" version "{version}"
    }}
    dependencies {{
      implementation "org.springframework.boot:spring-boot-starter-data-jpa:{version}"
    }}'''


def test_literal_maven_and_gradle_facts_classify_supported_framework() -> None:
    result = JavaSpringScaAnalyzer().analyze(_corpus_sources())

    assert result.framework.status == "supported"
    assert result.framework.framework == "spring-data-jpa"
    assert result.framework.evidence == (
        "build.gradle:boot-plugin:org.springframework.boot:3.5.5",
        "build.gradle:data-jpa:org.springframework.boot:spring-boot-starter-data-jpa:3.5.5",
        "pom.xml:boot-parent:org.springframework.boot:spring-boot-starter-parent:3.5.5",
        "pom.xml:data-jpa:org.springframework.boot:spring-boot-starter-data-jpa:3.5.5:inherited",
    )


@pytest.mark.parametrize(
    ("path", "build", "expected_code"),
    [
        (
            "pom.xml",
            """<project><dependencies><dependency><groupId>example</groupId>"
            "<artifactId>custom-data</artifactId><version>1</version>"
            "</dependency></dependencies></project>""",
            "unknown-framework",
        ),
        (
            "build.gradle",
            'dependencies { implementation "org.springframework.boot:'
            'spring-boot-starter-data-jpa:${springVersion}" }',
            "dynamic-framework-evidence",
        ),
        (
            "pom.xml",
            """<project><dependencies><dependency>"
            "<groupId>org.springframework.boot</groupId>"
            "<artifactId>spring-boot-starter-data-jpa</artifactId>"
            "<version>${spring.version}</version>"
            "</dependency></dependencies></project>""",
            "dynamic-framework-evidence",
        ),
    ],
)
def test_unknown_or_dynamic_build_evidence_is_residue(
    path: str, build: str, expected_code: str
) -> None:
    result = JavaSpringScaAnalyzer().analyze((_source(path, build),))

    assert result.framework.status == "unsupported"
    assert result.framework.framework is None
    assert expected_code in {entry.code for entry in result.residue}


@pytest.mark.parametrize(
    "build",
    [
        '// implementation "org.springframework.boot:spring-boot-starter-data-jpa:3.5.5"',
        '/* implementation "org.springframework.boot:spring-boot-starter-data-jpa:3.5.5" */',
        "def example = 'implementation \"org.springframework.boot:"
        "spring-boot-starter-data-jpa:3.5.5\"'",
        'repositories { implementation "org.springframework.boot:'
        'spring-boot-starter-data-jpa:3.5.5" }',
    ],
)
def test_gradle_comments_strings_and_unrelated_blocks_are_not_framework_evidence(
    build: str,
) -> None:
    result = JavaSpringScaAnalyzer().analyze((_source("build.gradle", build),))

    assert result.framework.status == "unsupported"
    assert result.framework.evidence == ()
    assert [entry.code for entry in result.residue] == ["unknown-framework"]


def test_conflicting_literal_versions_are_ambiguous_residue() -> None:
    pom = _maven_build("3.5.5")
    gradle = _gradle_build("3.4.0")

    result = JavaSpringScaAnalyzer().analyze(
        (_source("pom.xml", pom), _source("build.gradle", gradle))
    )

    assert result.framework.status == "unsupported"
    assert result.framework.evidence == ()
    assert [entry.code for entry in result.residue] == [
        "ambiguous-framework-evidence"
    ]


def test_conflicting_literal_jpa_versions_are_ambiguous_residue() -> None:
    build = """<project><parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.5.5</version></parent><dependencies><dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-data-jpa</artifactId>
        <version>3.4.0</version></dependency><dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-data-jpa</artifactId>
        <version>3.5.0</version></dependency></dependencies></project>"""

    result = JavaSpringScaAnalyzer().analyze((_source("pom.xml", build),))

    assert result.framework.status == "unsupported"
    assert "ambiguous-framework-evidence" in {
        entry.code for entry in result.residue
    }


def test_unsupported_framework_never_emits_spring_semantic_facts() -> None:
    java = '''package example;
interface MaybeRepository {
  @Query("SELECT x FROM Unknown x")
  Object find();
}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source(
                "pom.xml",
                "<project><groupId>example</groupId><artifactId>unknown</artifactId></project>",
            ),
            _source("src/MaybeRepository.java", java),
        )
    )

    assert result.framework.status == "unsupported"
    assert _facts(result, "java.annotation")
    assert not any(fact.kind.startswith("spring.") for fact in result.facts)


@pytest.mark.parametrize(
    ("build", "code"),
    [
        (
            """<project><dependencies><dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-data-jpa</artifactId>
            <version>3.5.5</version>
            </dependency></dependencies></project>""",
            "missing-boot-evidence",
        ),
        (
            """<project><parent>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-parent</artifactId>
            <version>3.5.5</version>
            </parent></project>""",
            "missing-jpa-dependency",
        ),
        (
            """<project><parent>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-parent</artifactId>
            <version>3.5.5</version>
            </parent><dependencyManagement><dependencies><dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-data-jpa</artifactId>
            </dependency></dependencies></dependencyManagement></project>""",
            "missing-jpa-dependency",
        ),
        (
            """<project><parent>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-parent</artifactId>
            <version>3.5.5</version>
            </parent><build><plugins><plugin>
            <groupId>example</groupId><artifactId>plugin</artifactId>
            <dependencies><dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-data-jpa</artifactId>
            </dependency></dependencies></plugin></plugins></build></project>""",
            "missing-jpa-dependency",
        ),
        (
            """<project><parent>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-parent</artifactId>
            <version>3.5.5</version>
            </parent><dependencies><dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-data-jpa</artifactId>
            <scope>test</scope></dependency></dependencies></project>""",
            "missing-jpa-dependency",
        ),
        (
            'dependencies { implementation "org.springframework.boot:'
            'spring-boot-starter-data-jpa:3.5.5" }',
            "missing-boot-evidence",
        ),
        (
            'plugins { id "org.springframework.boot" version "3.5.5" }',
            "missing-jpa-dependency",
        ),
    ],
)
def test_framework_requires_boot_and_active_top_level_jpa_dependency(
    build: str, code: str
) -> None:
    path = "pom.xml" if build.lstrip().startswith("<") else "build.gradle"

    result = JavaSpringScaAnalyzer().analyze((_source(path, build),))

    assert result.framework.status == "unsupported"
    assert code in {entry.code for entry in result.residue}


@pytest.mark.parametrize(
    ("version", "code"),
    [
        ("2.7.18", "unsupported-boot-version"),
        ("5.0.0", "unsupported-boot-version"),
        ("4.1.0-RC1", "unsupported-boot-version"),
        ("${spring-boot.version}", "dynamic-framework-evidence"),
        ("", "missing-boot-version"),
    ],
)
def test_boot_version_must_be_literal_supported_semantic_version(
    version: str, code: str
) -> None:
    result = JavaSpringScaAnalyzer().analyze(
        (_source("pom.xml", _maven_build(version)),)
    )

    assert result.framework.status == "unsupported"
    assert code in {entry.code for entry in result.residue}


def test_petclinic_boot_4_1_and_versionless_managed_jpa_are_supported() -> None:
    result = JavaSpringScaAnalyzer().analyze(
        (_source("pom.xml", _maven_build("4.1.0")),)
    )

    assert result.framework.status == "supported"
    assert result.framework.evidence == (
        "pom.xml:boot-parent:org.springframework.boot:spring-boot-starter-parent:4.1.0",
        "pom.xml:data-jpa:org.springframework.boot:spring-boot-starter-data-jpa:4.1.0:inherited",
    )


def test_versionless_boot_plugin_may_inherit_proven_boot_parent() -> None:
    build = _maven_build("4.1.0").replace(
        "</project>",
        """<build><plugins><plugin>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-maven-plugin</artifactId>
        </plugin></plugins></build></project>""",
    )

    result = JavaSpringScaAnalyzer().analyze((_source("pom.xml", build),))

    assert result.framework.status == "supported"
    assert (
        "pom.xml:boot-plugin:org.springframework.boot:spring-boot-maven-plugin:4.1.0:inherited"
        in result.framework.evidence
    )


def test_java_tree_sitter_facts_cover_j3_inputs_with_exact_locations() -> None:
    result = JavaSpringScaAnalyzer().analyze(_corpus_sources())

    types = {fact.subject: fact for fact in _facts(result, "java.type")}
    assert types["example.Owner"].attribute("declarationKind") == "class"
    assert types["example.OwnerRepository"].attribute("declarationKind") == "interface"
    assert types["example.OwnerRepository"].attribute("extends") == (
        "JpaRepository<Owner, Integer>",
    )
    assert types["example.Owner"].location.path.endswith("Owner.java")
    assert types["example.Owner"].location.line == 8
    assert types["example.Owner"].location.ast_kind == "class_declaration"
    assert types["example.Owner"].location.end_byte > types["example.Owner"].location.start_byte

    packages = _facts(result, "java.package")
    imports = _facts(result, "java.import")
    assert {fact.subject for fact in packages} == {"example"}
    assert "org.springframework.data.jpa.repository.JpaRepository" in {
        fact.subject for fact in imports
    }

    entity_tables = _facts(result, "spring.entity-table")
    assert [
        (fact.subject, fact.attribute("table"), fact.attribute("explicit"))
        for fact in entity_tables
    ] == [("example.Owner", "owners", "true")]

    repositories = _facts(result, "spring.repository-association")
    assert len(repositories) == 1
    assert repositories[0].subject == "example.OwnerRepository"
    assert repositories[0].attribute("baseType") == "JpaRepository"
    assert repositories[0].attribute("entityType") == "Owner"
    assert repositories[0].attribute("idType") == "Integer"

    bindings = _facts(result, "spring.repository-binding")
    assert [(fact.attribute("field"), fact.attribute("parameter")) for fact in bindings] == [
        ("owners", "owners")
    ]

    invocations = _facts(result, "java.invocation")
    assert [(fact.attribute("receiver"), fact.attribute("method")) for fact in invocations] == [
        ("owners", "findById"),
        ("owners", "save"),
    ]
    assert all(fact.location.ast_kind == "method_invocation" for fact in invocations)


def test_annotations_preserve_literals_and_dynamic_query_is_residue() -> None:
    result = JavaSpringScaAnalyzer().analyze(_corpus_sources())

    annotations = _facts(result, "java.annotation")
    table = next(fact for fact in annotations if fact.subject.endswith("Owner:@Table"))
    assert table.attribute("name") == "owners"
    literal_query = _facts(result, "spring.query")
    assert len(literal_query) == 1
    assert literal_query[0].attribute("literal") == "true"
    assert "SELECT o FROM Owner o" in literal_query[0].attribute("query")
    dynamic = [entry for entry in result.residue if entry.code == "dynamic-query"]
    assert len(dynamic) == 1
    assert dynamic[0].symbol == "BASE_QUERY"
    assert dynamic[0].location.ast_kind == "annotation"


@pytest.mark.parametrize(
    ("imports", "declarations", "expected_code"),
    [
        (
            "import fake.Entity;",
            "@Entity class Owner {}",
            "unresolved-framework-symbol",
        ),
        (
            "",
            "class Entity {} @Entity class Owner {}",
            "shadowed-framework-symbol",
        ),
        (
            "import jakarta.persistence.*;",
            "@Entity class Owner {}",
            "wildcard-framework-symbol",
        ),
        (
            "import jakarta.persistence.Entity; import fake.Entity;",
            "@Entity class Owner {}",
            "ambiguous-framework-symbol",
        ),
    ],
)
def test_only_exact_resolved_jpa_entity_symbol_drives_mapping(
    imports: str, declarations: str, expected_code: str
) -> None:
    java = f"package example; {imports} {declarations}"
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/Owner.java", java),
        )
    )

    assert _facts(result, "spring.entity-table") == ()
    assert expected_code in {entry.code for entry in result.residue}


def test_fake_repository_and_query_symbols_never_drive_spring_facts() -> None:
    java = '''package example;
import fake.JpaRepository;
import fake.Query;
class Owner {}
interface OwnerRepository extends JpaRepository<Owner, Integer> {
  @Query("SELECT o FROM Owner o") Object find();
}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/OwnerRepository.java", java),
        )
    )

    assert _facts(result, "spring.repository-association") == ()
    assert _facts(result, "spring.query") == ()
    assert {entry.symbol for entry in result.residue} >= {"JpaRepository", "Query"}


def test_repository_local_type_cannot_shadow_an_approved_fqn() -> None:
    fake_annotation = '''package jakarta.persistence;
public @interface Entity {}
'''
    owner = '''package example;
import jakarta.persistence.Entity;
@Entity class Owner {}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/jakarta/persistence/Entity.java", fake_annotation),
            _source("src/example/Owner.java", owner),
        )
    )

    assert _facts(result, "spring.entity-table") == ()
    assert "shadowed-framework-symbol" in {
        entry.code for entry in result.residue
    }


def test_repository_requires_exactly_two_generics_and_resolved_local_entity() -> None:
    java = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
interface TooMany extends JpaRepository<Missing, Integer, String> {}
interface MissingEntity extends JpaRepository<Missing, Integer> {}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/Repositories.java", java),
        )
    )

    assert _facts(result, "spring.repository-association") == ()
    assert {entry.code for entry in result.residue} >= {
        "invalid-repository-generics",
        "unresolved-repository-entity",
    }


@pytest.mark.parametrize(
    ("table_annotation", "expected_code"),
    [
        ("@Table(name = TABLE_NAME)", "dynamic-table-mapping"),
        (
            '@Table(name = "owners", schema = SCHEMA_NAME)',
            "dynamic-table-mapping",
        ),
        ("@fake.Table(name = \"owners\")", "unresolved-table-mapping"),
    ],
)
def test_present_dynamic_or_unresolved_table_never_defaults(
    table_annotation: str, expected_code: str
) -> None:
    java = f'''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity
{table_annotation}
class Owner {{ static final String TABLE_NAME = "owners"; }}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/Owner.java", java),
        )
    )

    assert _facts(result, "spring.entity-table") == ()
    assert expected_code in {entry.code for entry in result.residue}


def test_absent_table_annotation_emits_explicit_default_candidate() -> None:
    java = '''package example;
import jakarta.persistence.Entity;
@Entity class Owner {}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/Owner.java", java),
        )
    )

    mapping = _facts(result, "spring.entity-table")
    assert len(mapping) == 1
    assert mapping[0].attribute("explicit") == "false"
    assert mapping[0].attribute("table") == "Owner"


def test_unbound_and_shadowed_repository_receivers_do_not_emit_invocations() -> None:
    repository = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
class Owner {}
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
'''
    controller = '''package example;
class OwnerController {
  private final OwnerRepository owners;
  OwnerController(OwnerRepository owners) { this.owners = owners; }
  Owner unbound(OwnerRepository owners, Owner value) { return owners.save(value); }
}
class UnboundController {
  private OwnerRepository owners;
  Owner save(Owner value) { return owners.save(value); }
}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/OwnerRepository.java", repository),
            _source("src/example/OwnerController.java", controller),
        )
    )

    assert _facts(result, "java.invocation") == ()
    assert {entry.code for entry in result.residue} >= {
        "shadowed-repository-receiver",
        "unbound-repository-receiver",
    }


def test_multiple_constructors_do_not_prove_repository_injection() -> None:
    repository = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
class Owner {}
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
'''
    service = '''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  OwnerService(OwnerRepository owners, String mode) { this.owners = owners; }
  Owner save(Owner value) { return owners.save(value); }
}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/OwnerRepository.java", repository),
            _source("src/example/OwnerService.java", service),
        )
    )

    assert _facts(result, "spring.repository-binding") == ()
    assert _facts(result, "java.invocation") == ()
    assert "ambiguous-repository-injection" in {
        entry.code for entry in result.residue
    }


def test_exact_autowired_annotation_selects_one_of_multiple_constructors() -> None:
    repository = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
class Owner {}
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
'''
    service = '''package example;
import org.springframework.beans.factory.annotation.Autowired;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService() { this.owners = null; }
  @Autowired OwnerService(OwnerRepository owners) { this.owners = owners; }
  Owner save(Owner value) { return owners.save(value); }
}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/OwnerRepository.java", repository),
            _source("src/example/OwnerService.java", service),
        )
    )

    assert len(_facts(result, "spring.repository-binding")) == 1
    assert len(_facts(result, "java.invocation")) == 1


def test_nested_types_have_collision_free_qualified_names_and_ast_paths() -> None:
    java = '''package example;
class A { static class Inner {} }
class B { static class Inner {} }
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/Nested.java", java),
        )
    )

    types = _facts(result, "java.type")
    assert {fact.subject for fact in types} == {
        "example.A",
        "example.A.Inner",
        "example.B",
        "example.B.Inner",
    }
    assert len({fact.location.ast_path for fact in types}) == 4
    assert all("named[" in fact.location.ast_path for fact in types)
    assert all(fact.location.ast_path for fact in result.facts)
    assert all(entry.location.ast_path for entry in result.residue)


def test_comments_and_strings_do_not_create_java_structure_or_invocations() -> None:
    build = _gradle_build()
    java = '''package example;
// class FakeRepository extends JpaRepository<Fake, Integer> {}
class RealService {
  String example = "owners.save(fake) class AlsoFake {}";
}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (_source("build.gradle", build), _source("src/RealService.java", java))
    )

    assert [fact.subject for fact in _facts(result, "java.type")] == [
        "example.RealService"
    ]
    assert _facts(result, "java.invocation") == ()


def test_sqlglot_explicit_dialect_emits_deterministic_table_facts() -> None:
    result = JavaSpringScaAnalyzer().analyze(_corpus_sources())

    tables = _facts(result, "sql.table")
    assert [(fact.subject, fact.attribute("schema")) for fact in tables] == [
        ("owners", "public"),
        ("visits", "public"),
    ]
    assert all(fact.location.path.endswith("schema.sql") for fact in tables)
    assert all(fact.location.ast_kind == "table" for fact in tables)
    assert all(fact.location.end_byte > fact.location.start_byte for fact in tables)
    assert all(fact.attribute("dialect") == "postgres" for fact in tables)
    assert all(
        fact.attribute("dialectMode") == "h2-postgres-compatibility"
        for fact in tables
    )


@pytest.mark.parametrize(
    "identifier",
    ["?", ":name", "${name}", "{{ name }}", "@name"],
)
def test_dynamic_sql_table_identifiers_are_residue(identifier: str) -> None:
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "db/dynamic.sql",
                f"create table {identifier} (id integer);",
                dialect="postgres",
            ),
        )
    )

    assert _facts(result, "sql.table") == ()
    assert "dynamic-sql-identifier" in {entry.code for entry in result.residue}


def test_h2_compatibility_rejects_unsupported_h2_constructs() -> None:
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "db/h2/identity.sql",
                "create table owners (id integer auto_increment primary key);",
                dialect="h2",
            ),
        )
    )

    assert _facts(result, "sql.table") == ()
    assert [entry.code for entry in result.residue] == [
        "unsupported-h2-construct"
    ]


def test_sql_requires_explicit_dialect_and_malformed_sql_is_residue() -> None:
    build = _maven_build()
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", build),
            _source("db/no-dialect.sql", "create table hidden(id int)"),
            _source("db/broken.sql", "create table ???", dialect="h2"),
        )
    )

    assert _facts(result, "sql.table") == ()
    assert {entry.code for entry in result.residue} == {
        "missing-sql-dialect",
        "malformed-sql",
    }


def test_unsupported_sql_becomes_residue_without_logging_source(
    caplog: pytest.LogCaptureFixture,
) -> None:
    build = _maven_build()
    sql = "CREATE USER app IDENTIFIED BY 'SECRET-VALUE';"

    with caplog.at_level(logging.WARNING, logger="sqlglot"):
        result = JavaSpringScaAnalyzer().analyze(
            (
                _source("pom.xml", build),
                _source("db/admin.sql", sql, dialect="mysql"),
            )
        )

    assert [entry.code for entry in result.residue] == ["unsupported-sql"]
    assert "SECRET-VALUE" not in caplog.text


def test_malformed_java_is_quarantined_without_partial_facts() -> None:
    build = _gradle_build()
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("build.gradle", build),
            _source("src/Broken.java", "class Broken { void nope( { owners.save(x); }"),
        )
    )

    assert not any(fact.location.path == "src/Broken.java" for fact in result.facts)
    assert [entry.code for entry in result.residue] == ["malformed-java"]


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (JavaSpringSource("../escape.java", b"class A {}"), "relative tracked path"),
        (JavaSpringSource("A.java", "not-bytes"), "content must be bytes"),
        (JavaSpringSource("A.java", b"class A {}", sql_dialect="h2"), "SQL dialect"),
    ],
)
def test_source_contract_rejects_unsafe_path_or_content(
    source: JavaSpringSource, message: str
) -> None:
    with pytest.raises(JavaSpringAnalysisError, match=message):
        JavaSpringScaAnalyzer().analyze((source,))


def test_duplicate_paths_and_resource_limits_fail_closed() -> None:
    source = _source("src/A.java", "class A {}")
    with pytest.raises(JavaSpringAnalysisError, match="duplicate"):
        JavaSpringScaAnalyzer().analyze((source, source))
    with pytest.raises(JavaSpringAnalysisError, match="file count"):
        JavaSpringScaAnalyzer(JavaSpringScaLimits(max_files=1)).analyze(
            (source, _source("src/B.java", "class B {}"))
        )
    with pytest.raises(JavaSpringAnalysisError, match="per-file byte"):
        JavaSpringScaAnalyzer(JavaSpringScaLimits(max_file_bytes=5)).analyze((source,))
    with pytest.raises(JavaSpringAnalysisError, match="total byte"):
        JavaSpringScaAnalyzer(JavaSpringScaLimits(max_total_bytes=5)).analyze((source,))


def test_source_generator_stops_after_exact_limit_plus_one() -> None:
    consumed: list[int] = []

    def sources():
        for index in range(10):
            consumed.append(index)
            yield _source(f"src/{index}.java", f"class A{index} {{}}")

    with pytest.raises(JavaSpringAnalysisError, match="file count"):
        JavaSpringScaAnalyzer(JavaSpringScaLimits(max_files=2)).analyze(sources())

    assert consumed == [0, 1, 2]


def test_fact_and_residue_limits_fail_closed() -> None:
    build = _gradle_build()
    java = "package example; class A {} class B {}"
    with pytest.raises(JavaSpringAnalysisError, match="fact limit"):
        JavaSpringScaAnalyzer(JavaSpringScaLimits(max_facts=1)).analyze(
            (_source("build.gradle", build), _source("src/Many.java", java))
        )
    unknown_files = tuple(
        _source(f"db/{index}.sql", "select 1") for index in range(2)
    )
    with pytest.raises(JavaSpringAnalysisError, match="residue limit"):
        JavaSpringScaAnalyzer(JavaSpringScaLimits(max_residue=1)).analyze(unknown_files)


def test_results_are_deterministic_sorted_and_immutable() -> None:
    sources = _corpus_sources()
    first = JavaSpringScaAnalyzer().analyze(tuple(reversed(sources)))
    second = JavaSpringScaAnalyzer().analyze(sources)

    assert first == second
    assert tuple(fact.identifier for fact in first.facts) == tuple(
        sorted(fact.identifier for fact in first.facts)
    )
    with pytest.raises(FrozenInstanceError):
        first.framework.status = "changed"  # type: ignore[misc]
