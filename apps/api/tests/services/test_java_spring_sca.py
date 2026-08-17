from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import lineage_api.services.java_spring_sca as spring_sca
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


def _compile_java_spring(
    sources: tuple[JavaSpringSource, ...],
    *,
    schema_profile: str = "postgres",
    scope_complete: bool = True,
):
    analysis = JavaSpringScaAnalyzer().analyze(sources)
    context = spring_sca.JavaSpringEvidenceContext(
        origin="https://github.com/example/spring-service",
        repository="spring-service",
        revision="1" * 40,
        scope_digest="sha256:" + "2" * 64,
        environment="staging",
        platform=schema_profile,
        system="petclinic",
        analyzer_pack="java-spring-data-jpa-v1",
        ruleset_version="spring-data-rules-v1",
        resolver_version="schema-resolver-v1",
        schema_profile=schema_profile,
        scope_complete=scope_complete,
    )
    return spring_sca.JavaSpringEvidenceCompiler().compile(analysis, context)


def _lineage_sources(
    *,
    entity: str,
    repository: str,
    service: str,
    schema: str = "create table owners (id integer primary key);",
) -> tuple[JavaSpringSource, ...]:
    return (
        _source("pom.xml", _maven_build()),
        _source("src/example/Owner.java", entity),
        _source("src/example/OwnerRepository.java", repository),
        _source("src/example/OwnerService.java", service),
        _source(
            "src/main/resources/db/postgres/schema.sql",
            schema,
            dialect="postgres",
        ),
    )


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


def test_boot_4_rejects_explicit_boot_starter_data_jpa_3() -> None:
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source(
                "pom.xml",
                _maven_build("4.1.0", dependency_version="3.1.0"),
            ),
        )
    )

    assert result.framework.status == "unsupported"
    assert "incompatible-framework-cell" in {
        entry.code for entry in result.residue
    }


def test_direct_spring_data_jpa_is_not_guessed_without_compatibility_map() -> None:
    build = _maven_build("3.5.5", dependency_version="3.5.0").replace(
        "org.springframework.boot</groupId>\n        <artifactId>spring-boot-starter-data-jpa",
        "org.springframework.data</groupId>\n        <artifactId>spring-data-jpa",
    )

    result = JavaSpringScaAnalyzer().analyze((_source("pom.xml", build),))

    assert result.framework.status == "unsupported"
    assert "unsupported-direct-spring-data-jpa" in {
        entry.code for entry in result.residue
    }


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
    assert dynamic[0].symbol.startswith("dynamic-query:sha256:")
    assert dynamic[0].location.ast_kind == "annotation"


def test_residue_never_contains_malformed_java_or_dynamic_query_source_secrets() -> None:
    secret = "SUPER_SECRET_MARKER_7d78d5"
    malformed = f"class Broken {{ void bad( {{ {secret} }}"
    query = f'''package example;
import org.springframework.data.jpa.repository.Query;
interface Queries {{ @Query({secret}) Object find(); }}
'''

    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/Broken.java", malformed),
            _source("src/example/Queries.java", query),
        )
    )

    assert {entry.code for entry in result.residue} >= {
        "malformed-java",
        "dynamic-query",
    }
    for entry in result.residue:
        assert secret not in entry.message
        assert secret not in entry.symbol
        assert len(entry.code) <= 64
        assert len(entry.message) <= 96
        assert len(entry.symbol) <= 128
        assert len(entry.location.path.encode("utf-8")) <= 512
        assert len(entry.location.ast_kind) <= 64
        assert len(entry.location.ast_path) <= 512


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
            "import com.vendor.orm.*;",
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
    assert {
        entry.code for entry in result.residue
    } >= {"unresolved-framework-symbol"}
    assert all(
        entry.symbol.startswith(f"{entry.code}:sha256:")
        for entry in result.residue
    )


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


def test_repository_association_requires_interface_declaration() -> None:
    java = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
class Owner {}
class BadRepository extends JpaRepository<Owner, Integer> {}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/BadRepository.java", java),
        )
    )

    assert _facts(result, "spring.repository-association") == ()
    assert "invalid-repository-declaration" in {
        entry.code for entry in result.residue
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


def test_table_unique_constraints_do_not_block_a_literal_table_name() -> None:
    """spring-petclinic-rest's Role entity: `@Table(name = "roles",
    uniqueConstraints = @UniqueConstraint(...))`. The extra nested-annotation
    attribute must not defeat the literal `name` extraction.
    """
    java = '''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
@Entity
@Table(name = "roles", uniqueConstraints = @UniqueConstraint(columnNames = {"username", "role"}))
class Role {}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/Role.java", java),
        )
    )

    tables = _facts(result, "spring.entity-table")
    assert [fact.attribute("table") for fact in tables] == ["roles"]
    assert "dynamic-table-mapping" not in {item.code for item in result.residue}


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


def test_lambda_catch_and_enhanced_for_binders_shadow_repository_field() -> None:
    repository = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
class Owner {}
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
'''
    service = '''package example;
import java.util.List;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  void lambda(List<OwnerRepository> values) {
    values.forEach(owners -> owners.save(null));
  }
  void caught() {
    try { throw new RuntimeException(); }
    catch (RuntimeException owners) { owners.save(null); }
  }
  void loop(List<OwnerRepository> values) {
    for (OwnerRepository owners : values) { owners.save(null); }
  }
}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/OwnerRepository.java", repository),
            _source("src/example/OwnerService.java", service),
        )
    )

    assert _facts(result, "java.invocation") == ()
    assert len(
        [
            entry
            for entry in result.residue
            if entry.code == "shadowed-repository-receiver"
        ]
    ) == 3


def test_block_local_repository_shadow_preserves_calls_outside_its_scope() -> None:
    repository = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
class Owner {}
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
'''
    service = '''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  void scoped() {
    owners.save(null);
    { OwnerRepository owners = null; owners.save(null); }
    owners.save(null);
  }
}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/OwnerRepository.java", repository),
            _source("src/example/OwnerService.java", service),
        )
    )

    assert len(_facts(result, "java.invocation")) == 2
    assert len(
        [
            entry
            for entry in result.residue
            if entry.code == "shadowed-repository-receiver"
        ]
    ) == 1


def test_pattern_and_resource_binders_shadow_only_their_lexical_scopes() -> None:
    repository = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
class Owner {}
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
'''
    service = '''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  OwnerRepository acquire() { return this.owners; }
  void scoped(Object candidate) throws Exception {
    owners.save(null);
    if (candidate instanceof OwnerRepository owners) { owners.save(null); }
    owners.save(null);
    try (OwnerRepository owners = acquire()) { owners.save(null); }
    owners.save(null);
  }
}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/OwnerRepository.java", repository),
            _source("src/example/OwnerService.java", service),
        )
    )

    assert len(_facts(result, "java.invocation")) == 3
    assert len(
        [
            entry
            for entry in result.residue
            if entry.code == "shadowed-repository-receiver"
        ]
    ) == 2


def test_negated_instanceof_guard_binds_receiver_after_terminating_branch() -> None:
    repository = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
class Owner {}
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
'''
    service = '''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  void guarded(Object candidate) {
    if (!(candidate instanceof OwnerRepository owners)) return;
    owners.save(null);
  }
}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/OwnerRepository.java", repository),
            _source("src/example/OwnerService.java", service),
        )
    )

    assert _facts(result, "java.invocation") == ()
    assert len(
        [
            entry
            for entry in result.residue
            if entry.code == "shadowed-repository-receiver"
        ]
    ) == 1


def test_negated_instanceof_else_binds_only_unqualified_else_receiver() -> None:
    repository = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
class Owner {}
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
'''
    service = '''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  void guarded(Object candidate) {
    if (!(candidate instanceof OwnerRepository owners)) {
      this.owners.save(null);
    } else {
      owners.save(null);
    }
  }
}
'''
    result = JavaSpringScaAnalyzer().analyze(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/OwnerRepository.java", repository),
            _source("src/example/OwnerService.java", service),
        )
    )

    invocations = _facts(result, "java.invocation")
    assert len(invocations) == 1
    assert invocations[0].attribute("receiver") == "owners"
    assert len(
        [
            entry
            for entry in result.residue
            if entry.code == "shadowed-repository-receiver"
        ]
    ) == 1


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


def test_autowired_field_injected_repository_binds_like_constructor_injection() -> None:
    """spring-petclinic-rest's UserServiceImpl uses `@Autowired private
    UserRepository userRepository;` field injection rather than a constructor. The
    declared field type is just as statically provable as a constructor parameter.
    """
    repository = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
class Owner {}
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
'''
    service = '''package example;
import org.springframework.beans.factory.annotation.Autowired;
class OwnerService {
  @Autowired
  private OwnerRepository owners;
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

    bindings = _facts(result, "spring.repository-binding")
    assert [(f.attribute("field"), f.attribute("injectionMode")) for f in bindings] == [
        ("owners", "field")
    ]
    assert len(_facts(result, "java.invocation")) == 1
    assert "unbound-repository-receiver" not in {item.code for item in result.residue}


def test_setter_injected_repository_stays_unbound() -> None:
    """Setter injection is not statically provable the way a field or constructor
    declaration is, so it must stay quarantined rather than guessed.
    """
    repository = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
class Owner {}
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
'''
    service = '''package example;
import org.springframework.beans.factory.annotation.Autowired;
class OwnerService {
  private OwnerRepository owners;
  @Autowired
  public void setOwners(OwnerRepository owners) { this.owners = owners; }
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
    assert "unbound-repository-receiver" in {item.code for item in result.residue}


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
        (
            JavaSpringSource(f"{'a' * 513}.java", b"class A {}"),
            "source path byte limit",
        ),
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


def test_wide_ast_is_indexed_once_and_obeys_ast_node_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fields = "\n".join(f"  int field{index};" for index in range(1_500))
    java = f"class Wide {{\n{fields}\n}}"
    analyzer = JavaSpringScaAnalyzer()
    original = analyzer._index_ast_paths
    calls = 0

    def counted(path, root):
        nonlocal calls
        calls += 1
        return original(path, root)

    monkeypatch.setattr(analyzer, "_index_ast_paths", counted)
    result = analyzer.analyze(
        (_source("pom.xml", _maven_build()), _source("src/Wide.java", java))
    )

