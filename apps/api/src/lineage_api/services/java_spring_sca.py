from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from bisect import bisect_right
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Iterable, TypeAlias

import tree_sitter_java
from sqlglot import exp
from sqlglot.dialects import Dialect
from sqlglot.errors import ErrorLevel, ParseError, TokenError
from sqlglot.tokens import Token, TokenType
from tree_sitter import Language, Node, Parser, Tree

from lineage_api.domain.urns import LineageUrn
from lineage_api.services.schema_migrations import (
    MigrationSource,
    is_flyway_migration,
    is_liquibase_changelog,
    order_migrations,
    replay_liquibase,
    replay_migrations,
)


FactValue: TypeAlias = str | tuple[str, ...]
_SUPPORTED_COORDINATES = {
    ("org.springframework.boot", "spring-boot-starter-data-jpa"),
    ("org.springframework.data", "spring-data-jpa"),
}
_ENTITY_FQN = "jakarta.persistence.Entity"
_TABLE_FQN = "jakarta.persistence.Table"
_COLUMN_FQN = "jakarta.persistence.Column"
_JOIN_COLUMN_FQN = "jakarta.persistence.JoinColumn"
_MANY_TO_ONE_FQN = "jakarta.persistence.ManyToOne"
_ONE_TO_ONE_FQN = "jakarta.persistence.OneToOne"
_ONE_TO_MANY_FQN = "jakarta.persistence.OneToMany"
_MANY_TO_MANY_FQN = "jakarta.persistence.ManyToMany"
_ASSOCIATION_FQNS = frozenset(
    {_MANY_TO_ONE_FQN, _ONE_TO_ONE_FQN, _ONE_TO_MANY_FQN, _MANY_TO_MANY_FQN}
)
_JPA_REPOSITORY_FQN = "org.springframework.data.jpa.repository.JpaRepository"
_REPOSITORY_FQN = "org.springframework.data.repository.Repository"
_QUERY_FQN = "org.springframework.data.jpa.repository.Query"
_AUTOWIRED_FQN = "org.springframework.beans.factory.annotation.Autowired"
_JAKARTA_INJECT_FQN = "jakarta.inject.Inject"
_JAVAX_INJECT_FQN = "javax.inject.Inject"
_FIELD_INJECTION_FQNS = frozenset(
    {_AUTOWIRED_FQN, _JAKARTA_INJECT_FQN, _JAVAX_INJECT_FQN}
)
# @Table attributes that never feed a resolved fact (nested annotations, arrays of
# constraints) so a dynamic expression there must not block the literal `name`.
_TABLE_IGNORED_ATTRIBUTE_KEYS = frozenset({"uniqueConstraints", "indexes"})
_APPROVED_FRAMEWORK_SYMBOLS = frozenset(
    {
        _ENTITY_FQN,
        _TABLE_FQN,
        _COLUMN_FQN,
        _JOIN_COLUMN_FQN,
        _MANY_TO_ONE_FQN,
        _ONE_TO_ONE_FQN,
        _ONE_TO_MANY_FQN,
        _MANY_TO_MANY_FQN,
        _JPA_REPOSITORY_FQN,
        _REPOSITORY_FQN,
        _QUERY_FQN,
        _AUTOWIRED_FQN,
        _JAKARTA_INJECT_FQN,
        _JAVAX_INJECT_FQN,
        "org.springframework.stereotype.Controller",
        "org.springframework.stereotype.Repository",
        "org.springframework.stereotype.Service",
        "org.springframework.web.bind.annotation.RestController",
    }
)
_SENSITIVE_FRAMEWORK_NAMES = frozenset(
    fqn.rsplit(".", 1)[-1] for fqn in _APPROVED_FRAMEWORK_SYMBOLS
)
_APPROVED_REPOSITORY_BASES = frozenset({_JPA_REPOSITORY_FQN, _REPOSITORY_FQN})
_JPA_INHERITED_OPERATIONS = frozenset(
    {
        "count",
        "delete",
        "deleteAll",
        "deleteAllById",
        "deleteAllByIdInBatch",
        "deleteAllInBatch",
        "deleteById",
        "existsById",
        "findAll",
        "findAllById",
        "findById",
        "getById",
        "getOne",
        "getReferenceById",
        "save",
        "saveAll",
        "saveAllAndFlush",
        "saveAndFlush",
    }
)
_JPA_INHERITED_ARITIES = {
    "count": frozenset({0}),
    "delete": frozenset({1}),
    "deleteAll": frozenset({0, 1}),
    "deleteAllById": frozenset({1}),
    "deleteAllByIdInBatch": frozenset({1}),
    "deleteAllInBatch": frozenset({0, 1}),
    "deleteById": frozenset({1}),
    "existsById": frozenset({1}),
    "findAll": frozenset({0, 1}),
    "findAllById": frozenset({1}),
    "findById": frozenset({1}),
    "getById": frozenset({1}),
    "getOne": frozenset({1}),
    "getReferenceById": frozenset({1}),
    "save": frozenset({1}),
    "saveAll": frozenset({1}),
    "saveAllAndFlush": frozenset({1}),
    "saveAndFlush": frozenset({1}),
}
_DYNAMIC_BUILD_TOKEN = re.compile(r"(?:\$\{|\$[A-Za-z_]|\+)")
_SEMANTIC_VERSION = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")
_EVIDENCE_JAVA_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]{0,127}")
_EVIDENCE_TABLE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]{0,127}")
_GRADLE_CONFIGURATIONS = {"api", "compileOnly", "implementation", "runtimeOnly"}
_SQLGLOT_DIALECTS = {
    "h2": "postgres",
    "mysql": "mysql",
    "postgres": "postgres",
    "postgresql": "postgres",
}
_MAX_SOURCE_PATH_BYTES = 512
_MAX_AST_KIND_CHARS = 64
_MAX_AST_PATH_CHARS = 512
_RESIDUE_CODES = frozenset(
    {
        "ambiguous-entity-name",
        "ambiguous-framework-evidence",
        "ambiguous-framework-symbol",
        "ambiguous-repository-abstraction",
        "ambiguous-repository-injection",
        "ambiguous-wildcard-symbol",
        "dynamic-framework-evidence",
        "dynamic-entity-name",
        "dynamic-query",
        "dynamic-sql-identifier",
        "dynamic-table-mapping",
        "incompatible-framework-cell",
        "invalid-build-encoding",
        "invalid-java-encoding",
        "invalid-entity-name",
        "invalid-repository-declaration",
        "invalid-repository-generics",
        "invalid-sql-encoding",
        "malformed-build-file",
        "malformed-java",
        "malformed-sql",
        "missing-boot-evidence",
        "missing-boot-version",
        "missing-jpa-dependency",
        "missing-jpa-version",
        "missing-sql-dialect",
        "ignored-schema-statement",
        "shadowed-framework-symbol",
        "shadowed-repository-receiver",
        "unbound-repository-receiver",
        "unknown-framework",
        "unmapped-entity-column",
        "unresolved-framework-symbol",
        "unresolved-query-property",
        "unresolved-repository-entity",
        "unresolved-table-mapping",
        "unsupported-boot-version",
        "unsupported-direct-spring-data-jpa",
        "unsupported-h2-construct",
        "unsupported-jpa-version",
        "unsupported-sql",
        "wildcard-framework-symbol",
        # Migration-replay vocabulary: `_ingest_migrations` forwards residue produced
        # by `schema_migrations` verbatim, so that closed set is part of this one.
        "ambiguous-migration-version",
        "malformed-changelog",
        "malformed-migration-sql",
        "missing-changelog-include",
        "repeatable-migration-excluded",
        "unknown-migration-table",
        "unmodelled-changelog-change",
        "unmodelled-migration-statement",
        "unrecognised-migration-name",
        "unsupported-changelog-format",
    }
)


class JavaSpringAnalysisError(ValueError):
    """The supplied source scope violated an analyzer trust or resource boundary."""


@dataclass(frozen=True, slots=True)
class JavaSpringSource:
    path: str
    content: bytes
    sql_dialect: str | None = None


@dataclass(frozen=True, slots=True)
class JavaSpringScaLimits:
    max_files: int = 5_000
    max_file_bytes: int = 2 * 1024 * 1024
    max_total_bytes: int = 64 * 1024 * 1024
    max_facts: int = 100_000
    max_residue: int = 10_000
    max_ast_nodes: int = 500_000

    def __post_init__(self) -> None:
        for name in (
            "max_files",
            "max_file_bytes",
            "max_total_bytes",
            "max_facts",
            "max_residue",
            "max_ast_nodes",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name.replace('_', ' ')} must be a positive integer")


@dataclass(frozen=True, slots=True)
class SourceLocation:
    path: str
    line: int
    start_byte: int
    end_byte: int
    ast_kind: str
    ast_path: str


@dataclass(frozen=True, slots=True)
class SyntaxFact:
    identifier: str
    kind: str
    subject: str
    attributes: tuple[tuple[str, FactValue], ...]
    location: SourceLocation

    def attribute(self, name: str) -> FactValue:
        for key, value in self.attributes:
            if key == name:
                return value
        raise KeyError(name)


@dataclass(frozen=True, slots=True)
class AnalysisResidue:
    code: str
    message: str
    symbol: str
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class FrameworkClassification:
    status: str
    framework: str | None
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnalysisStats:
    files_seen: int
    java_files_parsed: int
    sql_files_parsed: int
    fact_count: int
    residue_count: int
    ast_nodes_indexed: int


@dataclass(frozen=True, slots=True)
class JavaSpringAnalysis:
    framework: FrameworkClassification
    facts: tuple[SyntaxFact, ...]
    residue: tuple[AnalysisResidue, ...]
    stats: AnalysisStats


@dataclass(frozen=True, slots=True)
class JavaSpringEvidenceContext:
    origin: str
    repository: str
    revision: str
    scope_digest: str
    environment: str
    platform: str
    system: str
    analyzer_pack: str
    ruleset_version: str
    resolver_version: str
    schema_profile: str
    scope_complete: bool = True

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.revision) is None:
            raise ValueError("revision must be an exact lowercase digest")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", self.scope_digest) is None:
            raise ValueError("scope digest must be an exact sha256 digest")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.repository) is None:
            raise ValueError("repository must be a safe name")
        if self.analyzer_pack != "java-spring-data-jpa-v1":
            raise ValueError("analyzer pack is outside the closed Java/Spring cell")
        if self.schema_profile not in _SQLGLOT_DIALECTS:
            raise ValueError("schema profile is outside the supported closed set")
        for name in (
            "origin",
            "environment",
            "platform",
            "system",
            "ruleset_version",
            "resolver_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name.replace('_', ' ')} must be a non-empty determinant")
            if len(value) > 512 or any(ord(character) < 32 for character in value):
                raise ValueError(f"{name.replace('_', ' ')} exceeds evidence bounds")
        if not self.origin.startswith("https://") or any(
            token in self.origin for token in ("@", "?", "#")
        ):
            raise ValueError("origin must be credential-free canonical HTTPS")
        LineageUrn(self.environment, self.platform, self.system, "__schema__")
        if not isinstance(self.scope_complete, bool):
            raise ValueError("scope complete must be boolean")


@dataclass(frozen=True, slots=True)
class JavaSpringFactReference:
    identifier: str
    kind: str
    subject: str
    location: SourceLocation
    attributes: tuple[tuple[str, FactValue], ...]

    @classmethod
    def from_fact(cls, fact: SyntaxFact) -> "JavaSpringFactReference":
        return cls(
            fact.identifier,
            fact.kind,
            fact.subject,
            fact.location,
            _evidence_fact_attributes(fact),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "kind": self.kind,
            "subject": self.subject,
            "location": _location_dict(self.location),
            "attributes": [
                {"name": name, "value": list(value) if isinstance(value, tuple) else value}
                for name, value in self.attributes
            ],
        }

    def attribute(self, name: str) -> FactValue:
        for key, value in self.attributes:
            if key == name:
                return value
        raise KeyError(name)


@dataclass(frozen=True, slots=True)
class JavaSpringEdgeEvidence:
    origin: str
    repository_name: str
    revision: str
    scope_digest: str
    analyzer_pack: str
    ruleset_version: str
    resolver_version: str
    schema_profile: str
    framework_evidence: tuple[str, ...]
    operation_rationale: str
    invocations: tuple[JavaSpringFactReference, ...]
    repository: JavaSpringFactReference
    entity: JavaSpringFactReference
    table: JavaSpringFactReference
    query: JavaSpringFactReference | None = None

    @property
    def invocation(self) -> JavaSpringFactReference:
        return self.invocations[0]

    def as_dict(self) -> dict[str, object]:
        return {
            "file": self.invocation.location.path,
            "line": self.invocation.location.line,
            "astPath": self.invocation.location.ast_path,
            "origin": self.origin,
            "revision": self.revision,
            "scopeDigest": self.scope_digest,
            "analyzerPack": self.analyzer_pack,
            "rulesetVersion": self.ruleset_version,
            "resolverVersion": self.resolver_version,
            "schemaProfile": self.schema_profile,
            "frameworkEvidence": list(self.framework_evidence),
            "operationRationale": self.operation_rationale,
            "invocations": [item.as_dict() for item in self.invocations],
            "repository": self.repository.as_dict(),
            "entity": self.entity.as_dict(),
            "table": self.table.as_dict(),
            "query": self.query.as_dict() if self.query is not None else None,
        }


@dataclass(frozen=True, slots=True)
class JavaSpringLineageEdge:
    stable_id: str
    from_urn: str
    to_urn: str
    edge_type: str
    transform: str
    service_urn: str
    dataset_urn: str
    exact: bool
    executed: bool
    mechanism: str
    evidence: JavaSpringEdgeEvidence

    @property
    def lineage_tuple(self) -> tuple[str, str, str, str]:
        return self.from_urn, self.to_urn, self.edge_type, self.transform

    def as_dict(self) -> dict[str, object]:
        return {
            "stableId": self.stable_id,
            "from": [self.from_urn],
            "to": self.to_urn,
            "edgeType": self.edge_type,
            "transform": self.transform,
            "serviceUrn": self.service_urn,
            "datasetUrn": self.dataset_urn,
            "mechanism": self.mechanism,
            "exact": self.exact,
            "executed": self.executed,
            "repo": self.evidence.repository_name,
            "digest": self.evidence.revision,
            "scopeDigest": self.evidence.scope_digest,
            "analyzerPack": self.evidence.analyzer_pack,
            "rulesetVersion": self.evidence.ruleset_version,
            "resolverVersion": self.evidence.resolver_version,
            "snapshotId": self.evidence.scope_digest,
            "schemaProfile": self.evidence.schema_profile,
            "evidence": self.evidence.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class JavaSpringCompilationResidue:
    code: str
    location: SourceLocation
    fact_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "location": _location_dict(self.location),
            "factIds": list(self.fact_ids),
        }


@dataclass(frozen=True, slots=True)
class JavaSpringCoverage:
    supported_java_files: int
    syntax_facts: int
    parser_residue: int
    invocations_seen: int
    invocations_proven: int
    invocations_unresolved: int
    edge_candidates: int
    edges_emitted: int
    edges_deduplicated: int
    residue_count: int

    def as_dict(self) -> dict[str, int]:
        return {
            "supportedJavaFiles": self.supported_java_files,
            "syntaxFacts": self.syntax_facts,
            "parserResidue": self.parser_residue,
            "invocationsSeen": self.invocations_seen,
            "invocationsProven": self.invocations_proven,
            "invocationsUnresolved": self.invocations_unresolved,
            "edgeCandidates": self.edge_candidates,
            "edgesEmitted": self.edges_emitted,
            "edgesDeduplicated": self.edges_deduplicated,
            "residueCount": self.residue_count,
        }


@dataclass(frozen=True, slots=True)
class JavaSpringLineageEvidence:
    status: str
    status_reasons: tuple[str, ...]
    context: JavaSpringEvidenceContext
    edges: tuple[JavaSpringLineageEdge, ...]
    residue: tuple[JavaSpringCompilationResidue, ...]
    coverage: JavaSpringCoverage

    def as_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": "1.0.0",
            "status": self.status,
            "statusReasons": list(self.status_reasons),
            "context": {
                "origin": self.context.origin,
                "repository": self.context.repository,
                "revision": self.context.revision,
                "scopeDigest": self.context.scope_digest,
                "environment": self.context.environment,
                "platform": self.context.platform,
                "system": self.context.system,
                "analyzerPack": self.context.analyzer_pack,
                "rulesetVersion": self.context.ruleset_version,
                "resolverVersion": self.context.resolver_version,
                "schemaProfile": self.context.schema_profile,
                "scopeComplete": self.context.scope_complete,
            },
            "edges": [edge.as_dict() for edge in self.edges],
            "residue": [item.as_dict() for item in self.residue],
            "coverage": self.coverage.as_dict(),
        }

    def to_bytes(self) -> bytes:
        return json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()


@dataclass(frozen=True, slots=True)
class _CompiledCandidate:
    from_urn: str
    to_urn: str
    edge_type: str
    transform: str
    service_urn: str
    dataset_urn: str
    rationale: str
    invocation: SyntaxFact
    repository: SyntaxFact
    entity: SyntaxFact
    table: SyntaxFact
    query: SyntaxFact | None


@dataclass(frozen=True, slots=True)
class _Operation:
    edge_type: str
    label: str
    rationale: str
    query: SyntaxFact | None = None
    # A @Query may legitimately target an entity other than the one its repository is
    # declared over — Petclinic's PetRepository reads PetType. The edge then belongs to
    # that entity's table, which is more precise than refusing it as a conflict.
    entity_override: str | None = None


@dataclass(frozen=True, slots=True)
class _IdentifierPart:
    value: str
    quoted: bool

    @property
    def rendered(self) -> str:
        return f'"{self.value}"' if self.quoted else self.value


@dataclass(frozen=True, slots=True)
class _QualifiedTableIdentity:
    name: _IdentifierPart
    schema: _IdentifierPart | None = None
    catalog: _IdentifierPart | None = None

    @property
    def rendered(self) -> str:
        return ".".join(
            part.rendered
            for part in (self.catalog, self.schema, self.name)
            if part is not None
        )

    def matches(self, candidate: "_QualifiedTableIdentity") -> bool:
        return (
            self.name == candidate.name
            and self.schema == candidate.schema
            and self.catalog == candidate.catalog
        )


