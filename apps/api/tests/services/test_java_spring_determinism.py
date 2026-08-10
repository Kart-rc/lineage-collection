from __future__ import annotations

from dataclasses import replace

import lineage_api.services.java_spring_sca as spring_sca


def _source(path: str, text: str, *, dialect: str | None = None):
    return spring_sca.JavaSpringSource(path, text.encode(), sql_dialect=dialect)


def _context():
    return spring_sca.JavaSpringEvidenceContext(
        origin="https://github.com/example/spring-service",
        repository="spring-service",
        revision="a" * 40,
        scope_digest="sha256:" + "b" * 64,
        environment="prod",
        platform="postgres",
        system="orders",
        analyzer_pack="java-spring-data-jpa-v1",
        ruleset_version="spring-data-rules-v1",
        resolver_version="schema-resolver-v1",
        schema_profile="postgres",
    )


def _sources():
    return (
        _source(
            "pom.xml",
            """<project><parent><groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-parent</artifactId><version>4.1.0</version>
            </parent><dependencies><dependency><groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-data-jpa</artifactId>
            </dependency></dependencies></project>""",
        ),
        _source(
            "src/Owner.java",
            'package example; import jakarta.persistence.Entity; import jakarta.persistence.Table; '
            '@Entity @Table(name="owners") class Owner {}',
        ),
        _source(
            "src/OwnerRepository.java",
            'package example; import org.springframework.data.jpa.repository.JpaRepository; '
            'interface OwnerRepository extends JpaRepository<Owner,Integer> {}',
        ),
        _source(
            "src/OwnerService.java",
            '''package example;
class OwnerService {
  private final OwnerRepository owners;
  OwnerService(OwnerRepository owners) { this.owners=owners; }
  Owner first() { return owners.findById(1).orElseThrow(); }
  Owner second() { return owners.findById(2).orElseThrow(); }
}''',
        ),
        _source(
            "db/postgres/schema.sql",
            "create table owners (id integer primary key);",
            dialect="postgres",
        ),
    )


def _compile(sources):
    analysis = spring_sca.JavaSpringScaAnalyzer().analyze(sources)
    return spring_sca.JavaSpringEvidenceCompiler().compile(analysis, _context())


def test_shuffled_sources_and_replay_are_byte_identical() -> None:
    sources = _sources()

    first = _compile(sources)
    replay = _compile(sources)
    shuffled = _compile(tuple(reversed(sources)))

    assert first.to_bytes() == replay.to_bytes() == shuffled.to_bytes()
    assert [edge.service_urn for edge in first.edges] == [
        "service://spring-service/example.OwnerService#first",
        "service://spring-service/example.OwnerService#second",
    ]
    assert (
        first.edges[0].evidence.invocation.location.start_byte
        != first.edges[1].evidence.invocation.location.start_byte
    )


def test_duplicate_proofs_collapse_without_losing_exact_citations() -> None:
    analysis = spring_sca.JavaSpringScaAnalyzer().analyze(_sources())
    invocation = next(fact for fact in analysis.facts if fact.kind == "java.invocation")
    duplicated = replace(analysis, facts=analysis.facts + (invocation,))

    evidence = spring_sca.JavaSpringEvidenceCompiler().compile(duplicated, _context())

    assert len(evidence.edges) == 2
    assert evidence.coverage.edge_candidates == 3
    assert evidence.coverage.edges_deduplicated == 1
    assert evidence.coverage.edges_emitted == 2
    assert len(evidence.edges[0].evidence.invocations) == 1


def test_metadata_determinants_change_stable_edge_identity() -> None:
    first = _compile(_sources())
    analysis = spring_sca.JavaSpringScaAnalyzer().analyze(_sources())
    changed_context = replace(_context(), revision="c" * 40)
    changed = spring_sca.JavaSpringEvidenceCompiler().compile(analysis, changed_context)

    assert [edge.stable_id for edge in first.edges] != [
        edge.stable_id for edge in changed.edges
    ]