    assert calls == 1
    assert 1_500 < result.stats.ast_nodes_indexed < 10_000
    with pytest.raises(JavaSpringAnalysisError, match="AST node limit"):
        JavaSpringScaAnalyzer(
            JavaSpringScaLimits(max_ast_nodes=100)
        ).analyze(
            (_source("pom.xml", _maven_build()), _source("src/Wide.java", java))
        )


@pytest.mark.parametrize(
    "build",
    [
        "}" + _gradle_build(),
        _gradle_build()[:-1],
        f"allprojects {{ {_gradle_build()} }}",
    ],
)
def test_malformed_or_nested_gradle_build_context_is_residue(build: str) -> None:
    result = JavaSpringScaAnalyzer().analyze(
        (_source("build.gradle", build),)
    )

    assert result.framework.status == "unsupported"
    assert "malformed-build-file" in {entry.code for entry in result.residue}


@pytest.mark.parametrize(
    "build",
    [
        '''def fake = /plugins {
  id "org.springframework.boot" version "3.5.5"
}
dependencies {
  implementation "org.springframework.boot:spring-boot-starter-data-jpa:3.5.5"
}/''',
        '''def fake = """plugins {
  id "org.springframework.boot" version "3.5.5"
}
dependencies {
  implementation "org.springframework.boot:spring-boot-starter-data-jpa:3.5.5"
}"""''',
        '''def fake = $/plugins {
  id "org.springframework.boot" version "3.5.5"
}
dependencies {
  implementation "org.springframework.boot:spring-boot-starter-data-jpa:3.5.5"
}/$''',
    ],
)
def test_unsupported_gradle_string_forms_fail_closed(build: str) -> None:
    result = JavaSpringScaAnalyzer().analyze((_source("build.gradle", build),))

    assert result.framework.status == "unsupported"
    assert "malformed-build-file" in {entry.code for entry in result.residue}


@pytest.mark.parametrize(
    "build",
    [
        f"wrapper({_gradle_build()})",
        f"def builds = [{_gradle_build()}]",
    ],
)
def test_gradle_framework_blocks_inside_parens_or_brackets_fail_closed(
    build: str,
) -> None:
    result = JavaSpringScaAnalyzer().analyze((_source("build.gradle", build),))