class JavaSpringEvidenceCompiler:
    """Compiles only complete Spring Data call-site proof chains into static edges."""

    _NONBLOCKING_ANALYSIS_RESIDUE = frozenset({"ignored-schema-statement"})

    def compile(
        self,
        analysis: JavaSpringAnalysis,
        context: JavaSpringEvidenceContext,
    ) -> JavaSpringLineageEvidence:
        facts = tuple(sorted(analysis.facts, key=lambda item: item.identifier))
        by_kind = {
            kind: tuple(item for item in facts if item.kind == kind)
            for kind in {
                "java.annotation",
                "java.invocation",
                "java.method",
                "spring.entity-table",
                "spring.query",
                "spring.repository-association",
                "spring.entity-field",
                "spring.query-element",
                "sql.column",
                "sql.table",
            }
        }
        residue: list[JavaSpringCompilationResidue] = [
            JavaSpringCompilationResidue(item.code, item.location)
            for item in analysis.residue
        ]
        status_reasons = {
            item.code
            for item in analysis.residue
            if item.code not in self._NONBLOCKING_ANALYSIS_RESIDUE
        }
        if analysis.framework.status != "supported":
            status_reasons.add("unsupported-framework")
        if analysis.stats.java_files_parsed == 0:
            status_reasons.add("zero-supported-java-files")
        if not context.scope_complete:
            status_reasons.add("incomplete-scope")

        repositories = _unique_facts_by_subject(
            by_kind["spring.repository-association"]
        )
        entities = _unique_facts_by_subject(by_kind["spring.entity-table"])
        queries = _facts_by_subject(by_kind["spring.query"])
        methods = _facts_by_subject(by_kind["java.method"])
        query_annotations = _query_annotations(by_kind["java.annotation"])
        tables = _native_profile_tables(by_kind["sql.table"], context.schema_profile)
        elements_by_method: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for fact in by_kind["spring.query-element"]:
            attributes = dict(fact.attributes)
            key = (str(attributes["repository"]), str(attributes["method"]))
            elements_by_method.setdefault(key, []).append(
                (str(attributes["table"]), str(attributes["column"]))
            )
        candidates: list[_CompiledCandidate] = []
        unresolved = 0

        for invocation in by_kind["java.invocation"]:
            invocation_facts = (invocation.identifier,)
            repository_name = str(invocation.attribute("repositoryType"))
            repository = repositories.get(repository_name)
            if repository is None:
                unresolved += 1
                code = (
                    "ambiguous-repository"
                    if repository_name in repositories.duplicates
                    else "missing-repository"
                )
                residue.append(
                    JavaSpringCompilationResidue(
                        code, invocation.location, invocation_facts
                    )
                )
                status_reasons.add(code)
                continue
            entity_name = str(repository.attribute("entityFqn"))
            entity = entities.get(entity_name)
            if entity is None:
                unresolved += 1
                code = (
                    "ambiguous-entity-mapping"
                    if entity_name in entities.duplicates
                    else "missing-entity-mapping"
                )
                residue.append(
                    JavaSpringCompilationResidue(
                        code,
                        invocation.location,
                        (invocation.identifier, repository.identifier),
                    )
                )
                status_reasons.add(code)
                continue
            table_identity = _mapping_table_identity(entity, context.schema_profile)
            if table_identity is None:
                unresolved += 1
                code = "unsupported-edge-identity"
                residue.append(
                    JavaSpringCompilationResidue(
                        code,
                        invocation.location,
                        (invocation.identifier, repository.identifier, entity.identifier),
                    )
                )
                status_reasons.add(code)
                continue
            table_name = table_identity.rendered
            matching_tables = tuple(
                table
                for table in tables
                if (
                    (candidate := _sql_fact_table_identity(table, context.schema_profile))
                    is not None
                    and table_identity.matches(candidate)
                )
            )
            if len(matching_tables) != 1:
                unresolved += 1
                code = (
                    "ambiguous-schema-table"
                    if len(matching_tables) > 1
                    else "missing-schema-table"
                )
                residue.append(
                    JavaSpringCompilationResidue(
                        code,
                        invocation.location,
                        (invocation.identifier, repository.identifier, entity.identifier),
                    )
                )
                status_reasons.add(code)
                continue
            table = matching_tables[0]
            called = str(invocation.attribute("method"))
            invocation_arity = str(invocation.attribute("arity"))
            owner_and_method = invocation.subject.rsplit(":", 1)[0]
            owner, separator, enclosing_method = owner_and_method.partition("#")
            if not (
                _bounded_java_fqn(repository.subject)
                and _bounded_java_fqn(entity_name)
                and _bounded_java_fqn(owner)
                and separator
                and _EVIDENCE_JAVA_IDENTIFIER.fullmatch(enclosing_method)
                and _EVIDENCE_JAVA_IDENTIFIER.fullmatch(called)
                and invocation_arity.isdigit()
                and int(invocation_arity) <= 255
            ):
                unresolved += 1
                code = "unsupported-edge-identity"
                residue.append(
                    JavaSpringCompilationResidue(
                        code,
                        invocation.location,
                        (
                            invocation.identifier,
                            repository.identifier,
                            entity.identifier,
                            table.identifier,
                        ),
                    )
                )
                status_reasons.add(code)
                continue
            operation, operation_error = _resolve_operation(
                repository,
                entity,
                table,
                called,
                int(invocation_arity),
                table_identity,
                queries,
                query_annotations,
                methods,
                context.schema_profile,
            )
            if operation is None:
                unresolved += 1
                code = operation_error or "unsupported-operation"
                residue.append(
                    JavaSpringCompilationResidue(
                        code,
                        invocation.location,
                        (
                            invocation.identifier,
                            repository.identifier,
                            entity.identifier,
                            table.identifier,
                        ),
                    )
                )
                status_reasons.add(code)
                continue

            if operation.entity_override is not None:
                override = next(
                    (
                        candidate
                        for _subject, candidate in entities.values
                        if candidate.attribute("entityName") == operation.entity_override
                    ),
                    None,
                )
                override_identity = (
                    _mapping_table_identity(override, context.schema_profile)
                    if override is not None
                    else None
                )
                override_tables = (
                    tuple(
                        item
                        for item in tables
                        if (
                            (
                                candidate := _sql_fact_table_identity(
                                    item, context.schema_profile
                                )
                            )
                            is not None
                            and override_identity is not None
                            and override_identity.matches(candidate)
                        )
                    )
                    if override_identity is not None
                    else ()
                )
                if len(override_tables) != 1:
                    unresolved += 1
                    residue.append(
                        JavaSpringCompilationResidue(
                            "query-entity-conflict",
                            invocation.location,
                            (invocation.identifier, repository.identifier),
                        )
                    )
                    status_reasons.add("query-entity-conflict")
                    continue
                entity = override
                table = override_tables[0]
                table_identity = override_identity
                table_name = override_identity.rendered

            service_urn = f"service://{context.repository}/{owner_and_method}"
            dataset_urn = str(
                LineageUrn(
                    context.environment,
                    context.platform,
                    context.system,
                    table_name,
                )
            )
            repository_simple = repository.subject.rsplit(".", 1)[-1]
            transform = f"{repository_simple}.{called} -> {table_name}{operation.label}"
            from_urn, to_urn = (
                (dataset_urn, service_urn)
                if operation.edge_type == "READS"
                else (service_urn, dataset_urn)
            )
            candidates.append(
                _CompiledCandidate(
                    from_urn,
                    to_urn,
                    operation.edge_type,
                    transform,
                    service_urn,
                    dataset_urn,
                    operation.rationale,
                    invocation,
                    repository,
                    entity,
                    table,
                    operation.query,
                )
            )

            # Additionally element-scope the dataset side for every provable column a
            # `spring.query-element` fact ties to this exact invoked (repository, method)
            # -- but only when that fact's own table agrees with the table this candidate
            # already resolved to. A fact for a different table (a cross-entity JPQL
            # projection, resolved against the repository's own declared entity rather
            # than the entity the query actually targets) must never element-scope a
            # candidate it does not describe; that stays an honest wall.
            for element_table, column in elements_by_method.get(
                (repository.subject, called), ()
            ):
                if element_table != table_name:
                    continue
                element_dataset_urn = str(
                    LineageUrn(
                        context.environment,
                        context.platform,
                        context.system,
                        table_name,
                    ).with_element(column)
                )
                element_transform = f"{transform}#{column}"
                element_from_urn, element_to_urn = (
                    (element_dataset_urn, service_urn)
                    if operation.edge_type == "READS"
                    else (service_urn, element_dataset_urn)
                )
                candidates.append(
                    _CompiledCandidate(
                        element_from_urn,
                        element_to_urn,
                        operation.edge_type,
                        element_transform,
                        service_urn,
                        element_dataset_urn,
                        operation.rationale,
                        invocation,
                        repository,
                        entity,
                        table,
                        operation.query,
                    )
                )

        edges = _collapse_java_spring_candidates(
            candidates, context, analysis.framework.evidence
        )
        ordered_residue = tuple(
            sorted(
                set(residue),
                key=lambda item: (
                    item.location.path,
                    item.location.start_byte,
                    item.code,
                    item.fact_ids,
                ),
            )
        )
        coverage = JavaSpringCoverage(
            supported_java_files=analysis.stats.java_files_parsed,
            syntax_facts=len(facts),
            parser_residue=len(analysis.residue),
            invocations_seen=len(by_kind["java.invocation"]),
            invocations_proven=len(candidates),
            invocations_unresolved=unresolved,
            edge_candidates=len(candidates),
            edges_emitted=len(edges),
            edges_deduplicated=len(candidates) - len(edges),
            residue_count=len(ordered_residue),
        )
        reasons = tuple(sorted(status_reasons))
        return JavaSpringLineageEvidence(
            "INTEGRATION_REQUIRED" if reasons else "COMPLETE",
            reasons,
            context,
            edges,
            ordered_residue,
            coverage,
        )


@dataclass(frozen=True, slots=True)
class _UniqueFactMap:
    values: tuple[tuple[str, SyntaxFact], ...]
    duplicates: frozenset[str]

    def get(self, subject: str) -> SyntaxFact | None:
        if subject in self.duplicates:
            return None
        return next(
            (fact for found_subject, fact in self.values if found_subject == subject),
            None,
        )


def _unique_facts_by_subject(facts: tuple[SyntaxFact, ...]) -> _UniqueFactMap:
    grouped = _facts_by_subject(facts)
    return _UniqueFactMap(
        tuple(
            (subject, values[0])
            for subject, values in sorted(grouped.items())
            if len(values) == 1
        ),
        frozenset(subject for subject, values in grouped.items() if len(values) != 1),
    )


def _facts_by_subject(
    facts: tuple[SyntaxFact, ...],
) -> dict[str, tuple[SyntaxFact, ...]]:
    grouped: dict[str, list[SyntaxFact]] = {}
    for fact in facts:
        grouped.setdefault(fact.subject, []).append(fact)
    return {
        subject: tuple(sorted(values, key=lambda item: item.identifier))
        for subject, values in sorted(grouped.items())
    }


def _optional_attribute(fact: SyntaxFact, name: str) -> FactValue | None:
    try:
        return fact.attribute(name)
    except KeyError:
        return None


def _query_annotations(
    annotations: tuple[SyntaxFact, ...],
) -> dict[str, tuple[SyntaxFact, ...]]:
    grouped: dict[str, list[SyntaxFact]] = {}
    for fact in annotations:
        if _optional_attribute(fact, "resolvedFqn") != _QUERY_FQN:
            continue
        subject = fact.subject.removesuffix(":@Query")
        grouped.setdefault(subject, []).append(fact)
    return {
        subject: tuple(sorted(values, key=lambda item: item.identifier))
        for subject, values in sorted(grouped.items())
    }


def _native_profile_tables(
    facts: tuple[SyntaxFact, ...], schema_profile: str
) -> tuple[SyntaxFact, ...]:
    if schema_profile == "h2":
        return ()
    expected_dialect = _SQLGLOT_DIALECTS[schema_profile]
    return tuple(
        fact
        for fact in facts
        if (
            _optional_attribute(fact, "dialectMode") == "native"
            and _optional_attribute(fact, "dialect") == expected_dialect
            and (
                schema_profile in PurePosixPath(fact.location.path).parts
                or _optional_attribute(fact, "schemaSource") == "migration"
            )
        )
    )


def _mapping_table_identity(
    fact: SyntaxFact, schema_profile: str
) -> _QualifiedTableIdentity | None:
    dialect = Dialect.get_or_raise(_SQLGLOT_DIALECTS[schema_profile])
    name = _mapping_identifier_part(str(fact.attribute("table")), dialect)
    if name is None:
        return None
    schema = _optional_mapping_identifier_part(fact, "schema", dialect)
    catalog = _optional_mapping_identifier_part(fact, "catalog", dialect)
    if schema is False or catalog is False:
        return None
    return _QualifiedTableIdentity(
        name,
        schema if isinstance(schema, _IdentifierPart) else None,
        catalog if isinstance(catalog, _IdentifierPart) else None,
    )


def _optional_mapping_identifier_part(
    fact: SyntaxFact, name: str, dialect: Dialect
) -> _IdentifierPart | bool | None:
    raw = _optional_attribute(fact, name)
    explicit = _optional_attribute(fact, f"{name}Explicit")
    if explicit != "true":
        return None
    if not isinstance(raw, str):
        return False
    return _mapping_identifier_part(raw, dialect) or False


def _mapping_identifier_part(raw: str, dialect: Dialect) -> _IdentifierPart | None:
    if not raw or len(raw) > 130:
        return None
    quoted = False
    value = raw
    quote_pairs = {'"': '"', "`": "`", "[": "]"}
    if raw[0] in quote_pairs:
        if len(raw) < 3 or raw[-1] != quote_pairs[raw[0]]:
            return None
        quoted = True
        value = raw[1:-1]
    if _EVIDENCE_TABLE_IDENTIFIER.fullmatch(value) is None:
        return None
    return _normalized_identifier_part(value, quoted, dialect)


def _sql_fact_table_identity(
    fact: SyntaxFact, schema_profile: str
) -> _QualifiedTableIdentity | None:
    dialect = Dialect.get_or_raise(_SQLGLOT_DIALECTS[schema_profile])
    name = _sql_fact_identifier_part(fact.subject, fact, "nameQuoted", dialect)
    schema = _sql_fact_identifier_part(
        str(_optional_attribute(fact, "schema") or ""), fact, "schemaQuoted", dialect
    )
    catalog = _sql_fact_identifier_part(
        str(_optional_attribute(fact, "catalog") or ""), fact, "catalogQuoted", dialect
    )
    if name is None:
        return None
    return _QualifiedTableIdentity(name, schema, catalog)


def _sql_fact_identifier_part(
    raw: str, fact: SyntaxFact, quoted_attribute: str, dialect: Dialect
) -> _IdentifierPart | None:
    if not raw:
        return None
    if _EVIDENCE_TABLE_IDENTIFIER.fullmatch(raw) is None:
        return None
    return _normalized_identifier_part(
        raw, _optional_attribute(fact, quoted_attribute) == "true", dialect
    )


def _normalized_identifier_part(
    value: str, quoted: bool, dialect: Dialect
) -> _IdentifierPart:
    identifier = dialect.normalize_identifier(
        exp.Identifier(this=value, quoted=quoted)
    )
    return _IdentifierPart(identifier.name, quoted)


def _sql_expression_table_identity(
    table: exp.Table, dialect: Dialect
) -> _QualifiedTableIdentity | None:
    name = _sql_expression_identifier_part(table.this, dialect)
    schema = _sql_expression_identifier_part(table.args.get("db"), dialect)
    catalog = _sql_expression_identifier_part(table.args.get("catalog"), dialect)
    if name is None:
        return None
    return _QualifiedTableIdentity(name, schema, catalog)


def _sql_expression_identifier_part(
    expression: exp.Expression | str | None, dialect: Dialect
) -> _IdentifierPart | None:
    if expression is None:
        return None
    if not isinstance(expression, exp.Identifier):
        return None
    if _EVIDENCE_TABLE_IDENTIFIER.fullmatch(expression.name) is None:
        return None
    return _normalized_identifier_part(
        expression.name, bool(expression.args.get("quoted")), dialect
    )


def _resolve_operation(
    repository: SyntaxFact,
    entity: SyntaxFact,
    table: SyntaxFact,
    called: str,
    invocation_arity: int,
    table_identity: _QualifiedTableIdentity,
    queries: dict[str, tuple[SyntaxFact, ...]],
    query_annotations: dict[str, tuple[SyntaxFact, ...]],
    methods: dict[str, tuple[SyntaxFact, ...]],
    schema_profile: str,
) -> tuple[_Operation | None, str | None]:
    matching_methods = tuple(
        method
        for method_facts in methods.values()
        for method in method_facts
        if method.subject.startswith(f"{repository.subject}#{called}(")
        and _optional_attribute(method, "name") == called
        and _optional_attribute(method, "arity") == str(invocation_arity)
    )
    if len(matching_methods) > 1:
        return None, "ambiguous-operation-overload"
    method = matching_methods[0] if matching_methods else None
    if method is not None and _optional_attribute(method, "declarationMode") != "abstract-interface":
        return None, "unsupported-operation"
    inherited = (
        repository.attribute("baseFqn") == _JPA_REPOSITORY_FQN
        and called in _JPA_INHERITED_OPERATIONS
        and invocation_arity in _JPA_INHERITED_ARITIES.get(called, ())
    )
    if method is not None and inherited and not _exact_inherited_redeclaration(
        repository, called, method
    ):
        return None, "ambiguous-operation-overload"
    method_subject = method.subject if method is not None else None
    annotations = query_annotations.get(method_subject, ()) if method_subject else ()
    if annotations:
        if len(annotations) != 1:
            return None, "ambiguous-query"
        annotation = annotations[0]
        if _optional_attribute(annotation, "dynamicValues"):
            return None, "dynamic-query"
        query_facts = queries.get(method_subject, ())
        if len(query_facts) != 1:
            return None, "ambiguous-query"
        query = query_facts[0]
        operation, target, language = _parse_explicit_query(query, schema_profile)
        if operation is None or target is None or language is None:
            return None, "unsupported-query"
        target_matches = (
            isinstance(target, _QualifiedTableIdentity)
            and table_identity.matches(target)
            if language == "SQL"
            else isinstance(target, str)
            and target == entity.attribute("entityName")
        )
        override: str | None = None
        if not target_matches:
            if language == "SQL" or not isinstance(target, str):
                return None, "query-table-conflict"
            # Only an entity actually declared in this scope may redirect the edge; an
            # unknown name is still a conflict, never a guessed table.
            override = target
        edge_type = "READS" if operation == "SELECT" else "WRITES"
        return (
            _Operation(
                edge_type,
                f" [{language} {operation}]",
                f"explicit @Query parsed as {language} {operation}",
                query,
                override,
            ),
            None,
        )

    if method is None and not inherited:
        return None, "unsupported-operation"
    lower = called.casefold()
    if lower.startswith(("find", "get", "read", "count", "exists")):
        return _Operation("READS", "", f"derived Spring Data read method {called}"), None
    if lower.startswith(("save", "insert", "update")):
        return _Operation("WRITES", "", f"derived Spring Data write method {called}"), None
    if lower.startswith(("delete", "remove")):
        return (
            _Operation(
                "WRITES",
                " [DELETE]",
                f"derived Spring Data delete method {called}",
            ),
            None,
        )
    return None, "unsupported-operation"


