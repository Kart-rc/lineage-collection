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


def test_literal_maven_and_gradle_facts_classify_supported_framework() -> None:
    result = JavaSpringScaAnalyzer().analyze(_corpus_sources())

    assert result.framework.status == "supported"
    assert result.framework.framework == "spring-data-jpa"
    assert result.framework.evidence == (
        "build.gradle:org.springframework.boot:spring-boot-starter-data-jpa:3.5.5",
        "pom.xml:org.springframework.boot:spring-boot-starter-data-jpa:3.5.5",
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
    pom = """<project><dependencies><dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-jpa</artifactId>
    <version>3.5.5</version>
    </dependency></dependencies></project>"""
    gradle = '''dependencies {
      implementation "org.springframework.boot:spring-boot-starter-data-jpa:3.4.0"
    }'''

    result = JavaSpringScaAnalyzer().analyze(
        (_source("pom.xml", pom), _source("build.gradle", gradle))
    )

    assert result.framework.status == "unsupported"
    assert result.framework.evidence == ()
    assert [entry.code for entry in result.residue] == [
        "ambiguous-framework-evidence"
    ]


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


def test_comments_and_strings_do_not_create_java_structure_or_invocations() -> None:
    build = (
        'dependencies { implementation "org.springframework.boot:'
        'spring-boot-starter-data-jpa:3.5.5" }'
    )
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


def test_sql_requires_explicit_dialect_and_malformed_sql_is_residue() -> None:
    build = """<project><dependencies><dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-jpa</artifactId>
    <version>3.5.5</version>
    </dependency></dependencies></project>"""
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
    build = """<project><dependencies><dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-jpa</artifactId>
    <version>3.5.5</version>
    </dependency></dependencies></project>"""
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
    build = (
        'dependencies { implementation "org.springframework.boot:'
        'spring-boot-starter-data-jpa:3.5.5" }'
    )
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


def test_fact_and_residue_limits_fail_closed() -> None:
    build = (
        'dependencies { implementation "org.springframework.boot:'
        'spring-boot-starter-data-jpa:3.5.5" }'
    )
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