    assert result.framework.status == "unsupported"
    assert "malformed-build-file" in {entry.code for entry in result.residue}


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


def test_compiler_emits_only_proven_call_site_read_write_and_delete_edges() -> None:
    entity = '''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name = "owners") class Owner {}
'''
    repository = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
'''
    service = '''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Owner load(Integer id) { return owners.findById(id).orElseThrow(); }
  Owner store(Owner owner) { return owners.save(owner); }
  void purge(Owner owner) { owners.delete(owner); }
}
'''
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source("src/example/Owner.java", entity),
            _source("src/example/OwnerRepository.java", repository),
            _source("src/example/OwnerService.java", service),
            _source(
                "src/main/resources/db/postgres/schema.sql",
                "create table owners (id integer primary key);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.status == "COMPLETE"
    assert [edge.edge_type for edge in evidence.edges] == ["READS", "WRITES", "WRITES"]
    assert evidence.edges[0].lineage_tuple == (
        "urn:ldp:staging:postgres:petclinic:owners",
        "service://spring-service/example.OwnerService#load",
        "READS",
        "OwnerRepository.findById -> owners",
    )
    assert evidence.edges[1].lineage_tuple == (
        "service://spring-service/example.OwnerService#purge",
        "urn:ldp:staging:postgres:petclinic:owners",
        "WRITES",
        "OwnerRepository.delete -> owners [DELETE]",
    )
    assert evidence.edges[2].lineage_tuple == (
        "service://spring-service/example.OwnerService#store",
        "urn:ldp:staging:postgres:petclinic:owners",
        "WRITES",
        "OwnerRepository.save -> owners",
    )
    assert evidence.coverage.invocations_seen == 3
    assert evidence.coverage.invocations_proven == 3
    assert evidence.coverage.invocations_unresolved == 0
    assert evidence.coverage.edges_emitted == 3
    assert all(edge.exact and not edge.executed for edge in evidence.edges)
    for edge in evidence.edges:
        assert edge.evidence.origin == "https://github.com/example/spring-service"
        assert edge.evidence.revision == "1" * 40
        assert edge.evidence.scope_digest == "sha256:" + "2" * 64
        assert edge.evidence.analyzer_pack == "java-spring-data-jpa-v1"
        assert edge.evidence.ruleset_version == "spring-data-rules-v1"
        assert edge.evidence.resolver_version == "schema-resolver-v1"
        assert edge.evidence.schema_profile == "postgres"
        assert (
            edge.evidence.invocation.location.end_byte
            > edge.evidence.invocation.location.start_byte
        )
        assert edge.evidence.repository.kind == "spring.repository-association"
        assert edge.evidence.entity.kind == "spring.entity-table"
        assert edge.evidence.table.kind == "sql.table"
        assert edge.evidence.framework_evidence
        assert dict(edge.evidence.repository.attributes)["entityFqn"] == "example.Owner"
        assert dict(edge.evidence.entity.attributes)["table"] == "owners"
        assert dict(edge.evidence.table.attributes)["dialectMode"] == "native"
        serialized = edge.as_dict()
        assert serialized["from"] == [edge.from_urn]
        assert serialized["repo"] == "spring-service"
        assert serialized["digest"] == "1" * 40
        assert serialized["scopeDigest"] == "sha256:" + "2" * 64
        assert serialized["evidence"]["file"] == edge.evidence.invocation.location.path
        assert serialized["evidence"]["line"] == edge.evidence.invocation.location.line
        assert serialized["evidence"]["astPath"] == edge.evidence.invocation.location.ast_path


@pytest.mark.parametrize(
    ("query", "method", "expected_type", "expected_suffix"),
    [
        ('@Query("SELECT o FROM Owner o")', "customRead", "READS", "[JPQL SELECT]"),
        ('@Query("UPDATE Owner o SET o.city = ?1")', "customMutation", "WRITES", "[JPQL UPDATE]"),
        (
            '@Query("DELETE FROM Owner o WHERE o.id = ?1")',
            "customRemoval",
            "WRITES",
            "[JPQL DELETE]",
        ),
        (
            '@Query(value = "SELECT * FROM owners", nativeQuery = true)',
            "nativeFetch",
            "READS",
            "[SQL SELECT]",
        ),
        (
            '@Query(value = "INSERT INTO owners (id) VALUES (1)", nativeQuery = true)',
            "nativeInsert",
            "WRITES",
            "[SQL INSERT]",
        ),
        (
            '@Query(value = "UPDATE owners SET city = \'x\'", nativeQuery = true)',
            "nativeUpdate",
            "WRITES",
            "[SQL UPDATE]",
        ),
        (
            '@Query(value = "DELETE FROM owners WHERE id = 1", nativeQuery = true)',
            "nativeDelete",
            "WRITES",
            "[SQL DELETE]",
        ),
    ],
)
def test_literal_query_precedence_is_parsed_conservatively(
    query: str, method: str, expected_type: str, expected_suffix: str
) -> None:
    repository = f'''package example;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
interface OwnerRepository extends JpaRepository<Owner, Integer> {{
  {query} Object {method}();
}}
'''
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
                '@Entity @Table(name="owners") class Owner {}',
            ),
            _source("src/example/OwnerRepository.java", repository),
            _source(
                "src/example/OwnerService.java",
                f'''package example;
class OwnerService {{
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) {{ this.owners = owners; }}
  Object run() {{ return owners.{method}(); }}
}}
''',
            ),
            _source(
                "src/main/resources/db/postgres/schema.sql",
                "create table owners (id integer primary key, city text);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.status == "COMPLETE"
    assert len(evidence.edges) == 1
    assert evidence.edges[0].edge_type == expected_type
    assert evidence.edges[0].transform.endswith(expected_suffix)
    assert evidence.edges[0].evidence.operation_rationale.startswith("explicit @Query")
    assert query.split('"', 2)[1] not in evidence.to_bytes().decode()


def test_text_block_query_with_constructor_projection_is_modelled() -> None:
    # Java text blocks are string literals, and a JPQL `select new Dto(...)`
    # constructor projection reads exactly the listed properties of its root
    # entity — both are well-defined, so neither may fall out as dynamic-query.
    repository = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
interface OwnerRepository extends JpaRepository<Owner, Integer> {
  @Query("""
      select new example.OwnerDto(o.id, o.city)
      from Owner o
      order by o.city
      """)
  Object customRead();
}
'''
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
                '@Entity @Table(name="owners") class Owner {}',
            ),
            _source("src/example/OwnerRepository.java", repository),
            _source(
                "src/example/OwnerService.java",
                '''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Object run() { return owners.customRead(); }
}
''',
            ),
            _source(
                "src/main/resources/db/postgres/schema.sql",
                "create table owners (id integer primary key, city text);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.status == "COMPLETE"
    assert not any(entry.code == "dynamic-query" for entry in evidence.residue)
    assert len(evidence.edges) == 1
    assert evidence.edges[0].edge_type == "READS"
    assert evidence.edges[0].transform.endswith("[JPQL SELECT]")


def test_constructor_projection_grounds_elements_without_fqcn_artifacts() -> None:
    # The constructor's dotted FQCN must not be misread as property references,
    # and audit fields inherited from a @MappedSuperclass resolve like any other.
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Audited.java",
                'package example; import jakarta.persistence.Column; import jakarta.persistence.MappedSuperclass; '
                '@MappedSuperclass abstract class Audited { @Column(name="created_at") protected String createdAt; }',
            ),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Column; import jakarta.persistence.Entity; '
                'import jakarta.persistence.Id; import jakarta.persistence.Table; '
                '@Entity @Table(name="owners") class Owner extends Audited { '
                '@Id private Integer id; @Column(name="city") private String city; }',
            ),
            _source(
                "src/example/OwnerRepository.java",
                '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
interface OwnerRepository extends JpaRepository<Owner, Integer> {
  @Query("""
      select new example.owner.web.OwnerDto(o.id, o.city, o.createdAt)
      from Owner o
      order by o.city
      """)
  Object customRead();
}
''',
            ),
            _source(
                "src/example/OwnerService.java",
                '''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Object run() { return owners.customRead(); }
}
''',
            ),
            _source(
                "src/main/resources/db/postgres/schema.sql",
                "create table owners (id integer primary key, city text, created_at timestamp);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.status == "COMPLETE"
    assert not any(
        entry.code == "unresolved-query-property" for entry in evidence.residue
    )
    element_targets = {
        edge.dataset_urn for edge in evidence.edges if "#" in edge.dataset_urn
    }
    assert element_targets == {
        "urn:ldp:staging:postgres:petclinic:owners#id",
        "urn:ldp:staging:postgres:petclinic:owners#city",
        "urn:ldp:staging:postgres:petclinic:owners#created_at",
    }


def test_explicit_entity_join_reads_every_joined_entity() -> None:
    # `from Post p join User u on ...` reads BOTH entities: one invocation emits
    # a READS edge (and element edges) for each — never silently dropping the
    # joined side.
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Post.java",
                'package example; import jakarta.persistence.Column; import jakarta.persistence.Entity; '
                'import jakarta.persistence.Id; import jakarta.persistence.Table; '
                '@Entity @Table(name="posts") class Post { @Id private Long id; '
                '@Column(name="title") private String title; '
                '@Column(name="slug") private String slug; '
                '@Column(name="created_by") private Long createdBy; }',
            ),
            _source(
                "src/example/User.java",
                'package example; import jakarta.persistence.Column; import jakarta.persistence.Entity; '
                'import jakarta.persistence.Id; import jakarta.persistence.Table; '
                '@Entity @Table(name="users") class User { @Id private Long id; '
                '@Column(name="name") private String name; }',
            ),
            _source(
                "src/example/UserRepository.java",
                '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
interface UserRepository extends JpaRepository<User, Long> {}
''',
            ),
            _source(
                "src/example/PostRepository.java",
                '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
interface PostRepository extends JpaRepository<Post, Long> {
  @Query("""
      select new example.web.PostDto(p.id, p.title, u.name)
      from Post p join User u on p.createdBy = u.id
      where p.slug = :slug
      """)
  Object findBySlug(String slug);
}
''',
            ),
            _source(
                "src/example/PostService.java",
                '''package example;
class PostService {
  private final PostRepository posts;
  PostService(PostRepository posts) { this.posts = posts; }
  Object run() { return posts.findBySlug("intro"); }
}
''',
            ),
            _source(
                "src/main/resources/db/postgres/schema.sql",
                "create table posts (id bigint primary key, title text, slug text, created_by bigint);\n"
                "create table users (id bigint primary key, name text);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.status == "COMPLETE"
    dataset_reads = {
        edge.dataset_urn
        for edge in evidence.edges
        if edge.edge_type == "READS" and "#" not in edge.dataset_urn
    }
    assert dataset_reads == {
        "urn:ldp:staging:postgres:petclinic:posts",
        "urn:ldp:staging:postgres:petclinic:users",
    }
    element_targets = {
        edge.dataset_urn for edge in evidence.edges if "#" in edge.dataset_urn
    }
    assert "urn:ldp:staging:postgres:petclinic:users#name" in element_targets
    assert "urn:ldp:staging:postgres:petclinic:posts#title" in element_targets


@pytest.mark.parametrize(
    "query",
    [
        '@Query("SELECT FROM Owner")',
        '@Query("SELECT o FROM Owner o; DELETE FROM Owner o")',
        '@Query("UPDATE Owner o")',
        '@Query("DELETE Owner o")',
        (
            '@Query(value = "SELECT * FROM owners; DELETE FROM owners", '
            "nativeQuery = true)"
        ),
        (
            '@Query(value = "SELECT * FROM owners; /* boundary */ '
            'DELETE FROM owners", nativeQuery = true)'
        ),
        '@Query(value = "SELECT * FROM owners; -- trailing", nativeQuery = true)',
    ],
)
def test_malformed_or_multi_statement_explicit_query_is_residue(query: str) -> None:
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
                '@Entity @Table(name="owners") class Owner {}',
            ),
            _source(
                "src/example/OwnerRepository.java",
                f'''package example;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
interface OwnerRepository extends JpaRepository<Owner, Integer> {{
  {query} Object custom();
}}
''',
            ),
            _source(
                "src/example/OwnerService.java",
                '''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Object run() { return owners.custom(); }
}
''',
            ),
            _source(
                "src/main/resources/db/postgres/schema.sql",
                "create table owners (id integer primary key);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.edges == ()
    assert evidence.status == "INTEGRATION_REQUIRED"
    assert evidence.coverage.invocations_unresolved == 1
    assert "unsupported-query" in {item.code for item in evidence.residue}


def test_explicit_query_conflict_or_dynamic_query_never_falls_back_to_method_name() -> None:
    repository = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
interface OwnerRepository extends JpaRepository<Owner, Integer> {
  String QUERY = "SELECT o FROM Owner o";
  @Query(QUERY) Object findDynamic();
  @Query("SELECT p FROM Pet p") Object findConflict();
}
'''
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
                '@Entity @Table(name="owners") class Owner {} class Pet {}',
            ),
            _source("src/example/OwnerRepository.java", repository),
            _source(
                "src/example/OwnerService.java",
                '''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  void run() { owners.findDynamic(); owners.findConflict(); }
}
''',
            ),
            _source(
                "src/main/resources/db/postgres/schema.sql",
                "create table owners (id integer primary key);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.edges == ()
    assert evidence.status == "INTEGRATION_REQUIRED"
    assert evidence.coverage.invocations_seen == 2
    assert evidence.coverage.invocations_unresolved == 2
    assert {item.code for item in evidence.residue} >= {
        "dynamic-query",
        "query-entity-conflict",
    }


@pytest.mark.parametrize(
    ("schema_sources", "scope_complete", "expected_reason"),
    [
        ((), True, "missing-schema-table"),
        (
            (
                _source("db/postgres/a.sql", "create table owners (id int);", dialect="postgres"),
                _source("db/postgres/b.sql", "create table owners (id int);", dialect="postgres"),
            ),
            True,
            "ambiguous-schema-table",
        ),
        (
            (_source("db/h2/schema.sql", "create table owners (id int);", dialect="h2"),),
            True,
            "missing-schema-table",
        ),
        (
            (_source("db/postgres/schema.sql", "create table owners (id int);", dialect="postgres"),),
            False,
            "incomplete-scope",
        ),
    ],
)
def test_incomplete_or_untrusted_schema_scope_is_integration_required(
    schema_sources: tuple[JavaSpringSource, ...],
    scope_complete: bool,
    expected_reason: str,
) -> None:
    sources = (
        _source("pom.xml", _maven_build()),
        _source(
            "src/example/Owner.java",
            'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
            '@Entity @Table(name="owners") class Owner {}',
        ),
        _source(
            "src/example/OwnerRepository.java",
            'package example; import org.springframework.data.jpa.repository.JpaRepository; '
            'interface OwnerRepository extends JpaRepository<Owner,Integer> {}',
        ),
        _source(
            "src/example/OwnerService.java",
            'package example; class OwnerService { private final OwnerRepository owners; '
            'OwnerService(OwnerRepository owners){this.owners=owners;} '
            'Owner load(){return owners.findById(1).orElseThrow();}}',
        ),
        *schema_sources,
    )
    evidence = _compile_java_spring(sources, scope_complete=scope_complete)

    assert evidence.status == "INTEGRATION_REQUIRED"
    assert expected_reason in evidence.status_reasons
    if expected_reason != "incomplete-scope":
        assert evidence.edges == ()


def test_unknown_operation_and_zero_supported_java_are_not_successful_empty_evidence() -> None:
    zero = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "db/postgres/schema.sql",
                "create table owners (id int);",
                dialect="postgres",
            ),
        )
    )
    assert zero.status == "INTEGRATION_REQUIRED"
    assert "zero-supported-java-files" in zero.status_reasons

    unknown = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
                '@Entity @Table(name="owners") class Owner {}',
            ),
            _source(
                "src/example/OwnerRepository.java",
                'package example; import org.springframework.data.jpa.repository.JpaRepository; '
                'interface OwnerRepository extends JpaRepository<Owner,Integer> {}',
            ),
            _source(
                "src/example/OwnerService.java",
                'package example; class OwnerService { private final OwnerRepository owners; '
                'OwnerService(OwnerRepository owners){this.owners=owners;} '
                'Object run(){return owners.searchEverything();}}',
            ),
            _source(
                "db/postgres/schema.sql",
                "create table owners (id int);",
                dialect="postgres",
            ),
        )
    )
    assert unknown.edges == ()
    assert unknown.status == "INTEGRATION_REQUIRED"
    assert unknown.coverage.invocations_unresolved == 1
    assert "unsupported-operation" in {item.code for item in unknown.residue}


def test_unbound_repository_call_is_integration_required_not_successful_empty() -> None:
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
                '@Entity @Table(name="owners") class Owner {}',
            ),
            _source(
                "src/example/OwnerRepository.java",
                'package example; import org.springframework.data.jpa.repository.JpaRepository; '
                'interface OwnerRepository extends JpaRepository<Owner,Integer> {}',
            ),
            _source(
                "src/example/OwnerService.java",
                'package example; class OwnerService { private OwnerRepository owners; '
                'Owner load(){return owners.findById(1).orElseThrow();}}',
            ),
            _source(
                "db/postgres/schema.sql",
                "create table owners (id int);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.edges == ()
    assert evidence.status == "INTEGRATION_REQUIRED"
    assert "unbound-repository-receiver" in evidence.status_reasons


def test_undeclared_custom_derived_method_is_not_assumed_from_its_prefix() -> None:
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
                '@Entity @Table(name="owners") class Owner {}',
            ),
            _source(
                "src/example/OwnerRepository.java",
                'package example; import org.springframework.data.jpa.repository.JpaRepository; '
                'interface OwnerRepository extends JpaRepository<Owner,Integer> {}',
            ),
            _source(
                "src/example/OwnerService.java",
                'package example; class OwnerService { private final OwnerRepository owners; '
                'OwnerService(OwnerRepository owners){this.owners=owners;} '
                'Object run(){return owners.findAnythingAtAll();}}',
            ),
            _source(
                "db/postgres/schema.sql",
                "create table owners (id int);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.edges == ()
    assert evidence.status == "INTEGRATION_REQUIRED"
    assert "unsupported-operation" in {item.code for item in evidence.residue}


def test_edge_identities_are_bounded_before_serialization() -> None:
    method = "find" + "A" * 300
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
                '@Entity @Table(name="owners") class Owner {}',
            ),
            _source(
                "src/example/OwnerRepository.java",
                "package example; import org.springframework.data.jpa.repository."
                "JpaRepository; interface OwnerRepository extends "
                f"JpaRepository<Owner,Integer> {{ Object {method}(); }}",
            ),
            _source(
                "src/example/OwnerService.java",
                'package example; class OwnerService { private final OwnerRepository owners; '
                'OwnerService(OwnerRepository owners){this.owners=owners;} '
                f'Object run(){{return owners.{method}();}}}}',
            ),
            _source(
                "db/postgres/schema.sql",
                "create table owners (id int);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.edges == ()
    assert evidence.status == "INTEGRATION_REQUIRED"
    assert "unsupported-edge-identity" in {item.code for item in evidence.residue}


def test_malformed_sql_marks_evidence_integration_required_even_with_a_proven_edge() -> None:
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
                '@Entity @Table(name="owners") class Owner {}',
            ),
            _source(
                "src/example/OwnerRepository.java",
                'package example; import org.springframework.data.jpa.repository.JpaRepository; '
                'interface OwnerRepository extends JpaRepository<Owner,Integer> {}',
            ),
            _source(
                "src/example/OwnerService.java",
                'package example; class OwnerService { private final OwnerRepository owners; '
                'OwnerService(OwnerRepository owners){this.owners=owners;} '
                'Owner run(){return owners.findById(1).orElseThrow();}}',
            ),
            _source(
                "db/postgres/schema.sql",
                "create table owners (id int); create table broken (",
                dialect="postgres",
            ),
        )
    )

    assert len(evidence.edges) == 1
    assert evidence.status == "INTEGRATION_REQUIRED"
    assert "malformed-sql" in evidence.status_reasons