def _exact_inherited_redeclaration(
    repository: SyntaxFact, called: str, method: SyntaxFact
) -> bool:
    parameter_types = _optional_attribute(method, "parameterTypes")
    if not isinstance(parameter_types, tuple):
        return False
    if not parameter_types:
        return True
    expected_attribute = (
        "idType"
        if called
        in {
            "deleteById",
            "existsById",
            "findById",
            "getById",
            "getOne",
            "getReferenceById",
        }
        else "entityType"
        if called in {"delete", "save", "saveAndFlush"}
        else None
    )
    expected = (
        _optional_attribute(repository, expected_attribute)
        if expected_attribute is not None
        else None
    )
    return isinstance(expected, str) and parameter_types == (expected,)


def _parse_explicit_query(
    query: SyntaxFact, schema_profile: str
) -> tuple[
    str | None,
    str | _QualifiedTableIdentity | None,
    str | None,
]:
    statement = str(query.attribute("query"))
    native = _optional_attribute(query, "nativeQuery") == "true"
    dialect_name = _SQLGLOT_DIALECTS[schema_profile]
    try:
        dialect = Dialect.get_or_raise(dialect_name if native else "postgres")
        tokens = dialect.tokenizer().tokenize(statement)
        if not tokens or any(
            token.token_type == TokenType.SEMICOLON for token in tokens
        ):
            return None, None, None
        normalized = _normalize_jpa_positional_parameters(statement, tokens)
        expressions = _silent_sqlglot_parse(dialect, normalized)
    except (ParseError, ValueError, TokenError):
        return None, None, None
    if len(expressions) != 1:
        return None, None, None
    parsed = (
        _native_query_operation(expressions[0])
        if native
        else _jpql_query_operation(expressions[0])
    )
    if parsed is None:
        return None, None, None
    operation, table = parsed
    if not table.name:
        return None, None, None
    if not native:
        if table.catalog or table.db:
            return None, None, None
        return operation, table.name, "JPQL"
    identity = _sql_expression_table_identity(table, dialect)
    if identity is None:
        return None, None, None
    return operation, identity, "SQL"


def _normalize_jpa_positional_parameters(
    statement: str, tokens: list[Token]
) -> str:
    normalized = list(statement)
    for placeholder, number in zip(tokens, tokens[1:]):
        if (
            placeholder.token_type == TokenType.PLACEHOLDER
            and number.token_type == TokenType.NUMBER
            and placeholder.end + 1 == number.start
        ):
            for index in range(number.start, number.end + 1):
                normalized[index] = " "
    return "".join(normalized)


def _native_query_operation(
    expression: exp.Expression,
) -> tuple[str, exp.Table] | None:
    tables = tuple(expression.find_all(exp.Table))
    if len(tables) != 1:
        return None
    table = tables[0]
    if isinstance(expression, exp.Select):
        return ("SELECT", table) if expression.expressions else None
    if isinstance(expression, exp.Insert):
        return (
            ("INSERT", table)
            if expression.args.get("expression") is not None
            else None
        )
    if isinstance(expression, exp.Update):
        return ("UPDATE", table) if expression.expressions else None
    if isinstance(expression, exp.Delete):
        return "DELETE", table
    return None


def _jpql_query_operation(
    expression: exp.Expression,
) -> tuple[str, exp.Table] | None:
    if any(
        expression.find(kind) is not None
        for kind in (exp.Join, exp.Subquery, exp.Union, exp.With)
    ):
        return None
    tables = tuple(expression.find_all(exp.Table))
    if len(tables) != 1:
        return None
    table = tables[0]
    if not _EVIDENCE_JAVA_IDENTIFIER.fullmatch(table.name):
        return None
    if isinstance(expression, exp.Select):
        projection = expression.expressions
        source = expression.args.get("from")
        if not isinstance(source, exp.From) or source.this is not table:
            return None
        # `FROM X WHERE ...` is valid JPQL shorthand for selecting X; sqlglot renders the
        # absent projection as a star. Spring Data uses this form in real repositories.
        if len(projection) == 1 and isinstance(projection[0], exp.Star):
            return "SELECT", table
        if len(projection) != 1 or not isinstance(projection[0], exp.Column):
            return None
        return "SELECT", table
    if isinstance(expression, exp.Update):
        return (
            ("UPDATE", table)
            if expression.this is table and expression.expressions
            else None
        )
    if isinstance(expression, exp.Delete):
        return ("DELETE", table) if expression.this is table else None
    return None


def _collapse_java_spring_candidates(
    candidates: list[_CompiledCandidate],
    context: JavaSpringEvidenceContext,
    framework_evidence: tuple[str, ...],
) -> tuple[JavaSpringLineageEdge, ...]:
    grouped: dict[
        tuple[str, str, str, str, str, str, str, str, str, str | None],
        list[_CompiledCandidate],
    ] = {}
    for candidate in candidates:
        key = (
            candidate.from_urn,
            candidate.to_urn,
            candidate.edge_type,
            candidate.transform,
            candidate.service_urn,
            candidate.dataset_urn,
            candidate.rationale,
            candidate.repository.identifier,
            candidate.entity.identifier,
            candidate.table.identifier,
            candidate.query.identifier if candidate.query is not None else None,
        )
        grouped.setdefault(key, []).append(candidate)

    edges: list[JavaSpringLineageEdge] = []
    for key, group in sorted(grouped.items(), key=lambda item: item[0]):
        first = group[0]
        invocation_facts = tuple(
            sorted(
                {item.invocation.identifier: item.invocation for item in group}.values(),
                key=lambda item: item.identifier,
            )
        )
        identity = json.dumps(
            {
                "origin": context.origin,
                "repository": context.repository,
                "revision": context.revision,
                "scopeDigest": context.scope_digest,
                "analyzerPack": context.analyzer_pack,
                "rulesetVersion": context.ruleset_version,
                "resolverVersion": context.resolver_version,
                "schemaProfile": context.schema_profile,
                "lineage": key,
                "invocations": [item.identifier for item in invocation_facts],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        stable_id = f"spring-sca-{hashlib.sha256(identity.encode()).hexdigest()}"
        evidence = JavaSpringEdgeEvidence(
            context.origin,
            context.repository,
            context.revision,
            context.scope_digest,
            context.analyzer_pack,
            context.ruleset_version,
            context.resolver_version,
            context.schema_profile,
            tuple(sorted(framework_evidence)),
            first.rationale,
            tuple(JavaSpringFactReference.from_fact(item) for item in invocation_facts),
            JavaSpringFactReference.from_fact(first.repository),
            JavaSpringFactReference.from_fact(first.entity),
            JavaSpringFactReference.from_fact(first.table),
            JavaSpringFactReference.from_fact(first.query) if first.query is not None else None,
        )
        edges.append(
            JavaSpringLineageEdge(
                stable_id,
                first.from_urn,
                first.to_urn,
                first.edge_type,
                first.transform,
                first.service_urn,
                first.dataset_urn,
                True,
                False,
                "SCA_STATIC",
                evidence,
            )
        )
    return tuple(
        sorted(
            edges,
            key=lambda item: (
                item.service_urn,
                item.dataset_urn,
                item.edge_type,
                item.transform,
                item.stable_id,
            ),
        )
    )


def _location_dict(location: SourceLocation) -> dict[str, object]:
    return {
        "path": location.path,
        "line": location.line,
        "startByte": location.start_byte,
        "endByte": location.end_byte,
        "astKind": location.ast_kind,
        "astPath": location.ast_path,
    }


def _bounded_java_fqn(value: str) -> bool:
    return len(value) <= 512 and all(
        _EVIDENCE_JAVA_IDENTIFIER.fullmatch(part) is not None
        for part in value.split(".")
    )


def _evidence_fact_attributes(
    fact: SyntaxFact,
) -> tuple[tuple[str, FactValue], ...]:
    allowed = {
        "java.invocation": {"arity", "method", "receiver", "repositoryType"},
        "spring.repository-association": {"baseFqn", "entityFqn"},
        "spring.entity-table": {
            "catalog",
            "catalogExplicit",
            "entityName",
            "explicit",
            "nameExplicit",
            "schema",
            "schemaExplicit",
            "table",
        },
        "spring.query": {"arity", "literal", "method", "nativeQuery", "parameterTypes"},
        "sql.table": {
            "catalog",
            "catalogQuoted",
            "dialect",
            "dialectMode",
            "nameQuoted",
            "schema",
            "schemaQuoted",
            "schemaSource",
        },
    }.get(fact.kind, set())
    return tuple(
        (name, value) for name, value in fact.attributes if name in allowed
    )


@dataclass(frozen=True, slots=True)
class _ParsedJava:
    source: JavaSpringSource
    tree: Tree
    package: str


@dataclass(frozen=True, slots=True)
class _GradleToken:
    kind: str
    value: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _BuildEvidence:
    path: str
    kind: str
    group: str
    artifact: str
    version: str
    inherited: bool = False

    @property
    def rendered(self) -> str:
        if self.kind == "boot-plugin" and not self.artifact:
            coordinate = f"{self.group}:{self.version}"
        else:
            coordinate = f"{self.group}:{self.artifact}:{self.version}"
        inherited = ":inherited" if self.inherited else ""
        return f"{self.path}:{self.kind}:{coordinate}{inherited}"


@dataclass(frozen=True, slots=True)
class _BuildClosure:
    path: str
    boot: tuple[_BuildEvidence, ...]
    jpa: tuple[_BuildEvidence, ...]
    relevant: bool
    invalid: bool
    # A multi-module Maven build splits its evidence: the aggregator declares the Boot
    # parent, each module declares the JPA dependency. These three fields are what let
    # the two halves be rejoined through the parent coordinates the module itself
    # states, without inferring anything from directory layout.
    coordinates: tuple[str, str] | None = None
    parent_coordinates: tuple[str, str] | None = None
    aggregator: bool = False


@dataclass(frozen=True, slots=True)
class _SymbolContext:
    explicit_imports: tuple[tuple[str, tuple[str, ...]], ...]
    wildcard_imports: tuple[str, ...]

    def imports_for(self, simple_name: str) -> tuple[str, ...]:
        return next(
            (values for name, values in self.explicit_imports if name == simple_name),
            (),
        )


@dataclass(frozen=True, slots=True)
class _JavaSymbolIndex:
    local_types: frozenset[str]
    contexts: tuple[tuple[str, _SymbolContext], ...]

    def context_for(self, path: str) -> _SymbolContext:
        return next(context for found_path, context in self.contexts if found_path == path)


@dataclass(frozen=True, slots=True)
class _AnnotationRecord:
    raw_name: str
    resolved_fqn: str | None
    literal_values: tuple[tuple[str, str], ...]
    node: Node
    dynamic_values: tuple[str, ...]


class JavaSpringScaAnalyzer:
    """Extracts bounded syntax facts from supplied bytes without running repository code."""

    def __init__(self, limits: JavaSpringScaLimits | None = None) -> None:
        self._limits = limits or JavaSpringScaLimits()
        self._parser = Parser(Language(tree_sitter_java.language()))
        self._facts: list[SyntaxFact] = []
        self._residue: list[AnalysisResidue] = []
        self._ast_paths: dict[str, dict[int, str]] = {}
        self._ast_nodes_indexed = 0

    def analyze(self, sources: Iterable[JavaSpringSource]) -> JavaSpringAnalysis:
        ordered = self._validate_sources(self._bounded_sources(sources))
        self._facts = []
        self._residue = []
        self._ast_paths = {}
        self._ast_nodes_indexed = 0
        framework = self._classify_framework(ordered)
        parsed_java: list[_ParsedJava] = []
        sql_files_parsed = 0

        migration_sources = tuple(
            source
            for source in ordered
            if is_flyway_migration(source.path) or is_liquibase_changelog(source.path)
        )
        for source in ordered:
            if source.path.endswith(".java"):
                parsed = self._parse_java(source)
                if parsed is not None:
                    parsed_java.append(parsed)
            elif source.path.endswith(".sql") and source not in migration_sources:
                if self._parse_sql(source):
                    sql_files_parsed += 1
        if migration_sources:
            sql_files_parsed += self._ingest_migrations(migration_sources)

        repository_types, symbols = self._extract_java_facts(parsed_java, framework)
        if framework.status == "supported":
            self._extract_repository_usage(parsed_java, repository_types, symbols)
            # Both passes have run, so entity fields can now be grounded in real columns.
            self._emit_entity_fields()
            self._emit_query_elements()

        facts = tuple(sorted(self._facts, key=lambda item: item.identifier))
        residue = tuple(
            sorted(
                self._residue,
                key=lambda item: (
                    item.location.path,
                    item.location.start_byte,
                    item.code,
                    item.symbol,
                ),
            )
        )
        return JavaSpringAnalysis(
            framework=framework,
            facts=facts,
            residue=residue,
            stats=AnalysisStats(
                files_seen=len(ordered),
                java_files_parsed=len(parsed_java),
                sql_files_parsed=sql_files_parsed,
                fact_count=len(facts),
                residue_count=len(residue),
                ast_nodes_indexed=self._ast_nodes_indexed,
            ),
        )

    def _ingest_migrations(self, sources: tuple[JavaSpringSource, ...]) -> int:
        """Replay ordered migrations and emit the same schema facts a schema.sql would.

        The replay is the schema; emitting per-statement facts would report tables and
        columns that later migrations removed. An incomplete replay emits nothing, so
        resolution fails closed on `missing-schema-table` rather than binding an entity
        to a schema the migrations do not actually produce.
        """
        migrations = tuple(
            MigrationSource(
                path=source.path,
                content=source.content,
                dialect=_SQLGLOT_DIALECTS[source.sql_dialect]
                if source.sql_dialect
                else "postgres",
                order=(),
            )
            for source in sources
        )
        changelogs = tuple(
            item for item in migrations if is_liquibase_changelog(item.path)
        )
        if changelogs:
            ordering_residue = ()
            schema = replay_liquibase(changelogs)
        else:
            ordered, ordering_residue = order_migrations(migrations)
            schema = replay_migrations(ordered)
        by_path = {source.path: source for source in sources}

        for entry in ordering_residue + schema.residue:
            source = by_path.get(entry.path)
            self._add_residue(
                entry.code,
                "migration schema replay could not model this input",
                entry.symbol,
                _whole_file_location(source, "sql_file")
                if source is not None
                else _scope_location(sources),
            )

        if not schema.complete:
            return 0

        dialect = migrations[0].dialect if migrations else "postgres"
        for table in schema.tables:
            source = by_path[table.path]
            self._add_fact(
                "sql.table",
                table.name,
                (
                    ("catalog", ""),
                    ("catalogQuoted", "false"),
                    ("dialect", dialect),
                    ("dialectMode", "native"),
                    ("nameQuoted", "false"),
                    ("schema", table.schema),
                    ("schemaQuoted", "false"),
                    ("schemaSource", "migration"),
                ),
                _whole_file_location(source, "table"),
            )
            for column in table.columns:
                column_source = by_path[column.path]
                self._add_fact(
                    "sql.column",
                    f"{table.name}.{column.name}",
                    (
                        ("column", column.name),
                        ("dataType", column.data_type),
                        ("dialect", dialect),
                        ("primaryKey", "true" if column.primary_key else "false"),
                        ("table", table.name),
                    ),
                    _whole_file_location(column_source, "table"),
                )
        return len(sources)

    def _bounded_sources(
        self, sources: Iterable[JavaSpringSource]
    ) -> tuple[JavaSpringSource, ...]:
        consumed: list[JavaSpringSource] = []
        for source in sources:
            if len(consumed) >= self._limits.max_files:
                raise JavaSpringAnalysisError("source file count limit exceeded")
            consumed.append(source)
        return tuple(consumed)

    def _validate_sources(
        self, sources: tuple[JavaSpringSource, ...]
    ) -> tuple[JavaSpringSource, ...]:
        if len(sources) > self._limits.max_files:
            raise JavaSpringAnalysisError("source file count limit exceeded")
        paths: set[str] = set()
        total_bytes = 0
        for source in sources:
            if not isinstance(source, JavaSpringSource):
                raise JavaSpringAnalysisError("sources must be JavaSpringSource records")
            _validate_source_path(source.path)
            if source.path in paths:
                raise JavaSpringAnalysisError("duplicate source path")
            paths.add(source.path)
            if not isinstance(source.content, bytes):
                raise JavaSpringAnalysisError("source content must be bytes")
            if len(source.content) > self._limits.max_file_bytes:
                raise JavaSpringAnalysisError("source per-file byte limit exceeded")
            total_bytes += len(source.content)
            if total_bytes > self._limits.max_total_bytes:
                raise JavaSpringAnalysisError("source total byte limit exceeded")
            # A Liquibase changelog is a schema source too: its `<sql>` escape hatch is
            # parsed with the profile's dialect, so it carries one legitimately.
            if source.path.endswith(".sql") or is_liquibase_changelog(source.path):
                if source.sql_dialect is not None and source.sql_dialect not in _SQLGLOT_DIALECTS:
                    raise JavaSpringAnalysisError("SQL dialect is not in the supported closed set")
            elif source.sql_dialect is not None:
                raise JavaSpringAnalysisError("SQL dialect is valid only for SQL sources")
        return tuple(sorted(sources, key=lambda item: item.path))

    def _classify_framework(
        self, sources: tuple[JavaSpringSource, ...]
    ) -> FrameworkClassification:
        closures: list[_BuildClosure] = []
        build_sources = [
            source
            for source in sources
            if PurePosixPath(source.path).name in {"pom.xml", "build.gradle", "build.gradle.kts"}
        ]
        for source in build_sources:
            try:
                text = source.content.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                self._add_residue(
                    "invalid-build-encoding",
                    "build metadata is not valid UTF-8",
                    "<invalid-utf8>",
                    _whole_file_location(source, "build_file"),
                )
                closures.append(_BuildClosure(source.path, (), (), True, True))
                continue
            if PurePosixPath(source.path).name == "pom.xml":
                closure = self._maven_framework_evidence(
                    source, text, _declared_parent_boot_versions(source, text, sources)
                )
            else:
                closure = self._gradle_framework_evidence(source, text)
            closures.append(closure)

        closures = _inherit_parent_build_evidence(closures)
        relevant = [closure for closure in closures if closure.relevant]
        if not relevant:
            location = (
                _whole_file_location(build_sources[0], "build_file")
                if build_sources
                else _scope_location(sources)
            )
            self._add_residue(
                "unknown-framework",
                "no supported literal Spring Boot and Spring Data JPA build closure was found",
                "spring-data-jpa",
                location,
            )
            return FrameworkClassification("unsupported", None, ())

        incomplete = False
        for closure in relevant:
            source = next(item for item in build_sources if item.path == closure.path)
            if not closure.boot:
                incomplete = True
                self._add_residue(
                    "missing-boot-evidence",
                    "Spring Data JPA requires a literal supported Spring Boot parent or plugin",
                    closure.path,
                    _whole_file_location(source, "build_file"),
                )
            if not closure.jpa and not closure.aggregator:
                incomplete = True
                self._add_residue(
                    "missing-jpa-dependency",
                    "Spring Boot support requires an active top-level Spring Data JPA dependency",
                    closure.path,
                    _whole_file_location(source, "build_file"),
                )
            incomplete = incomplete or closure.invalid
        if incomplete:
            return FrameworkClassification("unsupported", None, ())

        boot_versions = {item.version for closure in relevant for item in closure.boot}
        if len(boot_versions) != 1:
            self._add_residue(
                "ambiguous-framework-evidence",
                "build files declare conflicting literal Spring Boot versions",
                "/".join(sorted(boot_versions)),
                _whole_file_location(build_sources[0], "build_file"),
            )
            return FrameworkClassification("unsupported", None, ())
        boot_version = next(iter(boot_versions))
        boot_major = int(boot_version.split(".", 1)[0])
        for closure in relevant:
            for item in closure.jpa:
                if (
                    item.group == "org.springframework.data"
                    and item.artifact == "spring-data-jpa"
                ):
                    self._add_residue(
                        "unsupported-direct-spring-data-jpa",
                        "direct spring-data-jpa requires an explicit compatibility map",
                        item.rendered,
                        _whole_file_location(build_sources[0], "build_file"),
                    )
                    return FrameworkClassification("unsupported", None, ())
                if (
                    not item.inherited
                    and item.artifact == "spring-boot-starter-data-jpa"
                    and int(item.version.split(".", 1)[0]) != boot_major
                ):
                    self._add_residue(
                        "incompatible-framework-cell",
                        "explicit Boot starter Data JPA major must match Spring Boot major",
                        f"boot={boot_version};jpa={item.version}",
                        _whole_file_location(build_sources[0], "build_file"),
                    )
                    return FrameworkClassification("unsupported", None, ())
        jpa_versions: dict[tuple[str, str], set[str]] = {}
        for closure in relevant:
            for item in closure.jpa:
                jpa_versions.setdefault((item.group, item.artifact), set()).add(
                    item.version
                )
        if any(len(versions) != 1 for versions in jpa_versions.values()):
            self._add_residue(
                "ambiguous-framework-evidence",
                "build files declare conflicting literal Spring Data JPA versions",
                ";".join(
                    f"{group}:{artifact}={'/'.join(sorted(versions))}"
                    for (group, artifact), versions in sorted(jpa_versions.items())
                    if len(versions) != 1
                ),
                _whole_file_location(build_sources[0], "build_file"),
            )
            return FrameworkClassification("unsupported", None, ())
        evidence = {
            item.rendered
            for closure in relevant
            for item in (*closure.boot, *closure.jpa)
        }
        return FrameworkClassification(
            "supported", "spring-data-jpa", tuple(sorted(evidence))
        )

    def _maven_framework_evidence(
        self,
        source: JavaSpringSource,
        text: str,
        inherited_boot: frozenset[str] = frozenset(),
    ) -> _BuildClosure:
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            self._add_residue(
                "dynamic-framework-evidence",
                "Maven XML declarations outside the literal subset are forbidden",
                "DOCTYPE/ENTITY",
                _whole_file_location(source, "build_file"),
            )
            return _BuildClosure(source.path, (), (), True, True)
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            self._add_residue(
                "malformed-build-file",
                "Maven build metadata is malformed",
                "pom.xml",
                _whole_file_location(source, "build_file"),
            )
            return _BuildClosure(source.path, (), (), True, True)

        boot: list[_BuildEvidence] = []
        jpa: list[_BuildEvidence] = []
        relevant = False
        invalid = False
        own_values = _xml_values(root)
        parent = _xml_direct_child(root, "parent")
        parent_values = _xml_values(parent) if parent is not None else {}
        own_group = own_values.get("groupId") or parent_values.get("groupId", "")
        coordinates = (
            (own_group, own_values["artifactId"])
            if own_values.get("artifactId")
            else None
        )
        parent_coordinates = (
            (parent_values.get("groupId", ""), parent_values["artifactId"])
            if parent_values.get("artifactId")
            else None
        )
        aggregator = _xml_direct_child(root, "modules") is not None
        if parent is not None:
            values = _xml_values(parent)
            if (
                values.get("groupId") == "org.springframework.boot"
                and values.get("artifactId") == "spring-boot-starter-parent"
            ):
                relevant = True
                version = values.get("version", "")
                if self._supported_build_version(source, version):
                    boot.append(
                        _BuildEvidence(
                            source.path,
                            "boot-parent",
                            "org.springframework.boot",
                            "spring-boot-starter-parent",
                            version,
                        )
                    )
                else:
                    invalid = True

        build = _xml_direct_child(root, "build")
        plugins = _xml_direct_child(build, "plugins") if build is not None else None
        for plugin in _xml_direct_children(plugins, "plugin"):
            values = _xml_values(plugin)
            if (
                values.get("groupId", "org.springframework.boot")
                == "org.springframework.boot"
                and values.get("artifactId") == "spring-boot-maven-plugin"
            ):
                relevant = True
                version = values.get("version", "")
                inherited = False
                parent_versions = {
                    item.version for item in boot if item.kind == "boot-parent"
                }
                if not version and len(parent_versions) == 1:
                    version = next(iter(parent_versions))
                    inherited = True
                if self._supported_build_version(source, version):
                    boot.append(
                        _BuildEvidence(
                            source.path,
                            "boot-plugin",
                            "org.springframework.boot",
                            "spring-boot-maven-plugin",
                            version,
                            inherited,
                        )
                    )
                else:
                    invalid = True

        dependencies = _xml_direct_child(root, "dependencies")
        for dependency in _xml_direct_children(dependencies, "dependency"):
            values = _xml_values(dependency)
            group = values.get("groupId", "")
            artifact = values.get("artifactId", "")
            if (group, artifact) not in _SUPPORTED_COORDINATES:
                continue
            relevant = True
            if values.get("scope", "compile") not in {"", "compile", "runtime"}:
                continue
            if values.get("optional", "false").casefold() == "true":
                continue
            version = values.get("version", "")
            if any(_DYNAMIC_BUILD_TOKEN.search(value) for value in (group, artifact, version)):
                invalid = True
                self._add_residue(
                    "dynamic-framework-evidence",
                    "Spring framework Maven coordinates must be literal",
                    f"{group}:{artifact}:{version}",
                    _whole_file_location(source, "build_file"),
                )
                continue
            inherited = False
            if not version:
                # A module's versionless JPA dependency is managed by the Boot BOM its
                # aggregator declares, so the parent's version counts here too.
                boot_versions = {item.version for item in boot} or set(inherited_boot)
                if len(boot_versions) == 1:
                    version = next(iter(boot_versions))
                    inherited = True
                else:
                    invalid = True
                    self._add_residue(
                        "missing-jpa-version",
                        "versionless Maven JPA dependency requires one proven "
                        "Boot parent/plugin version",
                        f"{group}:{artifact}",
                        _whole_file_location(source, "build_file"),
                    )
                    continue
            if not _is_supported_version(version):
                invalid = True
                self._add_residue(
                    "unsupported-jpa-version",
                    "Spring Data JPA dependency version is outside the supported literal range",
                    version,
                    _whole_file_location(source, "build_file"),
                )
                continue
            jpa.append(
                _BuildEvidence(source.path, "data-jpa", group, artifact, version, inherited)
            )
        return _BuildClosure(
            source.path,
            tuple(boot),
            tuple(jpa),
            relevant or aggregator,
            invalid,
            coordinates,
            parent_coordinates,
            aggregator,
        )

    def _gradle_framework_evidence(
        self, source: JavaSpringSource, text: str
    ) -> _BuildClosure:
        boot: list[_BuildEvidence] = []
        jpa: list[_BuildEvidence] = []
        invalid = False
        tokens, malformed = _gradle_tokens(text)
        if malformed or _gradle_has_invalid_build_context(tokens):
            self._add_residue(
                "malformed-build-file",
                "Gradle build metadata is outside the bounded literal subset",
                PurePosixPath(source.path).name,
                _whole_file_location(source, "build_file"),
            )
            return _BuildClosure(source.path, (), (), True, True)
        plugins = tuple(
            item
            for item in _gradle_boot_plugins(tokens)
            if item[0].value == "org.springframework.boot"
        )
        for plugin_id, version_token in plugins:
            if version_token is None:
                invalid = True
                self._add_residue(
                    "missing-boot-version",
                    "Spring Boot Gradle plugin requires a literal semantic version",
                    plugin_id.value,
                    _text_location(source, plugin_id.start, plugin_id.end, "string_literal"),
                )
                continue
            if self._supported_build_version(source, version_token.value):
                boot.append(
                    _BuildEvidence(
                        source.path,
                        "boot-plugin",
                        "org.springframework.boot",
                        "",
                        version_token.value,
                    )
                )
            else:
                invalid = True
        for token in _gradle_literal_dependencies(tokens):
            coordinate = token.value.strip()
            parts = coordinate.split(":")
            if len(parts) < 2 or tuple(parts[:2]) not in _SUPPORTED_COORDINATES:
                continue
            if len(parts) > 3 or any(_DYNAMIC_BUILD_TOKEN.search(part) for part in parts):
                invalid = True
                self._add_residue(
                    "dynamic-framework-evidence",
                    "Spring framework Gradle coordinates must be literal",
                    coordinate,
                    _text_location(source, token.start, token.end, "string_literal"),
                )
                continue
            inherited = False
            if len(parts) == 2:
                boot_versions = {item.version for item in boot}
                if len(boot_versions) == 1:
                    parts.append(next(iter(boot_versions)))
                    inherited = True
                else:
                    invalid = True
                    self._add_residue(
                        "missing-jpa-version",
                        "versionless Gradle JPA dependency requires one proven "
                        "Boot plugin version",
                        coordinate,
                        _text_location(source, token.start, token.end, "string_literal"),
                    )
                    continue
            if len(parts) != 3 or not _is_supported_version(parts[2]):
                invalid = True
                self._add_residue(
                    "unsupported-jpa-version" if len(parts) == 3 else "missing-jpa-version",
                    "Gradle JPA dependency requires a supported literal semantic version",
                    coordinate,
                    _text_location(source, token.start, token.end, "string_literal"),
                )
                continue
            jpa.append(
                _BuildEvidence(
                    source.path,
                    "data-jpa",
                    parts[0],
                    parts[1],
                    parts[2],
                    inherited,
                )
            )
        relevant = bool(
            plugins
            or jpa
            or any(
                tuple(token.value.split(":")[:2]) in _SUPPORTED_COORDINATES
                for token in _gradle_literal_dependencies(tokens)
            )
        )
        return _BuildClosure(
            source.path, tuple(boot), tuple(jpa), relevant, invalid
        )

    def _supported_build_version(
        self, source: JavaSpringSource, version: str
    ) -> bool:
        if not version:
            self._add_residue(
                "missing-boot-version",
                "Spring Boot evidence requires a literal semantic version",
                "<missing>",
                _whole_file_location(source, "build_file"),
            )
            return False
        if _DYNAMIC_BUILD_TOKEN.search(version):
            self._add_residue(
                "dynamic-framework-evidence",
                "Spring Boot version must be literal",
                version,
                _whole_file_location(source, "build_file"),
            )
            return False
        if not _is_supported_version(version):
            self._add_residue(
                "unsupported-boot-version",
                "Spring Boot version must be >=3.0.0 and <5.0.0 without prerelease syntax",
                version,
                _whole_file_location(source, "build_file"),
            )
            return False
        return True

    def _parse_java(self, source: JavaSpringSource) -> _ParsedJava | None:
        try:
            source.content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            self._add_residue(
                "invalid-java-encoding",
                "Java source is not valid UTF-8",
                "<invalid-utf8>",
                _whole_file_location(source, "program"),
            )
            return None
        tree = self._parser.parse(source.content)
        self._index_ast_paths(source.path, tree.root_node)
        if tree.root_node.has_error:
            error = next(
                (node for node in _walk(tree.root_node) if node.is_error or node.is_missing),
                tree.root_node,
            )
            self._add_residue(
                "malformed-java",
                "Tree-sitter reported malformed Java syntax; the file was quarantined",
                f"{error.type}:{error.end_byte - error.start_byte}",
                self._node_location(source.path, error),
            )
            return None
        package_node = next(
            (
                child
                for child in tree.root_node.named_children
                if child.type == "package_declaration"
            ),
            None,
        )
        package = ""
        if package_node is not None and package_node.named_children:
            package = _node_text(package_node.named_children[0], source.content)
        return _ParsedJava(source, tree, package)

    def _index_ast_paths(self, path: str, root: Node) -> None:
        indexed: dict[int, str] = {}
        stack: list[tuple[Node, str]] = [(root, root.type)]
        while stack:
            node, ast_path = stack.pop()
            self._ast_nodes_indexed += 1
            if self._ast_nodes_indexed > self._limits.max_ast_nodes:
                raise JavaSpringAnalysisError("analysis AST node limit exceeded")
            indexed[node.id] = ast_path
            children = node.named_children
            for index in range(len(children) - 1, -1, -1):
                child = children[index]
                segment = f"{child.type}.named[{index}]"
                stack.append((child, _bounded_ast_path(ast_path, segment)))
        self._ast_paths[path] = indexed

    def _node_location(self, path: str, node: Node) -> SourceLocation:
        try:
            ast_path = self._ast_paths[path][node.id]
        except KeyError as error:
            raise JavaSpringAnalysisError("AST node is outside the indexed source tree") from error
        return SourceLocation(
            path,
            node.start_point.row + 1,
            node.start_byte,
            node.end_byte,
            node.type,
            ast_path,
        )

    def _extract_java_facts(
        self,
        parsed_files: list[_ParsedJava],
        framework: FrameworkClassification,
    ) -> tuple[set[str], _JavaSymbolIndex]:
        symbols = _build_java_symbol_index(parsed_files)
        repository_types: set[str] = set()
        # abstraction FQN -> {(entityFqn, entityType, idType, specialization, baseFqn)}
        abstractions: dict[str, set[tuple[str, str, str, str, str]]] = {}
        abstraction_sites: dict[str, SourceLocation] = {}
        for parsed in parsed_files:
            root = parsed.tree.root_node
            for child in root.named_children:
                if child.type == "package_declaration" and child.named_children:
                    self._add_fact(
                        "java.package",
                        _node_text(child.named_children[0], parsed.source.content),
                        (),
                        self._node_location(parsed.source.path, child),
                    )
                elif child.type == "import_declaration" and child.named_children:
                    imported = _java_import_text(child, parsed.source.content)
                    self._add_fact(
                        "java.import",
                        imported,
                        (),
                        self._node_location(parsed.source.path, child),
                    )

            for declaration in _type_declarations(root):
                simple_name_node = declaration.child_by_field_name("name")
                if simple_name_node is None:
                    continue
                qualified_name = _qualified_type_name(
                    declaration, parsed.package, parsed.source.content
                )
                extends, implements, generic_nodes, plain_nodes = _super_types(
                    declaration, parsed.source.content
                )
                attributes: list[tuple[str, FactValue]] = [
                    ("declarationKind", declaration.type.removesuffix("_declaration"))
                ]
                if extends:
                    attributes.append(("extends", extends))
                if implements:
                    attributes.append(("implements", implements))
                self._add_fact(
                    "java.type",
                    qualified_name,
                    attributes,
                    self._node_location(parsed.source.path, declaration),
                )
                annotations = self._emit_annotations(
                    parsed, declaration, qualified_name, symbols
                )
                if framework.status == "supported":
                    self._emit_entity_table(
                        parsed.source,
                        declaration,
                        qualified_name,
                        annotations,
                    )
                    for generic in generic_nodes:
                        association = _generic_parts(generic, parsed.source.content)
                        if association is None:
                            continue
                        base_type, arguments = association
                        base_fqn = self._resolve_java_symbol(
                            parsed, base_type, generic, symbols
                        )
                        if base_fqn not in _APPROVED_REPOSITORY_BASES:
                            continue
                        if declaration.type != "interface_declaration":
                            self._add_residue(
                                "invalid-repository-declaration",
                                "Spring Data repository association requires an interface declaration",
                                qualified_name,
                                self._node_location(parsed.source.path, declaration),
                            )
                            continue
                        if len(arguments) != 2:
                            self._add_residue(
                                "invalid-repository-generics",
                                "repository bases require exactly entity and identifier "
                                "generic arguments",
                                base_type,
                                self._node_location(parsed.source.path, generic),
                            )
                            continue
                        entity_fqn = self._resolve_java_symbol(
                            parsed, arguments[0], generic, symbols
                        )
                        if entity_fqn not in symbols.local_types:
                            self._add_residue(
                                "unresolved-repository-entity",
                                "repository entity generic must resolve to one local declaration",
                                arguments[0],
                                self._node_location(parsed.source.path, generic),
                            )
                            continue
                        repository_types.add(qualified_name)
                        self._add_fact(
                            "spring.repository-association",
                            qualified_name,
                            (
                                ("baseFqn", base_fqn),
                                ("baseType", base_fqn.rsplit(".", 1)[-1]),
                                ("entityFqn", entity_fqn),
                                ("entityType", arguments[0]),
                                ("idType", arguments[1]),
                            ),
                            self._node_location(parsed.source.path, generic),
                        )
                        # A specialization usually also extends a plain project interface
                        # -- the type services actually inject. Record the candidate so
                        # the abstraction can inherit this entity once every
                        # specialization has been seen.
                        for plain in plain_nodes:
                            abstraction_fqn = self._resolve_java_symbol(
                                parsed,
                                _node_text(plain, parsed.source.content),
                                plain,
                                symbols,
                            )
                            if (
                                abstraction_fqn is None
                                or abstraction_fqn not in symbols.local_types
                            ):
                                continue
                            abstractions.setdefault(abstraction_fqn, set()).add(
                                (
                                    entity_fqn,
                                    arguments[0],
                                    arguments[1],
                                    qualified_name,
                                    base_fqn,
                                )
                            )
                            abstraction_sites.setdefault(
                                abstraction_fqn,
                                self._node_location(parsed.source.path, plain),
                            )
                self._emit_members(
                    parsed,
                    declaration,
                    qualified_name,
                    symbols,
                    framework_supported=framework.status == "supported",
                )

        self._resolve_repository_abstractions(
            repository_types, abstractions, abstraction_sites
        )
        return repository_types, symbols

    def _emit_entity_fields(self) -> None:
        """Ground each entity field in a real schema column — the element level.

        Runs after both the Java and SQL passes so the join can be proven rather than
        assumed. A field only becomes an element when its column actually exists in the
        table the entity maps to: `@Transient` fields, relations and computed members
        simply have no column and are left out instead of invented.
        """
        columns: dict[str, set[str]] = {}
        for fact in self._facts:
            if fact.kind == "sql.column":
                columns.setdefault(dict(fact.attributes)["table"], set()).add(
                    dict(fact.attributes)["column"]
                )
        if not columns:
            return
        entity_tables = {
            fact.subject: dict(fact.attributes)["table"]
            for fact in self._facts
            if fact.kind == "spring.entity-table"
        }
        if not entity_tables:
            return

        # An entity commonly keeps its columns on a @MappedSuperclass, so a field
        # declared on an ancestor belongs to the entity's table too. The chain is
        # resolved by simple name against declared types; an ambiguous name is skipped
        # rather than picked.
        by_simple_name: dict[str, list[str]] = {}
        supertypes: dict[str, tuple[str, ...]] = {}
        for fact in self._facts:
            if fact.kind != "java.type":
                continue
            by_simple_name.setdefault(fact.subject.rsplit(".", 1)[-1], []).append(
                fact.subject
            )
            extends = dict(fact.attributes).get("extends")
            if isinstance(extends, tuple):
                supertypes[fact.subject] = tuple(
                    str(item).split("<", 1)[0].strip() for item in extends
                )

        # A @MappedSuperclass is mapped into *each* inheriting entity's table, so one
        # declaring class can legitimately belong to several entities: Person supplies
        # last_name to both owners and vets. Each pairing is emitted independently.
        owning_entities: dict[str, list[tuple[str, str]]] = {
            entity: [(entity, table)] for entity, table in entity_tables.items()
        }
        for entity, table in sorted(entity_tables.items()):
            frontier = [entity]
            seen: set[str] = set()
            while frontier:
                current = frontier.pop()
                for raw in supertypes.get(current, ()):
                    candidates = by_simple_name.get(raw.rsplit(".", 1)[-1], [])
                    if len(candidates) != 1:
                        continue
                    ancestor = candidates[0]
                    if ancestor in entity_tables or ancestor in seen:
                        continue
                    seen.add(ancestor)
                    owners = owning_entities.setdefault(ancestor, [])
                    if (entity, table) not in owners:
                        owners.append((entity, table))
                    frontier.append(ancestor)

        for fact in list(self._facts):
            if fact.kind != "java.field" or "#" not in fact.subject:
                continue
            declaring, field = fact.subject.rsplit("#", 1)
            for owner, table in owning_entities.get(declaring, ()):
                available = columns.get(table, set())
                attributes = dict(fact.attributes)
                explicit = attributes.get("column")
                if explicit is not None:
                    if explicit not in available:
                        # An explicit @Column naming a column the schema does not have is a
                        # real contradiction, not a field to skip.
                        self._add_residue(
                            "unmapped-entity-column",
                            "explicit @Column name is absent from the mapped table",
                            f"{table}.{explicit}",
                            fact.location,
                        )
                        continue
                    column, mapping = explicit, "explicit"
                else:
                    candidate = _snake_case(field)
                    if candidate not in available:
                        continue
                    column, mapping = candidate, "convention"
                self._add_fact(
                    "spring.entity-field",
                    f"{owner}#{field}",
                    (
                        ("column", column),
                        ("entity", owner),
                        ("field", field),
                        ("mapping", mapping),
                        ("table", table),
                    ),
                    fact.location,
                )

    def _emit_query_elements(self) -> None:
        """Resolve each repository method to the columns it actually touches.

        Two provable sources: a derived query method name, and a literal JPQL body. Both
        are resolved through the entity's proven field-to-column mappings, so a property
        that does not correspond to a real column is quarantined rather than invented.
        """
        fields_by_entity: dict[str, dict[str, tuple[str, str]]] = {}
        for fact in self._facts:
            if fact.kind != "spring.entity-field":
                continue
            attributes = dict(fact.attributes)
            fields_by_entity.setdefault(attributes["entity"], {})[attributes["field"]] = (
                attributes["column"],
                attributes["table"],
            )
        if not fields_by_entity:
            return
        entity_by_repository = {
            fact.subject: dict(fact.attributes)["entityFqn"]
            for fact in self._facts
            if fact.kind == "spring.repository-association"
        }
        queries = {
            fact.subject: dict(fact.attributes)["query"]
            for fact in self._facts
            if fact.kind == "spring.query"
        }

        for fact in list(self._facts):
            if fact.kind != "java.method" or "#" not in fact.subject:
                continue
            repository, signature = fact.subject.rsplit("#", 1)
            entity = entity_by_repository.get(repository)
            if entity is None:
                continue
            fields = fields_by_entity.get(entity)
            if not fields:
                continue
            method = signature.split("(", 1)[0]
            query = queries.get(fact.subject)
            if query is not None:
                properties: tuple[str, ...] | None = _jpql_properties(query)
                derivation = "jpql"
            else:
                properties = _derived_properties(method)
                derivation = "derived"
            if not properties:
                continue
            role = (
                "projection"
                if derivation == "jpql"
                else "predicate"
            )
            for prop in properties:
                mapped = fields.get(prop)
                if mapped is None:
                    self._add_residue(
                        "unresolved-query-property",
                        "query property does not map to a proven schema column",
                        f"{method}:{prop}",
                        fact.location,
                    )
                    continue
                column, table = mapped
                self._add_fact(
                    "spring.query-element",
                    f"{repository}#{method}:{column}",
                    (
                        ("column", column),
                        ("derivation", derivation),
                        ("entity", entity),
                        ("method", method),
                        ("property", prop),
                        ("repository", repository),
                        ("role", role),
                        ("table", table),
                    ),
                    fact.location,
                )

    def _resolve_repository_abstractions(
        self,
        repository_types: set[str],
        abstractions: dict[str, set[tuple[str, str, str, str, str]]],
        abstraction_sites: dict[str, SourceLocation],
    ) -> None:
        """Let a plain interface inherit the entity of its Spring Data specialization.

        Services frequently inject an abstraction rather than the Spring Data type, so
        without this the receiver is unbound and no lineage is produced at all. The
        inheritance only happens when it is unambiguous: if two specializations of the
        same abstraction disagree on the entity, Spring's choice is a runtime decision
        and the abstraction is quarantined rather than guessed.
        """
        for abstraction_fqn, candidates in sorted(abstractions.items()):
            if abstraction_fqn in repository_types:
                # Already a repository in its own right; nothing to inherit.
                continue
            entities = {candidate[0] for candidate in candidates}
            location = abstraction_sites[abstraction_fqn]
            if len(entities) != 1:
                self._add_residue(
                    "ambiguous-repository-abstraction",
                    "specializations disagree on the entity behind the injected abstraction",
                    abstraction_fqn,
                    location,
                )
                continue
            entity_fqn, entity_type, id_type, specialization, base_fqn = sorted(
                candidates
            )[0]
            repository_types.add(abstraction_fqn)
            self._add_fact(
                "spring.repository-association",
                abstraction_fqn,
                (
                    ("baseFqn", base_fqn),
                    ("baseType", base_fqn.rsplit(".", 1)[-1]),
                    ("entityFqn", entity_fqn),
                    ("entityType", entity_type),
                    ("idType", id_type),
                    ("viaAbstraction", "true"),
                    ("specializedBy", specialization),
                ),
                location,
            )

    def _emit_annotations(
        self,
        parsed: _ParsedJava,
        target: Node,
        target_subject: str,
        symbols: _JavaSymbolIndex,
    ) -> tuple[_AnnotationRecord, ...]:
        annotations: list[_AnnotationRecord] = []
        modifiers = next(
            (child for child in target.named_children if child.type == "modifiers"), None
        )
        if modifiers is None:
            return ()
        for annotation in (
            child
            for child in modifiers.named_children
            if child.type in {"annotation", "marker_annotation"}
        ):
            name_node = annotation.child_by_field_name("name")
            if name_node is None:
                continue
            name = _node_text(name_node, parsed.source.content)
            resolved = self._resolve_java_symbol(parsed, name, annotation, symbols)
            ignored_keys = (
                _TABLE_IGNORED_ATTRIBUTE_KEYS if resolved == _TABLE_FQN else frozenset()
            )
            literal_values, dynamic_values = _annotation_values(
                annotation, parsed.source.content, ignored_keys
            )
            attributes: list[tuple[str, FactValue]] = list(literal_values)
            if resolved is not None:
                attributes.append(("resolvedFqn", resolved))
            if dynamic_values:
                attributes.append(("dynamicValues", dynamic_values))
            self._add_fact(
                "java.annotation",
                f"{target_subject}:@{name}",
                attributes,
                self._node_location(parsed.source.path, annotation),
            )
            annotations.append(
                _AnnotationRecord(
                    name, resolved, literal_values, annotation, dynamic_values
                )
            )
        return tuple(annotations)

    def _emit_entity_table(
        self,
        source: JavaSpringSource,
        declaration: Node,
        qualified_name: str,
        annotations: tuple[_AnnotationRecord, ...],
    ) -> None:
        entities = tuple(
            item for item in annotations if item.resolved_fqn == _ENTITY_FQN
        )
        if not entities:
            return
        if len(entities) != 1:
            self._add_residue(
                "ambiguous-entity-name",
                "entity declaration has multiple exact jakarta.persistence.Entity annotations",
                qualified_name,
                self._node_location(source.path, declaration),
            )
            return
        entity = entities[0]
        if entity.dynamic_values:
            self._add_residue(
                "dynamic-entity-name",
                "entity name must be an absent or bounded string literal",
                entity.dynamic_values[0],
                self._node_location(source.path, entity.node),
            )
            return
        entity_values = dict(entity.literal_values)
        entity_name = entity_values.get("name", qualified_name.rsplit(".", 1)[-1])
        if _EVIDENCE_JAVA_IDENTIFIER.fullmatch(entity_name) is None:
            self._add_residue(
                "invalid-entity-name",
                "entity name is outside the bounded JPQL identifier set",
                entity_name,
                self._node_location(source.path, entity.node),
            )
            return
        syntactic_table = next(
            (item for item in annotations if _simple_type(item.raw_name) == "Table"),
            None,
        )
        table_values: dict[str, str] = {}
        location = self._node_location(source.path, declaration)
        if syntactic_table is not None:
            if syntactic_table.resolved_fqn != _TABLE_FQN:
                self._add_residue(
                    "unresolved-table-mapping",
                    "present @Table annotation did not resolve to jakarta.persistence.Table",
                    syntactic_table.raw_name,
                    self._node_location(source.path, syntactic_table.node),
                )
                return
            table_values = dict(syntactic_table.literal_values)
            if syntactic_table.dynamic_values:
                self._add_residue(
                    "dynamic-table-mapping",
                    "present @Table name is dynamic and cannot default safely",
                    syntactic_table.dynamic_values[0],
                    self._node_location(source.path, syntactic_table.node),
                )
                return
            location = self._node_location(source.path, syntactic_table.node)
        table_name = table_values.get("name", qualified_name.rsplit(".", 1)[-1])
        if not table_name:
            self._add_residue(
                "dynamic-table-mapping",
                "explicit table name must be a non-empty bounded literal",
                table_name,
                location,
            )
            return
        self._add_fact(
            "spring.entity-table",
            qualified_name,
            (
                ("catalog", table_values.get("catalog", "")),
                ("catalogExplicit", "true" if "catalog" in table_values else "false"),
                ("entityName", entity_name),
                ("explicit", "true" if "name" in table_values else "false"),
                ("nameExplicit", "true" if "name" in table_values else "false"),
                ("schema", table_values.get("schema", "")),
                ("schemaExplicit", "true" if "schema" in table_values else "false"),
                ("table", table_name),
            ),
            location,
        )

    def _emit_members(
        self,
        parsed: _ParsedJava,
        declaration: Node,
        owner: str,
        symbols: _JavaSymbolIndex,
        *,
        framework_supported: bool,
    ) -> None:
        body = declaration.child_by_field_name("body")
        if body is None:
            return
        for member in body.named_children:
            if member.type == "field_declaration":
                field_type = member.child_by_field_name("type")
                declarators = [
                    child
                    for child in member.named_children
                    if child.type == "variable_declarator"
                    and child.child_by_field_name("name") is not None
                ]
                if field_type is None or not declarators:
                    continue
                names = [
                    _node_text(
                        declarator.child_by_field_name("name"), parsed.source.content
                    )
                    for declarator in declarators
                ]
                # Annotations belong to the declaration, so they are resolved once and
                # attributed to its first declarator rather than emitted per name.
                column_override = _column_override(
                    self._emit_annotations(
                        parsed, member, f"{owner}#{names[0]}", symbols
                    )
                )
                for declarator, name in zip(declarators, names):
                    attributes: list[tuple[str, FactValue]] = [
                        ("type", _node_text(field_type, parsed.source.content))
                    ]
                    if column_override is not None:
                        attributes.append(("column", column_override))
                    self._add_fact(
                        "java.field",
                        f"{owner}#{name}",
                        tuple(attributes),
                        self._node_location(parsed.source.path, declarator),
                    )
            elif member.type == "constructor_declaration":
                self._add_fact(
                    "java.constructor",
                    owner,
                    (("parameters", _parameter_signature(member, parsed.source.content)),),
                    self._node_location(parsed.source.path, member),
                )
                self._emit_annotations(
                    parsed, member, f"{owner}#<init>", symbols
                )
            elif member.type == "method_declaration":
                name_node = member.child_by_field_name("name")
                if name_node is None:
                    continue
                method_name = _node_text(name_node, parsed.source.content)
                return_type = member.child_by_field_name("type")
                parameter_types = _canonical_parameter_types(
                    member, parsed.source.content
                )
                subject = f"{owner}#{method_name}({','.join(parameter_types)})"
                declaration_mode = _method_declaration_mode(
                    declaration, member, parsed.source.content
                )
                self._add_fact(
                    "java.method",
                    subject,
                    (
                        (
                            "returnType",
                            _node_text(return_type, parsed.source.content)
                            if return_type is not None
                            else "void",
                        ),
                        ("arity", str(len(parameter_types))),
                        ("declarationMode", declaration_mode),
                        ("name", method_name),
                        ("parameterTypes", parameter_types),
                        ("parameters", _parameter_signature(member, parsed.source.content)),
                    ),
                    self._node_location(parsed.source.path, member),
                )
                annotations = self._emit_annotations(
                    parsed, member, subject, symbols
                )
                query = next(
                    (item for item in annotations if item.resolved_fqn == _QUERY_FQN),
                    None,
                )
                if query is not None and framework_supported:
                    values = dict(query.literal_values)
                    query_value = values.get("value")
                    if query_value is not None:
                        query_attributes: list[tuple[str, FactValue]] = [
                            ("arity", str(len(parameter_types))),
                            ("literal", "true"),
                            ("method", method_name),
                            ("parameterTypes", parameter_types),
                            ("query", query_value),
                        ]
                        if "nativeQuery" in values:
                            query_attributes.append(
                                ("nativeQuery", values["nativeQuery"])
                            )
                        self._add_fact(
                            "spring.query",
                            subject,
                            query_attributes,
                            self._node_location(parsed.source.path, query.node),
                        )
                    else:
                        symbol = (
                            query.dynamic_values[0]
                            if query.dynamic_values
                            else "<missing>"
                        )
                        self._add_residue(
                            "dynamic-query",
                            "Spring @Query text is not a string literal",
                            symbol,
                            self._node_location(parsed.source.path, query.node),
                        )

    def _resolve_java_symbol(
        self,
        parsed: _ParsedJava,
        raw_name: str,
        node: Node,
        symbols: _JavaSymbolIndex,
    ) -> str | None:
        simple_name = _simple_type(raw_name)
        location = self._node_location(parsed.source.path, node)
        if "." in raw_name:
            if (
                raw_name in _APPROVED_FRAMEWORK_SYMBOLS
                and raw_name in symbols.local_types
            ):
                self._add_residue(
                    "shadowed-framework-symbol",
                    "repository-local declaration shadows an approved framework FQN",
                    simple_name,
                    location,
                )
                return None
            if raw_name in symbols.local_types or raw_name in _APPROVED_FRAMEWORK_SYMBOLS:
                return raw_name
            same_package = f"{parsed.package}.{raw_name}" if parsed.package else raw_name
            if same_package in symbols.local_types:
                return same_package
            if simple_name in _SENSITIVE_FRAMEWORK_NAMES:
                self._add_residue(
                    "unresolved-framework-symbol",
                    "framework-sensitive symbol is not an approved exact FQN",
                    simple_name,
                    location,
                )
            return None

        same_package = (
            f"{parsed.package}.{simple_name}" if parsed.package else simple_name
        )
        if same_package in symbols.local_types:
            if simple_name in _SENSITIVE_FRAMEWORK_NAMES:
                self._add_residue(
                    "shadowed-framework-symbol",
                    "local declaration shadows a framework-sensitive symbol",
                    simple_name,
                    location,
                )
            return same_package
        context = symbols.context_for(parsed.source.path)
        imports = context.imports_for(simple_name)
        if len(imports) > 1:
            if simple_name in _SENSITIVE_FRAMEWORK_NAMES:
                self._add_residue(
                    "ambiguous-framework-symbol",
                    "multiple explicit imports bind a framework-sensitive simple name",
                    simple_name,
                    location,
                )
            return None
        if len(imports) == 1:
            resolved = imports[0]
            if (
                resolved in _APPROVED_FRAMEWORK_SYMBOLS
                and resolved in symbols.local_types
            ):
                self._add_residue(
                    "shadowed-framework-symbol",
                    "repository-local declaration shadows an approved framework FQN",
                    simple_name,
                    location,
                )
                return None
            if (
                simple_name in _SENSITIVE_FRAMEWORK_NAMES
                and resolved not in _APPROVED_FRAMEWORK_SYMBOLS
            ):
                self._add_residue(
                    "unresolved-framework-symbol",
                    "framework-sensitive import is not in the approved FQN set",
                    simple_name,
                    location,
                )
                return None
            return resolved
        if context.wildcard_imports and simple_name in _SENSITIVE_FRAMEWORK_NAMES:
            # An on-demand import can still be provable *within the closed approved set*:
            # if exactly one approved framework package is wildcard-imported here and it
            # is the only approved package declaring this simple name, no other approved
            # symbol can be in play. Two such packages — `Repository` is declared by both
            # `org.springframework.data.repository` and `org.springframework.stereotype`
            # — remain a real ambiguity, and a package outside the approved set proves
            # nothing at all. This is what lets an unmodified `import jakarta.persistence.*`
            # resolve without widening the approved set itself.
            approved = sorted(
                fqn
                for fqn in _APPROVED_FRAMEWORK_SYMBOLS
                if fqn.rsplit(".", 1)[-1] == simple_name
                and fqn.rsplit(".", 1)[0] in context.wildcard_imports
            )
            shadowing = [
                f"{package}.{simple_name}"
                for package in context.wildcard_imports
                if f"{package}.{simple_name}" in symbols.local_types
            ]
            if len(approved) == 1 and not shadowing:
                return approved[0]
            self._add_residue(
                "wildcard-framework-symbol",
                "wildcard imports cannot prove a framework-sensitive symbol",
                simple_name,
                location,
            )
            return None
        if context.wildcard_imports:
            # A *local* name is different: the tracked scope is closed, so if exactly one
            # wildcard-imported package declares this simple name in the snapshot, no
            # other declaration can be in play. Two candidates are a real ambiguity.
            candidates = sorted(
                {
                    f"{package}.{simple_name}"
                    for package in context.wildcard_imports
                    if f"{package}.{simple_name}" in symbols.local_types
                }
            )
            if len(candidates) > 1:
                self._add_residue(
                    "ambiguous-wildcard-symbol",
                    "multiple wildcard imports declare the same local simple name",
                    simple_name,
                    location,
                )
                return None
            if len(candidates) == 1:
                return candidates[0]
        if simple_name in {
            "Boolean",
            "Byte",
            "Character",
            "Double",
            "Float",
            "Integer",
            "Long",
            "Short",
            "String",
        }:
            return f"java.lang.{simple_name}"
        return None

    def _extract_repository_usage(
        self,
        parsed_files: list[_ParsedJava],
        repository_types: set[str],
        symbols: _JavaSymbolIndex,
    ) -> None:
        if not repository_types:
            return
        for parsed in parsed_files:
            for declaration in _type_declarations(parsed.tree.root_node):
                name_node = declaration.child_by_field_name("name")
                body = declaration.child_by_field_name("body")
                if name_node is None or body is None:
                    continue
                owner = _qualified_type_name(
                    declaration, parsed.package, parsed.source.content
                )
                fields: dict[str, str] = {}
                for member in body.named_children:
                    if member.type != "field_declaration":
                        continue
                    field_type = member.child_by_field_name("type")
                    if field_type is None:
                        continue
                    type_name = self._resolve_java_symbol(
                        parsed,
                        _node_text(field_type, parsed.source.content),
                        field_type,
                        symbols,
                    )
                    if type_name not in repository_types:
                        continue
                    for declarator in (
                        child
                        for child in member.named_children
                        if child.type == "variable_declarator"
                    ):
                        field_name = declarator.child_by_field_name("name")
                        if field_name is not None:
                            fields[_node_text(field_name, parsed.source.content)] = type_name

                bound_fields: set[str] = set()
                # Field injection (`@Autowired`/`@Inject` directly on the field) is just
                # as statically provable as a constructor parameter: the declared type is
                # fixed at compile time, so the same binding semantics apply. Setter
                # injection is deliberately excluded -- the annotation sits on the method,
                # not the field, so this loop never sees it.
                for member in body.named_children:
                    if member.type != "field_declaration":
                        continue
                    field_type = member.child_by_field_name("type")
                    if field_type is None:
                        continue
                    type_name = self._resolve_java_symbol(
                        parsed,
                        _node_text(field_type, parsed.source.content),
                        field_type,
                        symbols,
                    )
                    if type_name not in repository_types:
                        continue
                    if not any(
                        self._has_resolved_annotation(parsed, member, symbols, fqn)
                        for fqn in _FIELD_INJECTION_FQNS
                    ):
                        continue
                    for declarator in (
                        child
                        for child in member.named_children
                        if child.type == "variable_declarator"
                    ):
                        field_name_node = declarator.child_by_field_name("name")
                        if field_name_node is None:
                            continue
                        field_name = _node_text(field_name_node, parsed.source.content)
                        bound_fields.add(field_name)
                        self._add_fact(
                            "spring.repository-binding",
                            f"{owner}#{field_name}",
                            (
                                ("field", field_name),
                                ("injectionMode", "field"),
                                ("parameter", ""),
                                ("repositoryType", type_name),
                            ),
                            self._node_location(parsed.source.path, member),
                        )
                constructors = tuple(
                    member
                    for member in body.named_children
                    if member.type == "constructor_declaration"
                )
                autowired_constructors = tuple(
                    constructor
                    for constructor in constructors
                    if self._has_resolved_annotation(
                        parsed, constructor, symbols, _AUTOWIRED_FQN
                    )
                )
                if len(constructors) == 1:
                    eligible_constructors = constructors
                    injection_mode = "single-constructor"
                elif len(autowired_constructors) == 1:
                    eligible_constructors = autowired_constructors
                    injection_mode = "autowired-constructor"
                else:
                    eligible_constructors = ()
                    injection_mode = "unproven"
                if len(constructors) > 1 and not eligible_constructors and fields:
                    self._add_residue(
                        "ambiguous-repository-injection",
                        "multiple constructors do not prove which repository "
                        "injection path Spring selects",
                        owner,
                        self._node_location(parsed.source.path, declaration),
                    )
                for member in eligible_constructors:
                    if member.type == "constructor_declaration":
                        raw_parameters = _parameters(member, parsed.source.content)
                        parameters = {
                            name: self._resolve_java_symbol(
                                parsed, raw_type, member, symbols
                            )
                            for name, raw_type in raw_parameters.items()
                        }
                        for assignment in (
                            node
                            for node in _walk(member)
                            if node.type == "assignment_expression"
                        ):
                            binding = _constructor_binding(
                                assignment, parsed.source.content, parameters, fields
                            )
                            if binding is None or binding[2] not in repository_types:
                                continue
                            field_name, parameter_name, repository_type = binding
                            bound_fields.add(field_name)
                            self._add_fact(
                                "spring.repository-binding",
                                f"{owner}#{field_name}",
                                (
                                    ("field", field_name),
                                    ("injectionMode", injection_mode),
                                    ("parameter", parameter_name),
                                    ("repositoryType", repository_type),
                                ),
                                self._node_location(parsed.source.path, assignment),
                            )
                for member in body.named_children:
                    if member.type == "method_declaration":
                        method_name_node = member.child_by_field_name("name")
                        if method_name_node is None:
                            continue
                        enclosing_method = _node_text(
                            method_name_node, parsed.source.content
                        )
                        binder_ranges = _lexical_binder_ranges(
                            member, parsed.source.content
                        )
                        for invocation in (
                            node
                            for node in _walk(member)
                            if node.type == "method_invocation"
                        ):
                            receiver_node = invocation.child_by_field_name("object")
                            called_node = invocation.child_by_field_name("name")
                            receiver_info = _receiver_name(
                                receiver_node, parsed.source.content
                            )
                            if (
                                receiver_info is None
                                or called_node is None
                            ):
                                continue
                            receiver, explicit_field = receiver_info
                            if fields.get(receiver) not in repository_types:
                                continue
                            if not explicit_field and _is_lexically_shadowed(
                                binder_ranges,
                                receiver,
                                receiver_node.start_byte,
                            ):
                                self._add_residue(
                                    "shadowed-repository-receiver",
                                    "method-local symbol shadows the bound repository field",
                                    receiver,
                                    self._node_location(parsed.source.path, invocation),
                                )
                                continue
                            if receiver not in bound_fields:
                                self._add_residue(
                                    "unbound-repository-receiver",
                                    "repository receiver lacks a proven injection binding",
                                    receiver,
                                    self._node_location(parsed.source.path, invocation),
                                )
                                continue
                            called = _node_text(called_node, parsed.source.content)
                            arguments = invocation.child_by_field_name("arguments")
                            arity = len(arguments.named_children) if arguments else 0
                            self._add_fact(
                                "java.invocation",
                                f"{owner}#{enclosing_method}:{receiver}.{called}",
                                (
                                    ("arity", str(arity)),
                                    ("method", called),
                                    ("receiver", receiver),
                                    ("repositoryType", fields[receiver]),
                                ),
                                self._node_location(parsed.source.path, invocation),
                            )

    def _has_resolved_annotation(
        self,
        parsed: _ParsedJava,
        target: Node,
        symbols: _JavaSymbolIndex,
        expected_fqn: str,
    ) -> bool:
        modifiers = next(
            (child for child in target.named_children if child.type == "modifiers"),
            None,
        )
        if modifiers is None:
            return False
        for annotation in modifiers.named_children:
            if annotation.type not in {"annotation", "marker_annotation"}:
                continue
            name = annotation.child_by_field_name("name")
            if name is None:
                continue
            resolved = self._resolve_java_symbol(
                parsed, _node_text(name, parsed.source.content), annotation, symbols
            )
            if resolved == expected_fqn:
                return True
        return False

    def _parse_sql(self, source: JavaSpringSource) -> bool:
        if source.sql_dialect is None:
            self._add_residue(
                "missing-sql-dialect",
                "SQL parsing requires an explicit supported dialect",
                source.path,
                _whole_file_location(source, "sql_file"),
            )
            return False
        try:
            text = source.content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            self._add_residue(
                "invalid-sql-encoding",
                "SQL source is not valid UTF-8",
                "<invalid-utf8>",
                _whole_file_location(source, "sql_file"),
            )
            return False
        sqlglot_dialect = _SQLGLOT_DIALECTS[source.sql_dialect]
        try:
            dialect = Dialect.get_or_raise(sqlglot_dialect)
            tokens = dialect.tokenizer().tokenize(text)
        except (ValueError, TokenError):
            self._add_residue(
                "malformed-sql",
                "SQLGlot rejected malformed or unsupported SQL",
                source.path,
                _whole_file_location(source, "sql_file"),
            )
            return False

        complete = True
        for statement_tokens in _sql_statement_tokens(tokens):
            if _is_session_statement_tokens(statement_tokens):
                # `CREATE DATABASE` and `USE` set up a session; they declare no table and
                # carry no lineage, so they are inventory-only rather than a reason to
                # reject the file. Real MySQL schema dumps open with both.
                self._add_residue(
                    "ignored-schema-statement",
                    "session statements declare no table and carry no lineage",
                    statement_tokens[0].text,
                    _sql_token_location(
                        source.path, text, statement_tokens[0], "sql_statement"
                    ),
                )
                continue
            if not _is_create_table_tokens(statement_tokens):
                is_index = _is_create_index_tokens(statement_tokens)
                valid_index = is_index and _valid_create_index_statement(
                    dialect, text, statement_tokens
                )
                if is_index and not valid_index:
                    complete = False
                self._add_residue(
                    "ignored-schema-statement"
                    if valid_index
                    else "malformed-sql"
                    if is_index
                    else "unsupported-sql",
                    "schema indexes are inventory-only and do not affect table identity"
                    if valid_index
                    else "CREATE INDEX must be structurally valid to be ignored"
                    if is_index
                    else "only explicit CREATE TABLE DDL yields schema facts",
                    statement_tokens[0].text,
                    _sql_token_location(
                        source.path, text, statement_tokens[0], "sql_statement"
                    ),
                )
                continue
            identifier_status = _sql_table_identifier_status(statement_tokens)
            if identifier_status != "concrete":
                complete = False
                self._add_residue(
                    "dynamic-sql-identifier"
                    if identifier_status == "dynamic"
                    else "malformed-sql",
                    "CREATE TABLE target must be a concrete identifier",
                    source.path,
                    _sql_token_location(
                        source.path, text, statement_tokens[0], "sql_statement"
                    ),
                )
                continue
            if source.sql_dialect == "h2" and _unsupported_h2_tokens(statement_tokens):
                complete = False
                self._add_residue(
                    "unsupported-h2-construct",
                    "H2 compatibility mode accepts only the PostgreSQL-compatible DDL subset",
                    source.path,
                    _sql_token_location(
                        source.path, text, statement_tokens[0], "sql_statement"
                    ),
                )
                continue
            start = statement_tokens[0].start
            end = statement_tokens[-1].end + 1
            statement_text = text[start:end]
            try:
                statement = _silent_sqlglot_parse_one(
                    dialect, statement_text
                )
            except (ParseError, ValueError, TokenError):
                complete = False
                self._add_residue(
                    "malformed-sql",
                    "SQLGlot rejected malformed or unsupported CREATE TABLE DDL",
                    source.path,
                    _sql_token_location(
                        source.path, text, statement_tokens[0], "sql_statement"
                    ),
                )
                continue
            table = _created_table(statement)
            locations = _create_table_token_locations(
                list(statement_tokens), [table] if table is not None else []
            )
            if table is None or len(locations) != 1:
                complete = False
                self._add_residue(
                    "malformed-sql",
                    "SQL table identity and location could not be proven from SQLGlot",
                    source.path,
                    _sql_token_location(
                        source.path, text, statement_tokens[0], "sql_statement"
                    ),
                )
                continue
            self._add_fact(
                "sql.table",
                table.name,
                (
                    ("catalog", table.catalog),
                    (
                        "catalogQuoted",
                        _sql_expression_quoted(table.args.get("catalog")),
                    ),
                    ("dialect", sqlglot_dialect),
                    (
                        "dialectMode",
                        "h2-postgres-compatibility"
                        if source.sql_dialect == "h2"
                        else "native",
                    ),
                    ("nameQuoted", _sql_expression_quoted(table.this)),
                    ("schema", table.db),
                    ("schemaQuoted", _sql_expression_quoted(table.args.get("db"))),
                    ("schemaSource", "profile-schema"),
                ),
                _sql_token_location(source.path, text, locations[0], "table"),
            )
            for column, data_type, primary_key in _created_columns(statement):
                self._add_fact(
                    "sql.column",
                    f"{table.name}.{column}",
                    (
                        ("column", column),
                        ("dataType", data_type),
                        ("dialect", sqlglot_dialect),
                        ("primaryKey", "true" if primary_key else "false"),
                        ("table", table.name),
                    ),
                    _sql_token_location(source.path, text, locations[0], "table"),
                )
        return complete

    def _add_fact(
        self,
        kind: str,
        subject: str,
        attributes: Iterable[tuple[str, FactValue]],
        location: SourceLocation,
    ) -> None:
        if len(self._facts) >= self._limits.max_facts:
            raise JavaSpringAnalysisError("analysis fact limit exceeded")
        sorted_attributes = tuple(sorted(attributes, key=lambda item: item[0]))
        identity = "|".join(
            (
                location.path,
                f"{location.start_byte:012d}",
                f"{location.end_byte:012d}",
                kind,
                subject,
                json.dumps(sorted_attributes, sort_keys=True, separators=(",", ":")),
            )
        )
        suffix = hashlib.sha256(identity.encode()).hexdigest()[:16]
        identifier = (
            f"{location.path}:{location.start_byte:012d}:{kind}:{subject}:{suffix}"
        )
        self._facts.append(
            SyntaxFact(identifier, kind, subject, sorted_attributes, location)
        )

    def _add_residue(
        self,
        code: str,
        message: str,
        symbol: str,
        location: SourceLocation,
    ) -> None:
        if len(self._residue) >= self._limits.max_residue:
            raise JavaSpringAnalysisError("analysis residue limit exceeded")
        if code not in _RESIDUE_CODES:
            raise JavaSpringAnalysisError("analysis residue code is outside the closed set")
        if (
            len(location.path.encode("utf-8")) > _MAX_SOURCE_PATH_BYTES
            or len(location.ast_kind) > _MAX_AST_KIND_CHARS
            or len(location.ast_path) > _MAX_AST_PATH_CHARS
        ):
            raise JavaSpringAnalysisError("analysis residue location limit exceeded")
        symbol_bytes = str(symbol).encode("utf-8", errors="replace")
        safe_symbol = (
            f"{code}:sha256:{hashlib.sha256(symbol_bytes).hexdigest()}"
            f":bytes:{len(symbol_bytes)}"
        )
        self._residue.append(
            AnalysisResidue(code, f"diagnostic:{code}", safe_symbol, location)
        )


def _maven_identity(text: str) -> tuple[tuple[str, str] | None, tuple[str, str] | None, str]:
    """(own coordinates, parent coordinates, boot-parent version) for one pom, cheaply."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None, None, ""
    own = _xml_values(root)
    parent_node = _xml_direct_child(root, "parent")
    parent = _xml_values(parent_node) if parent_node is not None else {}
    own_group = own.get("groupId") or parent.get("groupId", "")
    coordinates = (
        (own_group, own["artifactId"]) if own.get("artifactId") else None
    )
    parent_coordinates = (
        (parent.get("groupId", ""), parent["artifactId"])
        if parent.get("artifactId")
        else None
    )
    boot_version = (
        parent.get("version", "")
        if parent.get("groupId") == "org.springframework.boot"
        and parent.get("artifactId") == "spring-boot-starter-parent"
        else ""
    )
    return coordinates, parent_coordinates, boot_version


def _declared_parent_boot_versions(
    source: JavaSpringSource,
    text: str,
    sources: tuple[JavaSpringSource, ...],
) -> frozenset[str]:
    """Boot versions reachable through the parent this pom explicitly declares."""
    _, parent_coordinates, _ = _maven_identity(text)
    if parent_coordinates is None:
        return frozenset()
    versions: set[str] = set()
    for candidate in sources:
        if candidate.path == source.path:
            continue
        if PurePosixPath(candidate.path).name != "pom.xml":
            continue
        try:
            candidate_text = candidate.content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            continue
        coordinates, _, boot_version = _maven_identity(candidate_text)
        if coordinates == parent_coordinates and boot_version:
            versions.add(boot_version)
    return frozenset(versions)


def _inherit_parent_build_evidence(
    closures: list[_BuildClosure],
) -> list[_BuildClosure]:
    """Let a module inherit Boot evidence from the aggregator it names as its parent.

    Maven multi-module builds put the Boot parent in the aggregator and the Spring Data
    JPA dependency in each module, so neither file carries a complete closure alone.
    The link is followed only through the parent coordinates the module explicitly
    declares and only to a pom present in the analysed scope — never inferred from
    directory position, and never from a pom the snapshot does not contain.
    """
    by_coordinates = {
        closure.coordinates: closure
        for closure in closures
        if closure.coordinates is not None
    }
    rejoined: list[_BuildClosure] = []
    for closure in closures:
        if closure.boot or closure.parent_coordinates is None:
            rejoined.append(closure)
            continue
        parent = by_coordinates.get(closure.parent_coordinates)
        if parent is None or not parent.boot:
            rejoined.append(closure)
            continue
        inherited = tuple(
            _BuildEvidence(
                closure.path, item.kind, item.group, item.artifact, item.version, True
            )
            for item in parent.boot
        )
        rejoined.append(replace(closure, boot=inherited, relevant=True))
    return rejoined


def _validate_source_path(path: str) -> None:
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        raise JavaSpringAnalysisError("source path must be a relative tracked path")
    if len(path.encode("utf-8")) > _MAX_SOURCE_PATH_BYTES:
        raise JavaSpringAnalysisError("source path byte limit exceeded")
    candidate = PurePosixPath(path)
    if (
        candidate.is_absolute()
        or candidate.as_posix() != path
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise JavaSpringAnalysisError("source path must be a relative tracked path")


def _walk(node: Node) -> Iterable[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.named_children))


def _type_declarations(root: Node) -> Iterable[Node]:
    for node in _walk(root):
        if node.type in {
            "annotation_type_declaration",
            "class_declaration",
            "enum_declaration",
            "interface_declaration",
            "record_declaration",
        }:
            yield node


def _qualified_type_name(declaration: Node, package: str, content: bytes) -> str:
    names: list[str] = []
    current: Node | None = declaration
    declaration_types = {
        "annotation_type_declaration",
        "class_declaration",
        "enum_declaration",
        "interface_declaration",
        "record_declaration",
    }
    while current is not None:
        if current.type in declaration_types:
            name = current.child_by_field_name("name")
            if name is not None:
                names.append(_node_text(name, content))
        current = current.parent
    qualified = ".".join(reversed(names))
    return f"{package}.{qualified}" if package else qualified


def _java_import_text(node: Node, content: bytes) -> str:
    raw = _node_text(node, content).strip()
    if not raw.startswith("import ") or not raw.endswith(";"):
        return raw
    imported = raw[len("import ") : -1].strip()
    return imported[len("static ") :].strip() if imported.startswith("static ") else imported


def _build_java_symbol_index(parsed_files: list[_ParsedJava]) -> _JavaSymbolIndex:
    local_types = frozenset(
        _qualified_type_name(declaration, parsed.package, parsed.source.content)
        for parsed in parsed_files
        for declaration in _type_declarations(parsed.tree.root_node)
    )
    contexts: list[tuple[str, _SymbolContext]] = []
    for parsed in parsed_files:
        imports: dict[str, list[str]] = {}
        wildcards: list[str] = []
        for child in parsed.tree.root_node.named_children:
            if child.type != "import_declaration":
                continue
            raw = _node_text(child, parsed.source.content).strip()
            if raw.startswith("import static "):
                continue
            imported = _java_import_text(child, parsed.source.content)
            if imported.endswith(".*"):
                wildcards.append(imported[:-2])
                continue
            imports.setdefault(imported.rsplit(".", 1)[-1], []).append(imported)
        context = _SymbolContext(
            tuple(
                (name, tuple(sorted(set(values))))
                for name, values in sorted(imports.items())
            ),
            tuple(sorted(set(wildcards))),
        )
        contexts.append((parsed.source.path, context))
    return _JavaSymbolIndex(local_types, tuple(sorted(contexts)))


def _node_text(node: Node | None, content: bytes) -> str:
    if node is None:
        return ""
    return content[node.start_byte : node.end_byte].decode("utf-8", errors="strict")


def _bounded_ast_path(parent: str, segment: str) -> str:
    candidate = f"{parent}/{segment}"
    if len(candidate) <= _MAX_AST_PATH_CHARS:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    marker = f"/truncated:sha256:{digest}"
    return f"{candidate[: _MAX_AST_PATH_CHARS - len(marker)]}{marker}"


def _whole_file_location(source: JavaSpringSource, ast_kind: str) -> SourceLocation:
    return SourceLocation(
        source.path, 1, 0, len(source.content), ast_kind, ast_kind
    )


def _scope_location(sources: tuple[JavaSpringSource, ...]) -> SourceLocation:
    if sources:
        return SourceLocation(
            sources[0].path, 1, 0, 0, "repository_scope", "repository_scope"
        )
    return SourceLocation(
        "<repository>", 1, 0, 0, "repository_scope", "repository_scope"
    )


def _text_location(
    source: JavaSpringSource, start_character: int, end_character: int, ast_kind: str
) -> SourceLocation:
    text = source.content.decode("utf-8", errors="strict")
    start_byte = len(text[:start_character].encode())
    end_byte = len(text[:end_character].encode())
    line = text.count("\n", 0, start_character) + 1
    return SourceLocation(
        source.path,
        line,
        start_byte,
        end_byte,
        ast_kind,
        f"{ast_kind}[{start_byte}:{end_byte}]",
    )


def _local_xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_direct_child(
    parent: ET.Element | None, name: str
) -> ET.Element | None:
    if parent is None:
        return None
    return next(
        (child for child in parent if _local_xml_name(child.tag) == name), None
    )


def _xml_direct_children(
    parent: ET.Element | None, name: str
) -> tuple[ET.Element, ...]:
    if parent is None:
        return ()
    return tuple(
        child for child in parent if _local_xml_name(child.tag) == name
    )


def _xml_values(element: ET.Element) -> dict[str, str]:
    return {
        _local_xml_name(child.tag): (child.text or "").strip()
        for child in element
    }


def _is_supported_version(version: str) -> bool:
    match = _SEMANTIC_VERSION.fullmatch(version)
    if match is None:
        return False
    value = tuple(int(part) for part in match.groups())
    return (3, 0, 0) <= value < (5, 0, 0)


def _gradle_tokens(text: str) -> tuple[tuple[_GradleToken, ...], bool]:
    tokens: list[_GradleToken] = []
    delimiters: list[str] = []
    opening_delimiters = {"{", "(", "["}
    closing_delimiters = {"}": "{", ")": "(", "]": "["}
    index = 0
    while index < len(text):
        character = text[index]
        if character.isspace():
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline + 1
            continue
        if text.startswith("/*", index):
            close = text.find("*/", index + 2)
            if close < 0:
                return tuple(tokens), True
            index = close + 2
            continue
        if (
            text.startswith(('"""', "'''", "$/"), index)
            or character == "/"
        ):
            return tuple(tokens), True
        if character in {"'", '"'}:
            quote = character
            start = index + 1
            index += 1
            value: list[str] = []
            while index < len(text) and text[index] != quote:
                if text[index] == "\\":
                    if index + 1 >= len(text):
                        return tuple(tokens), True
                    value.append(text[index + 1])
                    index += 2
                else:
                    value.append(text[index])
                    index += 1
            if index >= len(text):
                return tuple(tokens), True
            tokens.append(_GradleToken("string", "".join(value), start, index))
            index += 1
            continue
        if character.isalpha() or character in {"_", "$"}:
            start = index
            index += 1
            while index < len(text) and (
                text[index].isalnum() or text[index] in {"_", ".", "$", "-"}
            ):
                index += 1
            tokens.append(_GradleToken("identifier", text[start:index], start, index))
            continue
        if character in opening_delimiters:
            delimiters.append(character)
        elif character in closing_delimiters:
            if not delimiters or delimiters.pop() != closing_delimiters[character]:
                return tuple(tokens), True
        tokens.append(_GradleToken("symbol", character, index, index + 1))
        index += 1
    return tuple(tokens), bool(delimiters)


def _gradle_has_invalid_build_context(tokens: tuple[_GradleToken, ...]) -> bool:
    delimiters: list[str] = []
    closing_delimiters = {"}": "{", ")": "(", "]": "["}
    for index, token in enumerate(tokens):
        if token.kind != "symbol":
            continue
        if token.value in {"{", "(", "["}:
            label = (
                tokens[index - 1].value
                if index > 0 and tokens[index - 1].kind == "identifier"
                else None
            )
            if (
                token.value == "{"
                and label in {"dependencies", "plugins"}
                and delimiters
            ):
                return True
            delimiters.append(token.value)
        elif token.value in closing_delimiters:
            delimiters.pop()
    return False


def _gradle_literal_dependencies(
    tokens: tuple[_GradleToken, ...],
) -> tuple[_GradleToken, ...]:
    result: list[_GradleToken] = []
    block_stack: list[str | None] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.kind == "symbol" and token.value == "{":
            label = (
                tokens[index - 1].value
                if index > 0 and tokens[index - 1].kind == "identifier"
                else None
            )
            block_stack.append(label)
            index += 1
            continue
        if token.kind == "symbol" and token.value == "}":
            if block_stack:
                block_stack.pop()
            index += 1
            continue
        if (
            block_stack == ["dependencies"]
            and token.kind == "identifier"
            and token.value in _GRADLE_CONFIGURATIONS
        ):
            candidate = index + 1
            if (
                candidate < len(tokens)
                and tokens[candidate].kind == "symbol"
                and tokens[candidate].value == "("
            ):
                candidate += 1
            if candidate < len(tokens) and tokens[candidate].kind == "string":
                result.append(tokens[candidate])
                index = candidate + 1
                continue
        index += 1
    return tuple(result)


def _gradle_boot_plugins(
    tokens: tuple[_GradleToken, ...],
) -> tuple[tuple[_GradleToken, _GradleToken | None], ...]:
    result: list[tuple[_GradleToken, _GradleToken | None]] = []
    block_stack: list[str | None] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.kind == "symbol" and token.value == "{":
            label = (
                tokens[index - 1].value
                if index > 0 and tokens[index - 1].kind == "identifier"
                else None
            )
            block_stack.append(label)
            index += 1
            continue
        if token.kind == "symbol" and token.value == "}":
            if block_stack:
                block_stack.pop()
            index += 1
            continue
        if (
            block_stack != ["plugins"]
            or token.kind != "identifier"
            or token.value != "id"
        ):
            index += 1
            continue
        candidate = index + 1
        if (
            candidate < len(tokens)
            and tokens[candidate].kind == "symbol"
            and tokens[candidate].value == "("
        ):
            candidate += 1
        if candidate >= len(tokens) or tokens[candidate].kind != "string":
            index += 1
            continue
        plugin_id = tokens[candidate]
        candidate += 1
        if (
            candidate < len(tokens)
            and tokens[candidate].kind == "symbol"
            and tokens[candidate].value == ")"
        ):
            candidate += 1
        version: _GradleToken | None = None
        next_index = candidate
        if (
            candidate < len(tokens)
            and tokens[candidate].kind == "identifier"
            and tokens[candidate].value == "version"
        ):
            candidate += 1
            if (
                candidate < len(tokens)
                and tokens[candidate].kind == "symbol"
                and tokens[candidate].value == "("
            ):
                candidate += 1
            if candidate < len(tokens) and tokens[candidate].kind == "string":
                version = tokens[candidate]
                next_index = candidate + 1
        result.append((plugin_id, version))
        index = max(index + 1, next_index)
    return tuple(result)


def _simple_type(type_name: str) -> str:
    return type_name.rsplit(".", 1)[-1].split("<", 1)[0]


def _super_types(
    declaration: Node, content: bytes
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[Node, ...], tuple[Node, ...]]:
    """Split declared supertypes into generic and plain nodes.

    The plain nodes matter for repository abstractions: a Spring Data specialization
    typically extends both a framework base with generics *and* a plain project interface
    that services actually inject.
    """
    extends: list[str] = []
    implements: list[str] = []
    generic_nodes: list[Node] = []
    plain_nodes: list[Node] = []
    for child in declaration.named_children:
        if child.type in {"extends_interfaces", "superclass"}:
            types = _direct_supertype_nodes(child)
            extends.extend(_node_text(node, content) for node in types)
        elif child.type in {"super_interfaces", "implements_interfaces"}:
            types = _direct_supertype_nodes(child)
            implements.extend(_node_text(node, content) for node in types)
        else:
            continue
        generic_nodes.extend(node for node in types if node.type == "generic_type")
        plain_nodes.extend(
            node
            for node in types
            if node.type in {"type_identifier", "scoped_type_identifier"}
        )
    return tuple(extends), tuple(implements), tuple(generic_nodes), tuple(plain_nodes)


def _direct_supertype_nodes(node: Node) -> tuple[Node, ...]:
    type_list = next((child for child in node.named_children if child.type == "type_list"), None)
    if type_list is not None:
        return tuple(type_list.named_children)
    return tuple(
        child
        for child in node.named_children
        if child.type
        not in {"extends", "implements", "permits", "type_parameters"}
    )


def _generic_parts(node: Node, content: bytes) -> tuple[str, tuple[str, ...]] | None:
    if node.type != "generic_type":
        return None
    arguments_node = next(
        (child for child in node.named_children if child.type == "type_arguments"), None
    )
    base_node = next(
        (child for child in node.named_children if child.type != "type_arguments"), None
    )
    if base_node is None or arguments_node is None:
        return None
    return _node_text(base_node, content), tuple(
        _node_text(child, content) for child in arguments_node.named_children
    )


def _annotation_values(
    annotation: Node,
    content: bytes,
    ignored_keys: frozenset[str] = frozenset(),
) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    arguments = annotation.child_by_field_name("arguments")
    if arguments is None:
        return (), ()
    literals: list[tuple[str, str]] = []
    dynamic: list[str] = []
    for argument in arguments.named_children:
        if argument.type == "element_value_pair":
            key = argument.child_by_field_name("key")
            value = argument.child_by_field_name("value")
            name = _node_text(key, content) if key is not None else "value"
        else:
            name = "value"
            value = argument
        if name in ignored_keys:
            continue
        literal = _java_literal(value, content)
        if literal is None:
            dynamic.append(_node_text(value, content) if value is not None else "<missing>")
        else:
            literals.append((name, literal))
    return tuple(sorted(literals)), tuple(sorted(dynamic))


def _java_literal(node: Node | None, content: bytes) -> str | None:
    if node is None:
        return None
    text = _node_text(node, content)
    if node.type == "string_literal":
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, str) else None
    if node.type in {"decimal_integer_literal", "false", "true"}:
        return text
    return None