def test_query_on_different_overload_never_overrides_inherited_operation() -> None:
    evidence = _compile_java_spring(
        _lineage_sources(
            entity='''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="owners") class Owner {}
''',
            repository='''package example;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
interface OwnerRepository extends JpaRepository<Owner, Integer> {
  @Query("DELETE FROM Owner o WHERE o.id = ?1")
  Object findById(Integer id, boolean hardDelete);
}
''',
            service='''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Owner load(Integer id) { return owners.findById(id).orElseThrow(); }
}
''',
        )
    )

    assert evidence.status == "COMPLETE"
    assert len(evidence.edges) == 1
    assert evidence.edges[0].edge_type == "READS"
    assert evidence.edges[0].transform == "OwnerRepository.findById -> owners"
    assert evidence.edges[0].evidence.query is None
    assert evidence.edges[0].evidence.invocation.attribute("arity") == "1"


def test_same_name_and_arity_repository_overloads_are_ambiguous() -> None:
    evidence = _compile_java_spring(
        _lineage_sources(
            entity='''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="owners") class Owner {}
''',
            repository='''package example;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
interface OwnerRepository extends JpaRepository<Owner, Integer> {
  @Query("SELECT o FROM Owner o") Object findCustom(Integer id);
  Object findCustom(String name);
}
''',
            service='''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Object load(Integer id) { return owners.findCustom(id); }
}
''',
        )
    )

    assert evidence.edges == ()
    assert evidence.status == "INTEGRATION_REQUIRED"
    assert "ambiguous-operation-overload" in {
        item.code for item in evidence.residue
    }


def test_same_arity_custom_overload_cannot_steal_inherited_operation() -> None:
    evidence = _compile_java_spring(
        _lineage_sources(
            entity='''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="owners") class Owner {}
''',
            repository='''package example;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
interface OwnerRepository extends JpaRepository<Owner, Integer> {
  @Query("DELETE FROM Owner o WHERE o.name = ?1")
  Object findById(String name);
}
''',
            service='''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Owner load(Integer id) { return owners.findById(id).orElseThrow(); }
}
''',
        )
    )

    assert evidence.edges == ()
    assert evidence.status == "INTEGRATION_REQUIRED"
    assert "ambiguous-operation-overload" in {
        item.code for item in evidence.residue
    }


@pytest.mark.parametrize(
    ("modifier", "body"),
    [
        ("default", "{ return null; }"),
        ("static", "{ return null; }"),
        ("private", "{ return null; }"),
    ],
)
def test_nonabstract_repository_method_never_becomes_derived_lineage(
    modifier: str, body: str
) -> None:
    evidence = _compile_java_spring(
        _lineage_sources(
            entity='''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="owners") class Owner {}
''',
            repository=f'''package example;
import org.springframework.data.jpa.repository.JpaRepository;
interface OwnerRepository extends JpaRepository<Owner, Integer> {{
  {modifier} Owner findCached() {body}
}}
''',
            service='''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Owner load() { return owners.findCached(); }
}
''',
        )
    )

    assert evidence.edges == ()
    assert evidence.status == "INTEGRATION_REQUIRED"
    assert "unsupported-operation" in {item.code for item in evidence.residue}


@pytest.mark.parametrize(
    ("table_annotation", "schema", "expected_dataset"),
    [
        (
            '@Table(name="owners", schema="audit")',
            "create table audit.owners (id integer primary key);",
            "audit.owners",
        ),
        (
            '@Table(name="\\\"Owners\\\"")',
            'create table "Owners" (id integer primary key);',
            '"Owners"',
        ),
        (
            '@Table(name="owners", schema="audit", catalog="warehouse")',
            "create table warehouse.audit.owners (id integer primary key);",
            "warehouse.audit.owners",
        ),
    ],
)
def test_complete_qualified_table_identity_drives_dataset_urn(
    table_annotation: str, schema: str, expected_dataset: str
) -> None:
    evidence = _compile_java_spring(
        _lineage_sources(
            entity=f'''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity {table_annotation} class Owner {{}}
''',
            repository='''package example;
import org.springframework.data.jpa.repository.JpaRepository;
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
''',
            service='''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Owner load(Integer id) { return owners.findById(id).orElseThrow(); }
}
''',
            schema=schema,
        )
    )

    assert evidence.status == "COMPLETE"
    assert len(evidence.edges) == 1
    assert evidence.edges[0].dataset_urn.endswith(f":{expected_dataset}")
    assert f"-> {expected_dataset}" in evidence.edges[0].transform


def test_qualified_or_quoted_table_mismatch_never_emits_edge() -> None:
    for table_annotation, schema in (
        (
            '@Table(name="owners")',
            "create table public.owners (id integer primary key);",
        ),
        (
            '@Table(name="owners", schema="audit")',
            "create table public.owners (id integer primary key);",
        ),
        (
            '@Table(name="\\\"Owners\\\"")',
            "create table owners (id integer primary key);",
        ),
    ):
        evidence = _compile_java_spring(
            _lineage_sources(
                entity=f'''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity {table_annotation} class Owner {{}}
''',
                repository='''package example;
import org.springframework.data.jpa.repository.JpaRepository;
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
''',
                service='''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Owner load(Integer id) { return owners.findById(id).orElseThrow(); }
}
''',
                schema=schema,
            )
        )

        assert evidence.edges == ()
        assert evidence.status == "INTEGRATION_REQUIRED"
        assert "missing-schema-table" in evidence.status_reasons


def test_an_anonymous_postgres_create_index_is_ignored_inventory() -> None:
    """spring-petclinic-rest's postgres schema uses PostgreSQL's optional-name form
    `CREATE INDEX ON vets (last_name)`. An anonymous index is structurally valid
    DDL -- the server names it -- so it is inventory-only, never malformed SQL.
    """
    sources = _lineage_sources(
        entity='''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="owners") class Owner {}
''',
        repository='''package example;
import org.springframework.data.jpa.repository.JpaRepository;
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
''',
        service='''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Owner load(Integer id) { return owners.findById(id).orElseThrow(); }
}
''',
        schema=(
            "create table owners (id integer primary key); "
            "create index on owners(id);"
        ),
    )

    evidence = _compile_java_spring(sources)

    assert evidence.status == "COMPLETE"
    assert "malformed-sql" not in {item.code for item in evidence.residue}
    assert "ignored-schema-statement" in {item.code for item in evidence.residue}


def test_analysis_residue_is_blocking_except_explicit_ignored_schema_inventory() -> None:
    sources = _lineage_sources(
        entity='''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="owners") class Owner {}
''',
        repository='''package example;
import org.springframework.data.jpa.repository.JpaRepository;
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
''',
        service='''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Owner load(Integer id) { return owners.findById(id).orElseThrow(); }
}
''',
        schema=(
            "create table owners (id integer primary key); "
            "create index idx_owners_id on owners(id);"
        ),
    )
    complete = _compile_java_spring(sources)
    assert complete.status == "COMPLETE"
    assert "ignored-schema-statement" in {item.code for item in complete.residue}

    blocked = _compile_java_spring(
        sources
        + (
            _source(
                "src/example/Fake.java",
                "package example; import fake.Entity; @Entity class Fake {}",
            ),
        )
    )
    assert len(blocked.edges) == 1
    assert blocked.status == "INTEGRATION_REQUIRED"
    assert "unresolved-framework-symbol" in blocked.status_reasons

    malformed_index = _compile_java_spring(
        tuple(
            replace(source, content=b"create table owners (id integer); create index;")
            if source.path.endswith("schema.sql")
            else source
            for source in sources
        )
    )
    assert len(malformed_index.edges) == 1
    assert malformed_index.status == "INTEGRATION_REQUIRED"
    assert "malformed-sql" in malformed_index.status_reasons


@pytest.mark.parametrize(
    "index_statement",
    [
        "CREATE INDEX IF NOT EXISTS idx_vets_last_name ON vets (last_name)",
        "CREATE INDEX IF NOT EXISTS idx_specialties_name ON specialties (name)",
        "CREATE INDEX IF NOT EXISTS idx_types_name ON types (name)",
        "CREATE INDEX IF NOT EXISTS idx_owners_last_name ON owners (last_name)",
        "CREATE INDEX IF NOT EXISTS idx_pets_name ON pets (name)",
        "CREATE INDEX IF NOT EXISTS idx_pets_owner_id ON pets (owner_id)",
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS unique_owner_pet_name "
            "ON pets (owner_id, LOWER(name))"
        ),
        "CREATE INDEX IF NOT EXISTS idx_visits_pet_id ON visits (pet_id)",
    ],
)
def test_exact_petclinic_index_shapes_are_nonblocking_inventory(
    index_statement: str,
) -> None:
    evidence = _compile_java_spring(
        _lineage_sources(
            entity='''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="owners") class Owner {}
''',
            repository='''package example;
import org.springframework.data.jpa.repository.JpaRepository;
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
''',
            service='''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Owner load(Integer id) { return owners.findById(id).orElseThrow(); }
}
''',
            schema=(
                "create table owners (id integer primary key); "
                f"{index_statement};"
            ),
        )
    )

    assert evidence.status == "COMPLETE"
    assert len(evidence.edges) == 1
    assert {item.code for item in evidence.residue} == {
        "ignored-schema-statement"
    }