def _parameter_signature(declaration: Node, content: bytes) -> tuple[str, ...]:
    return tuple(
        f"{type_name} {name}"
        for name, type_name in _parameters(declaration, content).items()
    )


def _canonical_parameter_types(declaration: Node, content: bytes) -> tuple[str, ...]:
    return tuple(
        "".join(type_name.split())
        for type_name in _parameters(declaration, content).values()
    )


def _method_declaration_mode(
    owner: Node, declaration: Node, content: bytes
) -> str:
    modifiers = next(
        (
            child
            for child in declaration.named_children
            if child.type == "modifiers"
        ),
        None,
    )
    modifier_text = _node_text(modifiers, content) if modifiers is not None else ""
    body = declaration.child_by_field_name("body")
    if (
        owner.type == "interface_declaration"
        and body is None
        and not re.search(r"\b(?:default|private|static)\b", modifier_text)
    ):
        return "abstract-interface"
    for mode in ("default", "static", "private"):
        if re.search(rf"\b{mode}\b", modifier_text):
            return mode
    return "concrete"


def _parameters(declaration: Node, content: bytes) -> dict[str, str]:
    parameters = declaration.child_by_field_name("parameters")
    if parameters is None:
        return {}
    result: dict[str, str] = {}
    for parameter in parameters.named_children:
        if parameter.type not in {"formal_parameter", "spread_parameter"}:
            continue
        name = parameter.child_by_field_name("name")
        type_node = parameter.child_by_field_name("type")
        if name is not None and type_node is not None:
            result[_node_text(name, content)] = _node_text(type_node, content)
    return result


def _lexical_binder_ranges(
    declaration: Node,
    content: bytes,
) -> dict[str, tuple[tuple[int, ...], tuple[int, ...]]]:
    ranges: dict[str, list[tuple[int, int]]] = {}

    def add(name: Node | None, start: int, end: int) -> None:
        if name is None or start >= end:
            return
        ranges.setdefault(_node_text(name, content), []).append((start, end))

    body = declaration.child_by_field_name("body") or declaration
    parameters = declaration.child_by_field_name("parameters")
    for name in _parameter_name_nodes(parameters):
        add(name, body.start_byte, body.end_byte)

    for node in _walk(body):
        if node.type == "local_variable_declaration":
            scope = _local_variable_scope(node, body)
            for declarator in (
                child
                for child in node.named_children
                if child.type == "variable_declarator"
            ):
                name = declarator.child_by_field_name("name")
                add(name, name.end_byte if name is not None else 0, scope.end_byte)
        elif node.type == "lambda_expression":
            scope = node.child_by_field_name("body") or node
            for name in _parameter_name_nodes(
                node.child_by_field_name("parameters")
            ):
                add(name, scope.start_byte, scope.end_byte)
        elif node.type == "catch_clause":
            scope = node.child_by_field_name("body") or node
            parameter = next(
                (
                    child
                    for child in node.named_children
                    if child.type == "catch_formal_parameter"
                ),
                None,
            )
            add(
                parameter.child_by_field_name("name") if parameter is not None else None,
                scope.start_byte,
                scope.end_byte,
            )
        elif node.type == "enhanced_for_statement":
            scope = node.child_by_field_name("body") or node
            add(
                node.child_by_field_name("name"),
                scope.start_byte,
                scope.end_byte,
            )
        elif node.type == "resource":
            statement = _ancestor_of_type(node, {"try_with_resources_statement"}, body)
            scope = statement.child_by_field_name("body") if statement is not None else None
            name = node.child_by_field_name("name")
            if scope is not None:
                add(name, name.end_byte if name is not None else 0, scope.end_byte)
        elif node.type in {
            "instanceof_expression",
            "record_pattern_component",
            "type_pattern",
        }:
            name = node.child_by_field_name("name") or _pattern_name_node(node)
            binding_range = _pattern_binding_range(node, body)
            if binding_range is not None:
                add(name, *binding_range)

    indexed: dict[str, tuple[tuple[int, ...], tuple[int, ...]]] = {}
    for name, found_ranges in ranges.items():
        ordered = sorted(found_ranges)
        starts: list[int] = []
        prefix_ends: list[int] = []
        maximum_end = 0
        for start, end in ordered:
            starts.append(start)
            maximum_end = max(maximum_end, end)
            prefix_ends.append(maximum_end)
        indexed[name] = (tuple(starts), tuple(prefix_ends))
    return indexed