def test_create_index_with_dropped_malformed_tail_is_blocking() -> None:
    sources = _lineage_sources(
        entity='''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="owners") class Owner {}
''',
        repository='''package example;
import org.springframework.data.jpa.repository.JpaRepository;
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
''',
        service='''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Owner load(Integer id) { return owners.findById(id).orElseThrow(); }
}
''',
        schema=(
            "create table owners (id integer primary key); "
            "create index idx on owners(id) include ();"
        ),
    )

    evidence = _compile_java_spring(sources)

    assert len(evidence.edges) == 1
    assert evidence.status == "INTEGRATION_REQUIRED"
    assert "malformed-sql" in evidence.status_reasons


@pytest.mark.parametrize(
    ("query_target", "expected_edges"),
    [("Person", 1), ("Owner", 0)],
)
def test_jpql_target_uses_literal_entity_name(
    query_target: str, expected_edges: int
) -> None:
    evidence = _compile_java_spring(
        _lineage_sources(
            entity='''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity(name="Person") @Table(name="owners") class Owner {}
''',
            repository=f'''package example;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
interface OwnerRepository extends JpaRepository<Owner, Integer> {{
  @Query("SELECT p FROM {query_target} p") Object findPerson();
}}
''',
            service='''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Object load() { return owners.findPerson(); }
}
''',
        )
    )

    assert len(evidence.edges) == expected_edges
    mapping = next(
        fact
        for fact in JavaSpringScaAnalyzer().analyze(
            _lineage_sources(
                entity='''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity(name="Person") @Table(name="owners") class Owner {}
''',
                repository="package example; interface Empty {}",
                service="package example; class EmptyService {}",
            )
        ).facts
        if fact.kind == "spring.entity-table"
    )
    assert mapping.attribute("entityName") == "Person"
    if expected_edges:
        assert evidence.status == "COMPLETE"
    else:
        assert evidence.status == "INTEGRATION_REQUIRED"
        assert "query-entity-conflict" in {item.code for item in evidence.residue}


def test_dynamic_entity_name_never_emits_mapping_or_lineage() -> None:
    sources = _lineage_sources(
        entity='''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity(name=ENTITY_NAME) @Table(name="owners")
class Owner { static final String ENTITY_NAME = "Person"; }
''',
        repository='''package example;
import org.springframework.data.jpa.repository.JpaRepository;
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
''',
        service='''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Owner load(Integer id) { return owners.findById(id).orElseThrow(); }
}
''',
    )
    analysis = JavaSpringScaAnalyzer().analyze(sources)
    evidence = spring_sca.JavaSpringEvidenceCompiler().compile(
        analysis,
        spring_sca.JavaSpringEvidenceContext(
            origin="https://github.com/example/spring-service",
            repository="spring-service",
            revision="1" * 40,
            scope_digest="sha256:" + "2" * 64,
            environment="staging",
            platform="postgres",
            system="petclinic",
            analyzer_pack="java-spring-data-jpa-v1",
            ruleset_version="spring-data-rules-v1",
            resolver_version="schema-resolver-v1",
            schema_profile="postgres",
        ),
    )

    assert not any(fact.kind == "spring.entity-table" for fact in analysis.facts)
    assert "dynamic-entity-name" in {item.code for item in analysis.residue}
    assert evidence.edges == ()
    assert evidence.status == "INTEGRATION_REQUIRED"


def test_constructor_injection_of_an_unresolvable_collaborator_does_not_crash() -> None:
    """A constructor may inject a collaborator whose type the resolver cannot pin.

    Reproduces a crash found by running the analyzer over spring-petclinic-rest, whose
    ClinicServiceImpl injects `PetRepository` -- a plain interface reached through a
    wildcard import. The field is not a tracked repository and the parameter type does
    not resolve, so both lookups returned None, compared equal, and the binding then
    indexed a field that was never present.
    """
    service = """package example;
class OwnerService {
  private final OwnerRepository owners;
  private final PetRepository petRepository;
  OwnerService(OwnerRepository owners, PetRepository petRepository) {
    this.owners = owners;
    this.petRepository = petRepository;
  }
  Owner read(Integer id) { return owners.findById(id).orElseThrow(); }
  Owner write(Owner owner) { return owners.save(owner); }
}"""
    sources = _lineage_sources(
        entity="""package example;
import jakarta.persistence.Entity; import jakarta.persistence.Table;
@Entity @Table(name="owners") class Owner {}""",
        repository="""package example;
import org.springframework.data.jpa.repository.JpaRepository;
interface OwnerRepository extends JpaRepository<Owner,Integer> {}""",
        service=service,
    )

    analysis = JavaSpringScaAnalyzer().analyze(sources)

    # The tracked repository is still bound; the unresolvable collaborator is ignored
    # rather than crashing the analyzer.
    bindings = _facts(analysis, "spring.repository-binding")
    assert any(fact.subject.endswith("#owners") for fact in bindings), bindings
    assert not any(fact.subject.endswith("#petRepository") for fact in bindings)


ABSTRACTION_ENTITY = """package example;
import jakarta.persistence.Entity; import jakarta.persistence.Table;
@Entity @Table(name="pets") class Pet {}"""

ABSTRACTION_INTERFACE = """package example;
public interface PetRepository {
    Pet findById(Integer id);
    Pet save(Pet pet);
}"""

ABSTRACTION_SERVICE = """package example;
class PetService {
  private final PetRepository pets;
  PetService(PetRepository pets) { this.pets = pets; }
  Pet read(Integer id) { return pets.findById(id); }
  Pet write(Pet pet) { return pets.save(pet); }
}"""


def _abstraction_sources(specializations: str) -> tuple[JavaSpringSource, ...]:
    return (
        _source("pom.xml", _maven_build()),
        _source("src/example/Pet.java", ABSTRACTION_ENTITY),
        _source("src/example/PetRepository.java", ABSTRACTION_INTERFACE),
        _source("src/example/PetService.java", ABSTRACTION_SERVICE),
        _source("src/example/Specializations.java", specializations),
        _source(
            "src/main/resources/db/postgres/schema.sql",
            "create table pets (id integer primary key);",
            dialect="postgres",
        ),
    )


def test_a_repository_abstraction_is_followed_to_its_specialization() -> None:
    """spring-petclinic-rest injects a plain interface, not the Spring Data type.

    `SpringDataPetRepository extends PetRepository, Repository<Pet, Integer>` means the
    injected `PetRepository` provably resolves to entity `Pet`, so a call through the
    abstraction is real lineage rather than an unbound receiver.
    """
    specializations = """package example;
import org.springframework.data.repository.Repository;
public interface SpringDataPetRepository extends PetRepository, Repository<Pet, Integer> {}"""

    result = JavaSpringScaAnalyzer().analyze(_abstraction_sources(specializations))

    associations = {
        fact.subject: fact.attribute("entityType")
        for fact in _facts(result, "spring.repository-association")
    }
    assert associations.get("example.SpringDataPetRepository") == "Pet"
    # The abstraction itself now carries the association, resolved through its
    # specialization rather than declared directly.
    assert associations.get("example.PetRepository") == "Pet"

    bindings = [
        fact.attribute("field") for fact in _facts(result, "spring.repository-binding")
    ]
    assert bindings == ["pets"]

    invocations = [
        (fact.attribute("receiver"), fact.attribute("method"))
        for fact in _facts(result, "java.invocation")
    ]
    assert ("pets", "findById") in invocations
    assert ("pets", "save") in invocations


def test_an_abstraction_with_two_entities_is_quarantined_not_guessed() -> None:
    specializations = """package example;
import org.springframework.data.repository.Repository;
interface Other {}
public interface SpringDataPetRepository extends PetRepository, Repository<Pet, Integer> {}
interface SecondPetRepository extends PetRepository, Repository<Other, Integer> {}"""

    result = JavaSpringScaAnalyzer().analyze(_abstraction_sources(specializations))

    associations = {
        fact.subject for fact in _facts(result, "spring.repository-association")
    }
    assert "example.PetRepository" not in associations
    assert "ambiguous-repository-abstraction" in {item.code for item in result.residue}
    assert _facts(result, "spring.repository-binding") == ()


def test_an_unspecialized_interface_never_becomes_a_repository() -> None:
    specializations = "package example;\ninterface Unrelated {}"

    result = JavaSpringScaAnalyzer().analyze(_abstraction_sources(specializations))

    associations = {
        fact.subject for fact in _facts(result, "spring.repository-association")
    }
    assert associations == set()
    assert _facts(result, "spring.repository-binding") == ()


def test_a_local_type_resolves_through_a_wildcard_import() -> None:
    """A service that reaches its repository through `import ...repository.*;` is normal.

    The tracked scope is closed, so exactly one local declaration of the simple name is
    provable. This is what lets a real Spring service bind its injected repository.
    """
    service = """package example.service;
import example.repo.*;
import example.model.*;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Owner read(Integer id) { return owners.findById(id).orElseThrow(); }
}"""
    sources = (
        _source("pom.xml", _maven_build()),
        _source(
            "src/example/model/Owner.java",
            "package example.model;\nimport jakarta.persistence.Entity;\n"
            'import jakarta.persistence.Table;\n@Entity @Table(name="owners") public class Owner {}',
        ),
        _source(
            "src/example/repo/OwnerRepository.java",
            "package example.repo;\nimport org.springframework.data.jpa.repository.JpaRepository;\n"
            "import example.model.Owner;\n"
            "public interface OwnerRepository extends JpaRepository<Owner,Integer> {}",
        ),
        _source("src/example/service/OwnerService.java", service),
        _source(
            "src/main/resources/db/postgres/schema.sql",
            "create table owners (id integer primary key);",
            dialect="postgres",
        ),
    )

    result = JavaSpringScaAnalyzer().analyze(sources)

    assert [f.attribute("field") for f in _facts(result, "spring.repository-binding")] == [
        "owners"
    ]