def _parameter_name_nodes(parameters: Node | None) -> tuple[Node, ...]:
    if parameters is None:
        return ()
    if parameters.type == "identifier":
        return (parameters,)
    names: list[Node] = []
    for node in _walk(parameters):
        if node.type in {"formal_parameter", "spread_parameter"}:
            name = node.child_by_field_name("name")
            if name is not None:
                names.append(name)
        elif node.type == "inferred_parameters":
            names.extend(
                child for child in node.named_children if child.type == "identifier"
            )
    return tuple(names)


def _local_variable_scope(declaration: Node, boundary: Node) -> Node:
    current = declaration.parent
    while current is not None and current.id != boundary.id:
        if current.type in {
            "block",
            "constructor_body",
            "for_statement",
            "switch_block_statement_group",
            "switch_rule",
        }:
            return current
        current = current.parent
    return boundary


def _ancestor_of_type(
    node: Node,
    types: set[str],
    boundary: Node,
) -> Node | None:
    current = node.parent
    while current is not None and current.id != boundary.id:
        if current.type in types:
            return current
        current = current.parent
    return None


def _pattern_name_node(pattern: Node) -> Node | None:
    return next(
        (
            child
            for child in reversed(pattern.named_children)
            if child.type == "identifier"
        ),
        None,
    )


def _pattern_binding_range(
    pattern: Node,
    boundary: Node,
) -> tuple[int, int] | None:
    name = pattern.child_by_field_name("name") or _pattern_name_node(pattern)
    pattern_start = name.end_byte if name is not None else pattern.end_byte
    current = pattern.parent
    while current is not None and current.id != boundary.id:
        if current.type == "switch_rule":
            return pattern_start, current.end_byte
        if current.type == "if_statement":
            consequence = current.child_by_field_name("consequence")
            if not _pattern_is_negated(pattern, current):
                scope = consequence or current
                return scope.start_byte, scope.end_byte
            alternative = current.child_by_field_name("alternative")
            if alternative is not None:
                return alternative.start_byte, alternative.end_byte
            if consequence is not None and _is_terminating_statement(consequence):
                block = _ancestor_of_type(current, {"block", "constructor_body"}, boundary)
                scope_end = block.end_byte if block is not None else boundary.end_byte
                return current.end_byte, scope_end
            block = _ancestor_of_type(current, {"block", "constructor_body"}, boundary)
            scope_end = block.end_byte if block is not None else boundary.end_byte
            return pattern_start, scope_end
        if current.type in {"for_statement", "while_statement"}:
            scope = current.child_by_field_name("body") or current
            return scope.start_byte, scope.end_byte
        current = current.parent
    return None


def _pattern_is_negated(pattern: Node, statement: Node) -> bool:
    negated = False
    current = pattern.parent
    while current is not None and current.id != statement.id:
        if current.type == "unary_expression":
            operator = current.child_by_field_name("operator")
            if operator is not None and operator.type == "!":
                negated = not negated
        current = current.parent
    return negated


def _is_terminating_statement(statement: Node) -> bool:
    if statement.type in {"return_statement", "throw_statement"}:
        return True
    if statement.type != "block" or not statement.named_children:
        return False
    return _is_terminating_statement(statement.named_children[-1])


def _is_lexically_shadowed(
    ranges: dict[str, tuple[tuple[int, ...], tuple[int, ...]]],
    name: str,
    position: int,
) -> bool:
    indexed = ranges.get(name)
    if indexed is None:
        return False
    starts, prefix_ends = indexed
    candidate = bisect_right(starts, position) - 1
    return candidate >= 0 and position < prefix_ends[candidate]


def _constructor_binding(
    assignment: Node,
    content: bytes,
    parameters: dict[str, str | None],
    fields: dict[str, str],
) -> tuple[str, str, str] | None:
    left = assignment.child_by_field_name("left")
    right = assignment.child_by_field_name("right")
    if left is None or right is None or left.type != "field_access" or right.type != "identifier":
        return None
    object_node = left.child_by_field_name("object")
    field_node = left.child_by_field_name("field")
    if (
        object_node is None
        or object_node.type != "this"
        or field_node is None
    ):
        return None
    field_name = _node_text(field_node, content)
    parameter_name = _node_text(right, content)
    # `fields` holds only tracked repository fields, and an unresolvable parameter type
    # is None. Comparing the two `.get` results alone treats "both absent" as a match,
    # so an injected collaborator that is neither tracked nor resolvable looked like a
    # binding and then indexed a field that was never there.
    field_type = fields.get(field_name)
    if field_type is None or field_type != parameters.get(parameter_name):
        return None
    return field_name, parameter_name, field_type


def _receiver_name(node: Node | None, content: bytes) -> tuple[str, bool] | None:
    if node is None:
        return None
    if node.type == "identifier":
        return _node_text(node, content), False
    if node.type == "field_access":
        object_node = node.child_by_field_name("object")
        field = node.child_by_field_name("field")
        if object_node is not None and object_node.type == "this" and field is not None:
            return _node_text(field, content), True
    return None


def _created_table(statement: exp.Expression | None) -> exp.Table | None:
    if (
        not isinstance(statement, exp.Create)
        or str(statement.args.get("kind", "")).upper() != "TABLE"
    ):
        return None
    target = statement.this
    if isinstance(target, exp.Schema):
        target = target.this
    return target if isinstance(target, exp.Table) else None


def _created_columns(
    statement: exp.Expression | None,
) -> tuple[tuple[str, str, bool], ...]:
    """Return (column, dataType, isPrimaryKey) for a proven CREATE TABLE.

    Only literal column definitions count. A table whose shape comes from a query or a
    LIKE clause has no provable element list, so it yields nothing rather than a guess.
    """
    if (
        not isinstance(statement, exp.Create)
        or str(statement.args.get("kind", "")).upper() != "TABLE"
    ):
        return ()
    schema = statement.this
    if not isinstance(schema, exp.Schema):
        return ()
    columns: list[tuple[str, str, bool]] = []
    for definition in schema.expressions:
        if not isinstance(definition, exp.ColumnDef):
            continue
        name = definition.this
        if not isinstance(name, exp.Identifier) or not name.name:
            continue
        data_type = definition.args.get("kind")
        primary_key = any(
            isinstance(constraint, exp.ColumnConstraint)
            and isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint)
            for constraint in definition.args.get("constraints") or ()
        )
        columns.append(
            (name.name, data_type.sql().upper() if data_type is not None else "", primary_key)
        )
    return tuple(columns)


def _sql_expression_quoted(expression: object) -> str:
    return (
        "true"
        if isinstance(expression, exp.Identifier)
        and bool(expression.args.get("quoted"))
        else "false"
    )


def _sql_statement_tokens(tokens: list[Token]) -> tuple[tuple[Token, ...], ...]:
    statements: list[tuple[Token, ...]] = []
    start = 0
    for index, token in enumerate(tokens):
        if token.token_type != TokenType.SEMICOLON:
            continue
        if index > start:
            statements.append(tuple(tokens[start:index]))
        start = index + 1
    if start < len(tokens):
        statements.append(tuple(tokens[start:]))
    return tuple(statements)


def _is_session_statement_tokens(tokens: tuple[Token, ...]) -> bool:
    """`USE db`, `CREATE DATABASE`, `CREATE SCHEMA` — session setup, not schema."""
    if not tokens:
        return False
    if tokens[0].token_type == TokenType.USE:
        return True
    return (
        len(tokens) >= 2
        and tokens[0].token_type == TokenType.CREATE
        and tokens[1].token_type in {TokenType.DATABASE, TokenType.SCHEMA}
    )


def _is_create_table_tokens(tokens: tuple[Token, ...]) -> bool:
    return (
        len(tokens) >= 2
        and tokens[0].token_type == TokenType.CREATE
        and tokens[1].token_type == TokenType.TABLE
    )


def _is_create_index_tokens(tokens: tuple[Token, ...]) -> bool:
    words = tuple(token.text.casefold() for token in tokens[:3])
    return bool(words) and words[0] == "create" and "index" in words[1:]


def _valid_create_index_statement(
    dialect: Dialect, text: str, tokens: tuple[Token, ...]
) -> bool:
    if not _closed_create_index_tokens(tokens):
        return False
    statement_text = text[tokens[0].start : tokens[-1].end + 1]
    try:
        statement = _silent_sqlglot_parse_one(dialect, statement_text)
    except (ParseError, ValueError, TokenError):
        return False
    if (
        not isinstance(statement, exp.Create)
        or str(statement.args.get("kind", "")).upper() != "INDEX"
        or not isinstance(statement.this, exp.Index)
    ):
        return False
    index = statement.this
    name = index.this
    table = index.args.get("table")
    parameters = index.args.get("params")
    columns = parameters.args.get("columns") if isinstance(parameters, exp.IndexParameters) else None
    return (
        isinstance(name, exp.Identifier)
        and _EVIDENCE_TABLE_IDENTIFIER.fullmatch(name.name) is not None
        and isinstance(table, exp.Table)
        and _sql_expression_table_identity(table, dialect) is not None
        and isinstance(columns, list)
        and bool(columns)
    )


def _closed_create_index_tokens(tokens: tuple[Token, ...]) -> bool:
    """Accept only the complete closed supported PostgreSQL index subset."""
    position = 0

    def consume_type(token_type: TokenType) -> bool:
        nonlocal position
        if position >= len(tokens) or tokens[position].token_type != token_type:
            return False
        position += 1
        return True

    def consume_word(word: str) -> bool:
        nonlocal position
        if position >= len(tokens) or tokens[position].text.casefold() != word:
            return False
        position += 1
        return True

    def consume_identifier() -> bool:
        nonlocal position
        if position >= len(tokens):
            return False
        token = tokens[position]
        if token.token_type not in {
            TokenType.IDENTIFIER,
            TokenType.NAME,
            TokenType.VAR,
        } or _EVIDENCE_TABLE_IDENTIFIER.fullmatch(token.text) is None:
            return False
        position += 1
        return True

    if not consume_type(TokenType.CREATE):
        return False
    if position < len(tokens) and tokens[position].token_type == TokenType.UNIQUE:
        position += 1
    if not consume_type(TokenType.INDEX):
        return False
    if position < len(tokens) and tokens[position].text.casefold() == "if":
        if not (
            consume_word("if")
            and consume_type(TokenType.NOT)
            and consume_type(TokenType.EXISTS)
        ):
            return False
    if not consume_identifier() or not consume_type(TokenType.ON):
        return False
    if not consume_identifier():
        return False
    qualifiers = 1
    while position < len(tokens) and tokens[position].token_type == TokenType.DOT:
        position += 1
        qualifiers += 1
        if qualifiers > 3 or not consume_identifier():
            return False
    if not consume_type(TokenType.L_PAREN):
        return False

    def consume_index_item() -> bool:
        nonlocal position
        if (
            position + 1 < len(tokens)
            and tokens[position].text.casefold() == "lower"
            and tokens[position + 1].token_type == TokenType.L_PAREN
        ):
            position += 2
            return consume_identifier() and consume_type(TokenType.R_PAREN)
        return consume_identifier()

    if not consume_index_item():
        return False
    while position < len(tokens) and tokens[position].token_type == TokenType.COMMA:
        position += 1
        if not consume_index_item():
            return False
    return consume_type(TokenType.R_PAREN) and position == len(tokens)


def _sql_table_identifier_status(tokens: tuple[Token, ...]) -> str:
    try:
        boundary = next(
            index
            for index in range(2, len(tokens))
            if tokens[index].token_type == TokenType.L_PAREN
        )
    except StopIteration:
        boundary = len(tokens)
    identity = list(tokens[2:boundary])
    if [token.text.casefold() for token in identity[:3]] == ["if", "not", "exists"]:
        identity = identity[3:]
    dynamic_types = {
        TokenType.COLON,
        TokenType.L_BRACE,
        TokenType.PARAMETER,
        TokenType.PLACEHOLDER,
        TokenType.R_BRACE,
    }
    if any(token.token_type == TokenType.DQMARK for token in identity):
        return "malformed"
    if any(token.token_type in dynamic_types for token in identity):
        return "dynamic"
    if not identity:
        return "malformed"
    identifier_types = {TokenType.IDENTIFIER, TokenType.VAR}
    expect_identifier = True
    for token in identity:
        if expect_identifier and token.token_type not in identifier_types:
            return "malformed"
        if not expect_identifier and token.token_type != TokenType.DOT:
            return "malformed"
        expect_identifier = not expect_identifier
    return "concrete" if not expect_identifier else "malformed"


def _unsupported_h2_tokens(tokens: tuple[Token, ...]) -> bool:
    unsupported = {
        "alias",
        "auto_increment",
        "generated",
        "identity",
        "sequence",
    }
    return any(token.text.casefold() in unsupported for token in tokens)


def _silent_sqlglot_parse_one(
    dialect: Dialect, statement: str
) -> exp.Expression | None:
    parsed = _silent_sqlglot_parse(dialect, statement)
    return parsed[0] if len(parsed) == 1 else None


def _silent_sqlglot_parse(
    dialect: Dialect, statement: str
) -> tuple[exp.Expression, ...]:
    parser_class = dialect.parser_class

    class _SilentParser(parser_class):  # type: ignore[valid-type, misc]
        def _warn_unsupported(self) -> None:
            return

    parsed = _SilentParser(error_level=ErrorLevel.RAISE, dialect=dialect).parse(
        dialect.tokenizer().tokenize(statement), statement
    )
    return tuple(item for item in parsed if item is not None)


def _create_table_token_locations(
    tokens: list[Token], tables: list[exp.Table]
) -> tuple[Token, ...]:
    locations: list[Token] = []
    cursor = 0
    for table in tables:
        table_keyword = next(
            (
                index
                for index in range(cursor, len(tokens))
                if tokens[index].token_type == TokenType.TABLE
            ),
            None,
        )
        if table_keyword is None:
            break
        boundary = next(
            (
                index
                for index in range(table_keyword + 1, len(tokens))
                if tokens[index].token_type in {TokenType.L_PAREN, TokenType.SEMICOLON}
            ),
            len(tokens),
        )
        matches = [
            token
            for token in tokens[table_keyword + 1 : boundary]
            if token.text.casefold() == table.name.casefold()
        ]
        if not matches:
            break
        locations.append(matches[-1])
        cursor = boundary
    return tuple(locations)


def _sql_token_location(
    path: str, text: str, token: Token, ast_kind: str
) -> SourceLocation:
    start_byte = len(text[: token.start].encode())
    end_byte = len(text[: token.end + 1].encode())
    return SourceLocation(
        path,
        token.line,
        start_byte,
        end_byte,
        ast_kind,
        f"sql.token[{token.start}:{token.end + 1}]/{ast_kind}",
    )


def _column_override(annotations: tuple["_AnnotationRecord", ...]) -> str | None:
    """The literal column name from an exact `@Column(name=...)`, if one is provable.

    Only a resolved `jakarta.persistence.Column` carrying a bounded literal counts. A
    dynamic or unresolvable annotation leaves the field to the naming convention rather
    than inventing a column name.
    """
    for record in annotations:
        if record.resolved_fqn != _COLUMN_FQN:
            continue
        name = dict(record.literal_values).get("name")
        if isinstance(name, str) and _EVIDENCE_TABLE_IDENTIFIER.fullmatch(name):
            return name
        return None
    return None


def _snake_case(name: str) -> str:
    """JPA's default physical naming: camelCase becomes snake_case."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


# Spring Data derived-query grammar, reduced to what can be proven without a parser.
_DERIVED_SUBJECTS = (
    "findBy", "readBy", "getBy", "queryBy", "searchBy", "streamBy",
    "countBy", "existsBy", "deleteBy", "removeBy",
)
_DERIVED_WRITE_SUBJECTS = ("deleteBy", "removeBy")
# Trailing operator keywords that qualify a property rather than name one.
_DERIVED_KEYWORDS = (
    "IsStartingWith", "IsEndingWith", "IsNotContaining", "IsContaining", "StartingWith",
    "EndingWith", "Containing", "IgnoreCase", "NotContains", "GreaterThanEqual",
    "LessThanEqual", "GreaterThan", "LessThan", "IsNotNull", "IsNull", "NotNull",
    "IsBetween", "Between", "NotLike", "Like", "NotIn", "In", "IsTrue", "IsFalse",
    "True", "False", "After", "Before", "Near", "Within", "Regex", "Not", "Is",
    "Equals", "Contains", "Matches", "Exists",
)


def _derived_properties(method: str) -> tuple[str, ...] | None:
    """Split a derived query method into the properties it constrains.

    Returns None when the name is not a derived query at all, so a plain repository
    method is never mistaken for one.
    """
    subject = next((item for item in _DERIVED_SUBJECTS if method.startswith(item)), None)
    if subject is None:
        return None
    remainder = method[len(subject):]
    if not remainder:
        return ()
    # OrderBy introduces sort properties, which are read too, so keep both halves.
    remainder = remainder.replace("OrderBy", "And")
    properties: list[str] = []
    for part in re.split(r"And|Or", remainder):
        if not part:
            continue
        for keyword in _DERIVED_KEYWORDS:
            if part.endswith(keyword) and len(part) > len(keyword):
                part = part[: -len(keyword)]
                break
        if part and part[0].isupper():
            properties.append(part[0].lower() + part[1:])
    return tuple(properties)


def _jpql_properties(query: str, alias_hint: str | None = None) -> tuple[str, ...]:
    """Property references in a JPQL body, as `alias.property` pairs.

    Only dotted references are taken: a bare identifier could be an entity, an alias or a
    keyword, and guessing between them is exactly what this analyzer must not do.
    """
    found: list[str] = []
    for match in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]*\.([A-Za-z_][A-Za-z0-9_]*)", query):
        name = match.group(1)
        if name not in found:
            found.append(name)
    return tuple(found)