def test_a_wildcard_binds_a_framework_name_only_from_one_approved_package() -> None:
    """Narrowed from "never": an on-demand import is provable inside the closed set.

    The rule was previously that only an exact import may bind a framework-sensitive
    name, because another wildcard-imported package might supply `Entity` from a jar
    this analyzer cannot see. That is now narrowed rather than dropped: a wildcard binds
    only when exactly one *approved* framework package is wildcard-imported here, it is
    the only approved package declaring that simple name, and no local type shadows it.
    Real Spring code writes `import jakarta.persistence.*;`, and refusing it meant the
    upstream Petclinic microservices produced no lineage at all.

    The residual risk is deliberate and bounded: the developer must have named the
    approved package in the import for it to bind.
    """
    java = (
        "package example;\nimport jakarta.persistence.*;\n"
        '@Entity @Table(name="owners") class Owner {}'
    )

    result = JavaSpringScaAnalyzer().analyze(
        (_source("pom.xml", _maven_build()), _source("src/example/Owner.java", java))
    )

    assert [f.attribute("table") for f in _facts(result, "spring.entity-table")] == [
        "owners"
    ]
    assert "wildcard-framework-symbol" not in {entry.code for entry in result.residue}


def test_a_wildcard_from_an_unapproved_package_still_proves_nothing() -> None:
    java = (
        "package example;\nimport com.vendor.orm.*;\n"
        '@Entity @Table(name="owners") class Owner {}'
    )

    result = JavaSpringScaAnalyzer().analyze(
        (_source("pom.xml", _maven_build()), _source("src/example/Owner.java", java))
    )

    assert _facts(result, "spring.entity-table") == ()
    assert "wildcard-framework-symbol" in {entry.code for entry in result.residue}


def test_two_wildcard_imports_declaring_the_same_local_name_are_quarantined() -> None:
    service = """package example.service;
import example.a.*;
import example.b.*;
class Service {
  private final Thing thing;
  Service(Thing thing) { this.thing = thing; }
}"""
    sources = (
        _source("pom.xml", _maven_build()),
        _source("src/example/a/Thing.java", "package example.a;\npublic interface Thing {}"),
        _source("src/example/b/Thing.java", "package example.b;\npublic interface Thing {}"),
        _source("src/example/service/Service.java", service),
    )

    result = JavaSpringScaAnalyzer().analyze(sources)

    assert _facts(result, "spring.repository-binding") == ()


# --- element level: schema columns and entity field mappings ---------------------------

ELEMENT_SCHEMA = """create table owners (
  id integer primary key,
  last_name varchar(255) not null,
  telephone varchar(20)
);"""

ELEMENT_ENTITY = """package example;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="owners")
class Owner {
  private Integer id;
  @Column(name = "last_name") private String lastName;
  private String telephone;
  private String notPersisted;
}"""


def _element_sources() -> tuple[JavaSpringSource, ...]:
    return (
        _source("pom.xml", _maven_build()),
        _source("src/example/Owner.java", ELEMENT_ENTITY),
        _source(
            "src/example/OwnerRepository.java",
            "package example;\nimport org.springframework.data.jpa.repository.JpaRepository;\n"
            "interface OwnerRepository extends JpaRepository<Owner,Integer> {}",
        ),
        _source("src/main/resources/db/postgres/schema.sql", ELEMENT_SCHEMA, dialect="postgres"),
    )


def test_schema_columns_are_emitted_as_elements() -> None:
    result = JavaSpringScaAnalyzer().analyze(_element_sources())

    columns = {
        (fact.attribute("table"), fact.attribute("column"))
        for fact in _facts(result, "sql.column")
    }
    assert columns == {
        ("owners", "id"),
        ("owners", "last_name"),
        ("owners", "telephone"),
    }
    primary = {
        fact.attribute("column")
        for fact in _facts(result, "sql.column")
        if fact.attribute("primaryKey") == "true"
    }
    assert primary == {"id"}


def test_entity_fields_map_to_columns_explicitly_or_by_convention() -> None:
    result = JavaSpringScaAnalyzer().analyze(_element_sources())

    mappings = {
        (fact.attribute("field"), fact.attribute("column"), fact.attribute("mapping"))
        for fact in _facts(result, "spring.entity-field")
    }
    assert ("lastName", "last_name", "explicit") in mappings
    assert ("telephone", "telephone", "convention") in mappings
    assert ("id", "id", "convention") in mappings
    # A field with no matching column is not invented as an element.
    assert not any(field == "notPersisted" for field, _c, _m in mappings)


def test_an_explicit_column_absent_from_the_schema_is_quarantined() -> None:
    entity = ELEMENT_ENTITY.replace('name = "last_name"', 'name = "surname"')
    sources = tuple(
        _source("src/example/Owner.java", entity)
        if item.path == "src/example/Owner.java"
        else item
        for item in _element_sources()
    )

    result = JavaSpringScaAnalyzer().analyze(sources)

    assert "unmapped-entity-column" in {item.code for item in result.residue}
    assert not any(
        fact.attribute("field") == "lastName"
        for fact in _facts(result, "spring.entity-field")
    )


# --- L3: which columns a repository method actually touches -----------------------------

L3_SCHEMA = """create table owners (
  id integer primary key,
  first_name varchar(255),
  last_name varchar(255),
  city varchar(255)
);"""

L3_ENTITY = """package example;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="owners")
class Owner {
  private Integer id;
  private String firstName;
  @Column(name="last_name") private String lastName;
  private String city;
}"""

L3_REPOSITORY = """package example;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
interface OwnerRepository extends JpaRepository<Owner,Integer> {
    List<Owner> findByLastName(String lastName);
    List<Owner> findByLastNameAndCity(String lastName, String city);
    List<Owner> findByFirstNameStartingWith(String prefix);
    long countByCity(String city);
    @Query("SELECT o FROM Owner o WHERE o.city = ?1")
    List<Owner> search(String city);
    List<Owner> findByUnknownProperty(String value);
}"""


def _l3_sources() -> tuple[JavaSpringSource, ...]:
    return (
        _source("pom.xml", _maven_build()),
        _source("src/example/Owner.java", L3_ENTITY),
        _source("src/example/OwnerRepository.java", L3_REPOSITORY),
        _source("src/main/resources/db/postgres/schema.sql", L3_SCHEMA, dialect="postgres"),
    )


def _elements(result) -> set[tuple[str, str, str]]:
    return {
        (
            fact.attribute("method"),
            fact.attribute("column"),
            fact.attribute("derivation"),
        )
        for fact in _facts(result, "spring.query-element")
    }


def test_derived_query_methods_resolve_to_the_columns_they_filter_on() -> None:
    elements = _elements(JavaSpringScaAnalyzer().analyze(_l3_sources()))

    assert ("findByLastName", "last_name", "derived") in elements
    # And / multiple predicates both land.
    assert ("findByLastNameAndCity", "last_name", "derived") in elements
    assert ("findByLastNameAndCity", "city", "derived") in elements
    # A trailing keyword is stripped from the property.
    assert ("findByFirstNameStartingWith", "first_name", "derived") in elements
    # count* is still a read predicate.
    assert ("countByCity", "city", "derived") in elements


def test_jpql_bodies_resolve_to_the_columns_they_reference() -> None:
    elements = _elements(JavaSpringScaAnalyzer().analyze(_l3_sources()))

    assert ("search", "city", "jpql") in elements


def test_an_unknown_property_never_invents_a_column() -> None:
    result = JavaSpringScaAnalyzer().analyze(_l3_sources())
    elements = _elements(result)

    assert not any(method == "findByUnknownProperty" for method, _c, _d in elements)
    assert "unresolved-query-property" in {item.code for item in result.residue}


def test_query_elements_carry_the_table_so_an_element_urn_can_be_built() -> None:
    facts = _facts(JavaSpringScaAnalyzer().analyze(_l3_sources()), "spring.query-element")

    assert facts
    for fact in facts:
        assert fact.attribute("table") == "owners"
        assert fact.attribute("role") in {"predicate", "projection"}


def test_fields_inherited_from_a_mapped_superclass_become_elements() -> None:
    """Petclinic keeps first_name / last_name on a @MappedSuperclass, not the entity.

    Without following the extends chain the headline query `findByLastNameStartingWith`
    resolves to nothing at all.
    """
    person = """package example;
import jakarta.persistence.Column;
import jakarta.persistence.MappedSuperclass;
@MappedSuperclass
class Person {
  @Column(name="last_name") private String lastName;
  private String firstName;
}"""
    owner = """package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="owners")
class Owner extends Person {
  private String city;
}"""
    repository = """package example;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;
interface OwnerRepository extends JpaRepository<Owner,Integer> {
    List<Owner> findByLastNameStartingWith(String prefix);
}"""
    sources = (
        _source("pom.xml", _maven_build()),
        _source("src/example/Person.java", person),
        _source("src/example/Owner.java", owner),
        _source("src/example/OwnerRepository.java", repository),
        _source("src/main/resources/db/postgres/schema.sql", L3_SCHEMA, dialect="postgres"),
    )

    result = JavaSpringScaAnalyzer().analyze(sources)

    mapped = {
        (fact.attribute("field"), fact.attribute("column"))
        for fact in _facts(result, "spring.entity-field")
        if fact.attribute("entity").endswith(".Owner")
    }
    assert ("lastName", "last_name") in mapped
    assert ("firstName", "first_name") in mapped
    assert ("city", "city") in mapped

    elements = _elements(result)
    assert ("findByLastNameStartingWith", "last_name", "derived") in elements


def test_derived_property_resolves_through_a_many_to_one_join_column() -> None:
    """spring-petclinic-rest's `VisitRepository.findByPetId` derives against
    `Visit.pet`, a `@ManyToOne` with an explicit `@JoinColumn(name = "pet_id")`. The
    derived property `petId` is association traversal (`pet` + the association's key)
    and resolves to the declared join column, not a made-up `pet_id` field.
    """
    entity_pet = '''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="pets") class Pet {}
'''
    entity_visit = '''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
@Entity @Table(name="visits")
class Visit {
  @ManyToOne
  @JoinColumn(name = "pet_id")
  private Pet pet;
}
'''
    repository = '''package example;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;
interface VisitRepository extends JpaRepository<Visit, Integer> {
  List<Visit> findByPetId(Integer petId);
}
'''
    schema = "create table visits (id integer primary key, pet_id integer not null);"
    sources = (
        _source("pom.xml", _maven_build()),
        _source("src/example/Pet.java", entity_pet),
        _source("src/example/Visit.java", entity_visit),
        _source("src/example/VisitRepository.java", repository),
        _source("src/main/resources/db/postgres/schema.sql", schema, dialect="postgres"),
    )

    result = JavaSpringScaAnalyzer().analyze(sources)

    elements = _elements(result)
    assert ("findByPetId", "pet_id", "derived") in elements
    assert "unresolved-query-property" not in {item.code for item in result.residue}


def test_derived_property_without_a_literal_join_column_stays_residue() -> None:
    """The association+Id resolution only fires on a proven, literal @JoinColumn --
    an association with no explicit join column keeps its residue rather than
    guessing a `<field>_id` convention that JPA does not itself guarantee.
    """
    entity_pet = '''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="pets") class Pet {}
'''
    entity_visit = '''package example;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
@Entity @Table(name="visits")
class Visit {
  @Column(name = "description") private String description;
  @ManyToOne
  private Pet pet;
}
'''
    repository = '''package example;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;
interface VisitRepository extends JpaRepository<Visit, Integer> {
  List<Visit> findByPetId(Integer petId);
}
'''
    schema = (
        "create table visits (id integer primary key, pet_id integer not null, "
        "description varchar(255));"
    )
    sources = (
        _source("pom.xml", _maven_build()),
        _source("src/example/Pet.java", entity_pet),
        _source("src/example/Visit.java", entity_visit),
        _source("src/example/VisitRepository.java", repository),
        _source("src/main/resources/db/postgres/schema.sql", schema, dialect="postgres"),
    )

    result = JavaSpringScaAnalyzer().analyze(sources)

    elements = _elements(result)
    assert not any(method == "findByPetId" for method, _c, _d in elements)
    assert "unresolved-query-property" in {item.code for item in result.residue}


def test_jpql_join_fetch_of_a_declared_association_is_not_residue() -> None:
    """spring-petclinic-rest's SpringDataOwnerRepository queries `left join fetch
    owner.pets`, a declared `@OneToMany`. That is association traversal, not a
    column projection, so it must not be flagged as an unresolved query property.
    """
    entity_pet = '''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="pets") class Pet {}
'''
    entity_owner = '''package example;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.OneToMany;
import jakarta.persistence.Table;
import java.util.Set;
@Entity @Table(name="owners")
class Owner {
  @Column(name="last_name") private String lastName;
  @OneToMany(mappedBy = "owner")
  private Set<Pet> pets;
}
'''
    repository = '''package example;
import java.util.Collection;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
interface OwnerRepository extends JpaRepository<Owner, Integer> {
  @Query("SELECT DISTINCT owner FROM Owner owner left join fetch owner.pets WHERE owner.lastName LIKE :lastName%")
  Collection<Owner> findByLastName(String lastName);
}
'''
    schema = "create table owners (id integer primary key, last_name varchar(255));"
    sources = (
        _source("pom.xml", _maven_build()),
        _source("src/example/Pet.java", entity_pet),
        _source("src/example/Owner.java", entity_owner),
        _source("src/example/OwnerRepository.java", repository),
        _source("src/main/resources/db/postgres/schema.sql", schema, dialect="postgres"),
    )

    result = JavaSpringScaAnalyzer().analyze(sources)

    elements = _elements(result)
    assert ("findByLastName", "last_name", "jpql") in elements
    assert not any(fact.attribute("column") == "pets" for fact in _facts(result, "spring.query-element"))
    assert "unresolved-query-property" not in {item.code for item in result.residue}


def test_a_to_many_join_column_is_not_an_own_table_contradiction() -> None:
    """spring-petclinic's `Owner.pets` is a unidirectional `@OneToMany` whose
    `@JoinColumn(name = "owner_id")` names the foreign key on the TARGET table
    (`pets`), never a column of the owning entity's own table (`owners`). A literal
    to-many join column must not be quarantined as an unmapped entity column, and
    must not claim `owner_id` as an element of `owners` either.
    """
    entity_pet = '''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="pets") class Pet {}
'''
    entity_owner = '''package example;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.OneToMany;
import jakarta.persistence.Table;
import java.util.List;
@Entity @Table(name="owners")
class Owner {
  @Column(name="last_name") private String lastName;
  @OneToMany
  @JoinColumn(name = "owner_id")
  private final List<Pet> pets = null;
}
'''
    repository = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
interface OwnerRepository extends JpaRepository<Owner, Integer> {}
'''
    schema = (
        "create table owners (id integer primary key, last_name varchar(255));\n"
        "create table pets (id integer primary key, owner_id integer not null);"
    )
    sources = (
        _source("pom.xml", _maven_build()),
        _source("src/example/Pet.java", entity_pet),
        _source("src/example/Owner.java", entity_owner),
        _source("src/example/OwnerRepository.java", repository),
        _source("src/main/resources/db/postgres/schema.sql", schema, dialect="postgres"),
    )

    result = JavaSpringScaAnalyzer().analyze(sources)

    assert "unmapped-entity-column" not in {item.code for item in result.residue}
    assert not any(
        fact.attribute("field") == "pets"
        for fact in _facts(result, "spring.entity-field")
    )


def test_a_to_one_join_column_absent_from_the_own_table_stays_quarantined() -> None:
    """The to-many forgiveness must not weaken the to-one contradiction: a
    `@ManyToOne` join column lives on the entity's own table, so a literal
    `@JoinColumn` naming a column that table does not have is still residue.
    """
    entity_pet = '''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="pets") class Pet {}
'''
    entity_visit = '''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
@Entity @Table(name="visits")
class Visit {
  @ManyToOne
  @JoinColumn(name = "pet_identifier")
  private Pet pet;
}
'''
    repository = '''package example;
import org.springframework.data.jpa.repository.JpaRepository;
interface VisitRepository extends JpaRepository<Visit, Integer> {}
'''
    schema = (
        "create table pets (id integer primary key);\n"
        "create table visits (id integer primary key, pet_id integer not null);"
    )
    sources = (
        _source("pom.xml", _maven_build()),
        _source("src/example/Pet.java", entity_pet),
        _source("src/example/Visit.java", entity_visit),
        _source("src/example/VisitRepository.java", repository),
        _source("src/main/resources/db/postgres/schema.sql", schema, dialect="postgres"),
    )

    result = JavaSpringScaAnalyzer().analyze(sources)

    assert "unmapped-entity-column" in {item.code for item in result.residue}


def test_jpql_property_matching_neither_column_nor_association_stays_residue() -> None:
    """A JPQL property that names something real (a declared association) is
    forgiven; a property that names nothing provable at all is not.
    """
    entity_owner = '''package example;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="owners")
class Owner {
  @Column(name="last_name") private String lastName;
}
'''
    repository = '''package example;
import java.util.Collection;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
interface OwnerRepository extends JpaRepository<Owner, Integer> {
  @Query("SELECT owner FROM Owner owner WHERE owner.nickname = :nickname")
  Collection<Owner> findByNickname(String nickname);
}
'''
    schema = "create table owners (id integer primary key, last_name varchar(255));"
    sources = (
        _source("pom.xml", _maven_build()),
        _source("src/example/Owner.java", entity_owner),
        _source("src/example/OwnerRepository.java", repository),
        _source("src/main/resources/db/postgres/schema.sql", schema, dialect="postgres"),
    )

    result = JavaSpringScaAnalyzer().analyze(sources)

    elements = _elements(result)
    assert not any(method == "findByNickname" for method, _c, _d in elements)
    assert "unresolved-query-property" in {item.code for item in result.residue}


# --- L4: query-element facts wired into element-scoped SCA edges ------------------------


def test_derived_predicate_method_yields_both_the_dataset_edge_and_the_element_edge() -> None:
    """A same-entity derived-query method proves a specific column, so it should ground

    an additional edge on that column, alongside the existing bare dataset edge (kept
    for existing consumers).
    """
    evidence = _compile_java_spring(
        _lineage_sources(
            entity='''package example;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="owners") class Owner {
  private Integer id;
  @Column(name="last_name") private String lastName;
}
''',
            repository='''package example;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;
interface OwnerRepository extends JpaRepository<Owner, Integer> {
  List<Owner> findByLastName(String lastName);
}
''',
            service='''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Object load() { return owners.findByLastName("Smith"); }
}
''',
            schema="create table owners (id integer primary key, last_name varchar(255));",
        )
    )

    assert evidence.status == "COMPLETE"
    assert len(evidence.edges) == 2

    bare = next(edge for edge in evidence.edges if "#" not in edge.dataset_urn)
    element = next(edge for edge in evidence.edges if "#" in edge.dataset_urn)

    assert bare.edge_type == "READS"
    assert bare.dataset_urn == "urn:ldp:staging:postgres:petclinic:owners"
    assert bare.from_urn == bare.dataset_urn
    assert bare.to_urn == bare.service_urn
    assert bare.transform == "OwnerRepository.findByLastName -> owners"

    assert element.edge_type == "READS"
    assert element.dataset_urn == "urn:ldp:staging:postgres:petclinic:owners#last_name"
    assert element.from_urn == element.dataset_urn
    assert element.to_urn == element.service_urn == bare.service_urn
    assert element.transform == "OwnerRepository.findByLastName -> owners#last_name"


def test_cross_entity_jpql_never_element_scopes_the_mismatched_table_candidate() -> None:
    """Mirrors petclinic's `PetRepository.findPetTypeById` -> `types`.

    The @Query redirects the edge to a different entity's table (`types`), and the
    alias-aware property resolution grounds `t.id` against the entity the alias
    actually binds (`PetType` -> `types.id`) -- so the redirected candidate is
    element-scoped by its OWN table's fact, and no fact for the repository's
    declared table (`owners`) ever element-scopes it.
    """
    evidence = _compile_java_spring(
        _lineage_sources(
            entity='''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="owners") class Owner {
  private Integer id;
}
''',
            repository='''package example;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
interface OwnerRepository extends JpaRepository<Owner, Integer> {
  @Query("FROM PetType t WHERE t.id = ?1")
  Object findTypeById(Integer id);
}
''',
            service='''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners = owners; }
  Object load() { return owners.findTypeById(1); }
}
''',
            schema=(
                "create table owners (id integer primary key); "
                "create table types (id integer primary key, name varchar(255));"
            ),
        )
        + (
            _source(
                "src/example/PetType.java",
                '''package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name="types") class PetType {
  private Integer id;
}
''',
            ),
        )
    )

    assert evidence.status == "COMPLETE"
    assert {edge.dataset_urn for edge in evidence.edges} == {
        "urn:ldp:staging:postgres:petclinic:types",
        "urn:ldp:staging:postgres:petclinic:types#id",
    }
    assert not any("owners#" in edge.dataset_urn for edge in evidence.edges)


def test_a_default_method_delegating_to_a_declared_query_resolves_through_it() -> None:
    """jhipster's generated repositories expose default methods that delegate 1:1 to
    @Query siblings; the call site must resolve through the delegation, not refuse."""
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
                '@Entity @Table(name="owners") class Owner {}',
            ),
            _source(
                "src/example/OwnerRepository.java",
                'package example; import java.util.List; '
                'import org.springframework.data.jpa.repository.JpaRepository; '
                'import org.springframework.data.jpa.repository.Query; '
                'interface OwnerRepository extends JpaRepository<Owner,Integer> { '
                '@Query("select owner from Owner owner") List<Owner> findAllWithToOneRelationships(); '
                'default List<Owner> findAllWithEagerRelationships() '
                '{ return this.findAllWithToOneRelationships(); } }',
            ),
            _source(
                "src/example/OwnerService.java",
                'package example; import java.util.List; class OwnerService { '
                'private final OwnerRepository owners; '
                'OwnerService(OwnerRepository owners){this.owners=owners;} '
                'List<Owner> run(){return owners.findAllWithEagerRelationships();}}',
            ),
            _source(
                "db/postgres/schema.sql",
                "create table owners (id int);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.status == "COMPLETE"
    assert len(evidence.edges) == 1
    assert evidence.edges[0].edge_type == "READS"
    assert evidence.edges[0].dataset_urn == "urn:ldp:staging:postgres:petclinic:owners"


def test_a_default_method_with_a_non_delegating_body_stays_unsupported() -> None:
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
                '@Entity @Table(name="owners") class Owner {}',
            ),
            _source(
                "src/example/OwnerRepository.java",
                'package example; import java.util.List; '
                'import org.springframework.data.jpa.repository.JpaRepository; '
                'interface OwnerRepository extends JpaRepository<Owner,Integer> { '
                'default List<Owner> findTuned() { setUp(); return List.of(); } '
                'default void setUp() {} }',
            ),
            _source(
                "src/example/OwnerService.java",
                'package example; import java.util.List; class OwnerService { '
                'private final OwnerRepository owners; '
                'OwnerService(OwnerRepository owners){this.owners=owners;} '
                'List<Owner> run(){return owners.findTuned();}}',
            ),
            _source(
                "db/postgres/schema.sql",
                "create table owners (id int);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.status == "INTEGRATION_REQUIRED"
    assert "unsupported-operation" in evidence.status_reasons


def test_flush_is_recorded_as_ignored_inventory_without_blocking() -> None:
    """`repository.flush()` moves no table data; it must be evidence, not refusal."""
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
                '@Entity @Table(name="owners") class Owner {}',
            ),
            _source(
                "src/example/OwnerRepository.java",
                'package example; import org.springframework.data.jpa.repository.JpaRepository; '
                'interface OwnerRepository extends JpaRepository<Owner,Integer> {}',
            ),
            _source(
                "src/example/OwnerService.java",
                'package example; class OwnerService { private final OwnerRepository owners; '
                'OwnerService(OwnerRepository owners){this.owners=owners;} '
                'void wipe(Owner o){owners.delete(o); owners.flush();}}',
            ),
            _source(
                "db/postgres/schema.sql",
                "create table owners (id int);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.status == "COMPLETE"
    assert len(evidence.edges) == 1
    assert evidence.edges[0].edge_type == "WRITES"
    assert "ignored-repository-operation" in {item.code for item in evidence.residue}
    assert "ignored-repository-operation" not in evidence.status_reasons
    assert evidence.coverage.invocations_unresolved == 0


def test_jpql_association_fetch_join_resolves_to_the_root_entity_table() -> None:
    """`left join fetch x.assoc` traverses an association from the root entity —
    the same bounded semantics as derived-method association traversal."""
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
                '@Entity @Table(name="owners") class Owner {}',
            ),
            _source(
                "src/example/OwnerRepository.java",
                'package example; import java.util.List; '
                'import org.springframework.data.jpa.repository.JpaRepository; '
                'import org.springframework.data.jpa.repository.Query; '
                'interface OwnerRepository extends JpaRepository<Owner,Integer> { '
                '@Query("select owner from Owner owner left join fetch owner.pets where owner.id = :id") '
                'List<Owner> findWithPets(Integer id); }',
            ),
            _source(
                "src/example/OwnerService.java",
                'package example; import java.util.List; class OwnerService { '
                'private final OwnerRepository owners; '
                'OwnerService(OwnerRepository owners){this.owners=owners;} '
                'List<Owner> run(){return owners.findWithPets(1);}}',
            ),
            _source(
                "db/postgres/schema.sql",
                "create table owners (id int);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.status == "COMPLETE"
    assert len(evidence.edges) == 1
    assert evidence.edges[0].edge_type == "READS"
    assert evidence.edges[0].dataset_urn == "urn:ldp:staging:postgres:petclinic:owners"


def test_jpql_join_to_an_independent_entity_stays_unsupported() -> None:
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
                '@Entity @Table(name="owners") class Owner {}',
            ),
            _source(
                "src/example/OwnerRepository.java",
                'package example; import java.util.List; '
                'import org.springframework.data.jpa.repository.JpaRepository; '
                'import org.springframework.data.jpa.repository.Query; '
                'interface OwnerRepository extends JpaRepository<Owner,Integer> { '
                '@Query("select owner from Owner owner join Visit visit") '
                'List<Owner> findCrossJoin(); }',
            ),
            _source(
                "src/example/OwnerService.java",
                'package example; import java.util.List; class OwnerService { '
                'private final OwnerRepository owners; '
                'OwnerService(OwnerRepository owners){this.owners=owners;} '
                'List<Owner> run(){return owners.findCrossJoin();}}',
            ),
            _source(
                "db/postgres/schema.sql",
                "create table owners (id int);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.status == "INTEGRATION_REQUIRED"
    assert "unsupported-query" in evidence.status_reasons


def test_a_bag_relationship_wrapper_delegates_through_the_inner_query_call() -> None:
    """jhipster's many-to-many pattern: `return this.fetchBagRelationships(
    this.findAllWithToOneRelationships());` — the resolvable delegate is the inner
    @Query sibling; the outer fragment method is outside the pack's model."""
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
                '@Entity @Table(name="owners") class Owner {}',
            ),
            _source(
                "src/example/OwnerRepositoryWithBagRelationships.java",
                'package example; import java.util.List; '
                'interface OwnerRepositoryWithBagRelationships { '
                'List<Owner> fetchBagRelationships(List<Owner> owners); }',
            ),
            _source(
                "src/example/OwnerRepository.java",
                'package example; import java.util.List; '
                'import org.springframework.data.jpa.repository.JpaRepository; '
                'import org.springframework.data.jpa.repository.Query; '
                'interface OwnerRepository extends OwnerRepositoryWithBagRelationships, JpaRepository<Owner,Integer> { '
                '@Query("select owner from Owner owner") List<Owner> findAllWithToOneRelationships(); '
                'default List<Owner> findAllWithEagerRelationships() '
                '{ return this.fetchBagRelationships(this.findAllWithToOneRelationships()); } }',
            ),
            _source(
                "src/example/OwnerService.java",
                'package example; import java.util.List; class OwnerService { '
                'private final OwnerRepository owners; '
                'OwnerService(OwnerRepository owners){this.owners=owners;} '
                'List<Owner> run(){return owners.findAllWithEagerRelationships();}}',
            ),
            _source(
                "db/postgres/schema.sql",
                "create table owners (id int);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.status == "COMPLETE"
    assert len(evidence.edges) == 1
    assert evidence.edges[0].edge_type == "READS"


def test_limiter_and_projection_derived_subjects_ground_their_predicates() -> None:
    from lineage_api.services.java_spring_sca import _derived_properties

    assert _derived_properties("findOneByLogin") == ("login",)
    assert _derived_properties("findAllByIdNotNullAndActivatedIsTrue") == ("id", "activated")
    assert _derived_properties("findOneWithAuthoritiesByLogin") == ("login",)
    assert _derived_properties("findOneByEmailIgnoreCase") == ("email",)
    # `By` inside a word yields no properties (pre-existing `findBy` prefix rule:
    # the lowercase remainder is filtered, so nothing is ever grounded from it).
    assert _derived_properties("findByteStats") == ()
    # Plain repository methods are never mistaken for derived queries.
    assert _derived_properties("flush") is None


def test_a_delegated_call_inherits_the_delegates_element_grounding() -> None:
    evidence = _compile_java_spring(
        (
            _source("pom.xml", _maven_build()),
            _source(
                "src/example/Owner.java",
                'package example; import jakarta.persistence.Entity; '
                'import jakarta.persistence.Table; import jakarta.persistence.Column; '
                'import jakarta.persistence.Id; '
                '@Entity @Table(name="owners") class Owner { '
                '@Id @Column(name="id") Integer id; }',
            ),
            _source(
                "src/example/OwnerRepository.java",
                'package example; import java.util.Optional; '
                'import org.springframework.data.jpa.repository.JpaRepository; '
                'import org.springframework.data.jpa.repository.Query; '
                'interface OwnerRepository extends JpaRepository<Owner,Integer> { '
                '@Query("select owner from Owner owner where owner.id = :id") '
                'Optional<Owner> findOneWithToOneRelationships(Integer id); '
                'default Optional<Owner> findOneWithEagerRelationships(Integer id) '
                '{ return this.findOneWithToOneRelationships(id); } }',
            ),
            _source(
                "src/example/OwnerService.java",
                'package example; import java.util.Optional; class OwnerService { '
                'private final OwnerRepository owners; '
                'OwnerService(OwnerRepository owners){this.owners=owners;} '
                'Optional<Owner> run(){return owners.findOneWithEagerRelationships(1);}}',
            ),
            _source(
                "db/postgres/schema.sql",
                "create table owners (id int primary key);",
                dialect="postgres",
            ),
        )
    )

    assert evidence.status == "COMPLETE"
    urns = {edge.dataset_urn for edge in evidence.edges}
    assert "urn:ldp:staging:postgres:petclinic:owners" in urns
    assert "urn:ldp:staging:postgres:petclinic:owners#id" in urns
