from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable, TypeAlias

import tree_sitter_java
from sqlglot import exp
from sqlglot.dialects import Dialect
from sqlglot.errors import ErrorLevel, ParseError, TokenError
from sqlglot.tokens import Token, TokenType
from tree_sitter import Language, Node, Parser, Tree


FactValue: TypeAlias = str | tuple[str, ...]
_SUPPORTED_COORDINATES = {
    ("org.springframework.boot", "spring-boot-starter-data-jpa"),
    ("org.springframework.data", "spring-data-jpa"),
}
_REPOSITORY_BASES = {
    "CrudRepository",
    "JpaRepository",
    "ListCrudRepository",
    "PagingAndSortingRepository",
    "Repository",
}
_DYNAMIC_BUILD_TOKEN = re.compile(r"(?:\$\{|\$[A-Za-z_]|\+)")
_GRADLE_CONFIGURATIONS = {"api", "compileOnly", "implementation", "runtimeOnly"}
_SQLGLOT_DIALECTS = {
    "h2": "postgres",
    "mysql": "mysql",
    "postgres": "postgres",
    "postgresql": "postgres",
}


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

    def __post_init__(self) -> None:
        for name in (
            "max_files",
            "max_file_bytes",
            "max_total_bytes",
            "max_facts",
            "max_residue",
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


@dataclass(frozen=True, slots=True)
class JavaSpringAnalysis:
    framework: FrameworkClassification
    facts: tuple[SyntaxFact, ...]
    residue: tuple[AnalysisResidue, ...]
    stats: AnalysisStats


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
class _BuildCoordinate:
    path: str
    group: str
    artifact: str
    version: str

    @property
    def rendered(self) -> str:
        coordinate = f"{self.group}:{self.artifact}"
        if self.version:
            coordinate = f"{coordinate}:{self.version}"
        return f"{self.path}:{coordinate}"


class JavaSpringScaAnalyzer:
    """Extracts bounded syntax facts from supplied bytes without running repository code."""

    def __init__(self, limits: JavaSpringScaLimits | None = None) -> None:
        self._limits = limits or JavaSpringScaLimits()
        self._parser = Parser(Language(tree_sitter_java.language()))
        self._facts: list[SyntaxFact] = []
        self._residue: list[AnalysisResidue] = []

    def analyze(self, sources: Iterable[JavaSpringSource]) -> JavaSpringAnalysis:
        ordered = self._validate_sources(tuple(sources))
        self._facts = []
        self._residue = []
        framework = self._classify_framework(ordered)
        parsed_java: list[_ParsedJava] = []
        sql_files_parsed = 0

        for source in ordered:
            if source.path.endswith(".java"):
                parsed = self._parse_java(source)
                if parsed is not None:
                    parsed_java.append(parsed)
            elif source.path.endswith(".sql"):
                if self._parse_sql(source):
                    sql_files_parsed += 1

        repository_types = self._extract_java_facts(parsed_java, framework)
        if framework.status == "supported":
            self._extract_repository_usage(parsed_java, repository_types)

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
            ),
        )

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
            if source.path.endswith(".sql"):
                if source.sql_dialect is not None and source.sql_dialect not in _SQLGLOT_DIALECTS:
                    raise JavaSpringAnalysisError("SQL dialect is not in the supported closed set")
            elif source.sql_dialect is not None:
                raise JavaSpringAnalysisError("SQL dialect is valid only for SQL sources")
        return tuple(sorted(sources, key=lambda item: item.path))

    def _classify_framework(
        self, sources: tuple[JavaSpringSource, ...]
    ) -> FrameworkClassification:
        evidence: list[_BuildCoordinate] = []
        dynamic = False
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
                dynamic = True
                continue
            if PurePosixPath(source.path).name == "pom.xml":
                found, unsafe = self._maven_framework_evidence(source, text)
            else:
                found, unsafe = self._gradle_framework_evidence(source, text)
            evidence.extend(found)
            dynamic = dynamic or unsafe

        if dynamic:
            return FrameworkClassification("unsupported", None, ())
        versions: dict[tuple[str, str], set[str]] = {}
        for item in evidence:
            if item.version:
                versions.setdefault((item.group, item.artifact), set()).add(item.version)
        conflicts = {
            coordinate: tuple(sorted(found_versions))
            for coordinate, found_versions in versions.items()
            if len(found_versions) > 1
        }
        if conflicts:
            symbol = ",".join(
                f"{group}:{artifact}={'/'.join(found_versions)}"
                for (group, artifact), found_versions in sorted(conflicts.items())
            )
            self._add_residue(
                "ambiguous-framework-evidence",
                "supported framework coordinates declare conflicting literal versions",
                symbol,
                _whole_file_location(build_sources[0], "build_file"),
            )
            return FrameworkClassification("unsupported", None, ())
        if evidence:
            return FrameworkClassification(
                "supported",
                "spring-data-jpa",
                tuple(sorted({item.rendered for item in evidence})),
            )
        location = (
            _whole_file_location(build_sources[0], "build_file")
            if build_sources
            else _scope_location(sources)
        )
        self._add_residue(
            "unknown-framework",
            "no supported literal Spring Data JPA build coordinate was found",
            "spring-data-jpa",
            location,
        )
        return FrameworkClassification("unsupported", None, ())

    def _maven_framework_evidence(
        self, source: JavaSpringSource, text: str
    ) -> tuple[list[_BuildCoordinate], bool]:
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            self._add_residue(
                "dynamic-framework-evidence",
                "Maven XML declarations outside the literal subset are forbidden",
                "DOCTYPE/ENTITY",
                _whole_file_location(source, "build_file"),
            )
            return [], True
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            self._add_residue(
                "malformed-build-file",
                "Maven build metadata is malformed",
                "pom.xml",
                _whole_file_location(source, "build_file"),
            )
            return [], True

        evidence: list[_BuildCoordinate] = []
        dynamic = False
        for dependency in root.iter():
            if _local_xml_name(dependency.tag) != "dependency":
                continue
            values = {
                _local_xml_name(child.tag): (child.text or "").strip()
                for child in dependency
            }
            group = values.get("groupId", "")
            artifact = values.get("artifactId", "")
            version = values.get("version", "")
            if (group, artifact) not in _SUPPORTED_COORDINATES:
                continue
            if any(_DYNAMIC_BUILD_TOKEN.search(value) for value in (group, artifact, version)):
                dynamic = True
                self._add_residue(
                    "dynamic-framework-evidence",
                    "Spring framework Maven coordinates must be literal",
                    f"{group}:{artifact}:{version}",
                    _whole_file_location(source, "build_file"),
                )
                continue
            evidence.append(_BuildCoordinate(source.path, group, artifact, version))
        return evidence, dynamic

    def _gradle_framework_evidence(
        self, source: JavaSpringSource, text: str
    ) -> tuple[list[_BuildCoordinate], bool]:
        evidence: list[_BuildCoordinate] = []
        dynamic = False
        tokens, malformed = _gradle_tokens(text)
        if malformed:
            self._add_residue(
                "malformed-build-file",
                "Gradle build metadata is outside the bounded literal subset",
                PurePosixPath(source.path).name,
                _whole_file_location(source, "build_file"),
            )
            return [], True
        for token in _gradle_literal_dependencies(tokens):
            coordinate = token.value.strip()
            parts = coordinate.split(":")
            if len(parts) < 2 or tuple(parts[:2]) not in _SUPPORTED_COORDINATES:
                continue
            if len(parts) > 3 or any(_DYNAMIC_BUILD_TOKEN.search(part) for part in parts):
                dynamic = True
                self._add_residue(
                    "dynamic-framework-evidence",
                    "Spring framework Gradle coordinates must be literal",
                    coordinate,
                    _text_location(source, token.start, token.end, "string_literal"),
                )
                continue
            version = parts[2] if len(parts) == 3 else ""
            evidence.append(_BuildCoordinate(source.path, parts[0], parts[1], version))
        return evidence, dynamic

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
        if tree.root_node.has_error:
            error = next(
                (node for node in _walk(tree.root_node) if node.is_error or node.is_missing),
                tree.root_node,
            )
            self._add_residue(
                "malformed-java",
                "Tree-sitter reported malformed Java syntax; the file was quarantined",
                _node_text(error, source.content),
                _node_location(source.path, error),
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

    def _extract_java_facts(
        self,
        parsed_files: list[_ParsedJava],
        framework: FrameworkClassification,
    ) -> set[str]:
        repository_types: set[str] = set()
        for parsed in parsed_files:
            root = parsed.tree.root_node
            for child in root.named_children:
                if child.type == "package_declaration" and child.named_children:
                    self._add_fact(
                        "java.package",
                        _node_text(child.named_children[0], parsed.source.content),
                        (),
                        _node_location(parsed.source.path, child),
                    )
                elif child.type == "import_declaration" and child.named_children:
                    imported = _node_text(child.named_children[0], parsed.source.content)
                    self._add_fact(
                        "java.import",
                        imported,
                        (),
                        _node_location(parsed.source.path, child),
                    )

            for declaration in _type_declarations(root):
                simple_name_node = declaration.child_by_field_name("name")
                if simple_name_node is None:
                    continue
                simple_name = _node_text(simple_name_node, parsed.source.content)
                qualified_name = (
                    f"{parsed.package}.{simple_name}" if parsed.package else simple_name
                )
                extends, implements, generic_nodes = _super_types(
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
                    _node_location(parsed.source.path, declaration),
                )
                annotations = self._emit_annotations(
                    parsed.source, declaration, qualified_name
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
                        if _simple_type(base_type) not in _REPOSITORY_BASES or len(arguments) < 2:
                            continue
                        repository_types.add(simple_name)
                        self._add_fact(
                            "spring.repository-association",
                            qualified_name,
                            (
                                ("baseType", _simple_type(base_type)),
                                ("entityType", arguments[0]),
                                ("idType", arguments[1]),
                            ),
                            _node_location(parsed.source.path, generic),
                        )
                self._emit_members(
                    parsed,
                    declaration,
                    qualified_name,
                    framework_supported=framework.status == "supported",
                )
        return repository_types

    def _emit_annotations(
        self, source: JavaSpringSource, target: Node, target_subject: str
    ) -> dict[str, tuple[tuple[str, str], Node, tuple[str, ...]]]:
        annotations: dict[str, tuple[tuple[str, str], Node, tuple[str, ...]]] = {}
        modifiers = next(
            (child for child in target.named_children if child.type == "modifiers"), None
        )
        if modifiers is None:
            return annotations
        for annotation in (
            child
            for child in modifiers.named_children
            if child.type in {"annotation", "marker_annotation"}
        ):
            name_node = annotation.child_by_field_name("name")
            if name_node is None:
                continue
            name = _node_text(name_node, source.content)
            literal_values, dynamic_values = _annotation_values(annotation, source.content)
            attributes: list[tuple[str, FactValue]] = list(literal_values)
            if dynamic_values:
                attributes.append(("dynamicValues", dynamic_values))
            self._add_fact(
                "java.annotation",
                f"{target_subject}:@{name}",
                attributes,
                _node_location(source.path, annotation),
            )
            annotations[name] = (literal_values, annotation, dynamic_values)
        return annotations

    def _emit_entity_table(
        self,
        source: JavaSpringSource,
        declaration: Node,
        qualified_name: str,
        annotations: dict[str, tuple[tuple[str, str], Node, tuple[str, ...]]],
    ) -> None:
        entity = next(
            (value for name, value in annotations.items() if _simple_type(name) == "Entity"),
            None,
        )
        if entity is None:
            return
        table = next(
            (value for name, value in annotations.items() if _simple_type(name) == "Table"),
            None,
        )
        table_name = qualified_name.rsplit(".", 1)[-1]
        explicit = "false"
        location = _node_location(source.path, declaration)
        if table is not None:
            values = dict(table[0])
            if values.get("name"):
                table_name = values["name"]
                explicit = "true"
                location = _node_location(source.path, table[1])
        self._add_fact(
            "spring.entity-table",
            qualified_name,
            (
                ("explicit", explicit),
                ("table", table_name),
            ),
            location,
        )

    def _emit_members(
        self,
        parsed: _ParsedJava,
        declaration: Node,
        owner: str,
        *,
        framework_supported: bool,
    ) -> None:
        body = declaration.child_by_field_name("body")
        if body is None:
            return
        for member in body.named_children:
            if member.type == "field_declaration":
                field_type = member.child_by_field_name("type")
                for declarator in (
                    child
                    for child in member.named_children
                    if child.type == "variable_declarator"
                ):
                    name_node = declarator.child_by_field_name("name")
                    if name_node is None or field_type is None:
                        continue
                    name = _node_text(name_node, parsed.source.content)
                    self._add_fact(
                        "java.field",
                        f"{owner}#{name}",
                        (("type", _node_text(field_type, parsed.source.content)),),
                        _node_location(parsed.source.path, declarator),
                    )
            elif member.type == "constructor_declaration":
                self._add_fact(
                    "java.constructor",
                    owner,
                    (("parameters", _parameter_signature(member, parsed.source.content)),),
                    _node_location(parsed.source.path, member),
                )
            elif member.type == "method_declaration":
                name_node = member.child_by_field_name("name")
                if name_node is None:
                    continue
                method_name = _node_text(name_node, parsed.source.content)
                return_type = member.child_by_field_name("type")
                subject = f"{owner}#{method_name}"
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
                        ("parameters", _parameter_signature(member, parsed.source.content)),
                    ),
                    _node_location(parsed.source.path, member),
                )
                annotations = self._emit_annotations(parsed.source, member, subject)
                query = next(
                    (
                        value
                        for name, value in annotations.items()
                        if _simple_type(name) == "Query"
                    ),
                    None,
                )
                if query is not None and framework_supported:
                    values = dict(query[0])
                    query_value = values.get("value")
                    if query_value is not None:
                        self._add_fact(
                            "spring.query",
                            subject,
                            (("literal", "true"), ("query", query_value)),
                            _node_location(parsed.source.path, query[1]),
                        )
                    else:
                        symbol = query[2][0] if query[2] else "<missing>"
                        self._add_residue(
                            "dynamic-query",
                            "Spring @Query text is not a string literal",
                            symbol,
                            _node_location(parsed.source.path, query[1]),
                        )

    def _extract_repository_usage(
        self, parsed_files: list[_ParsedJava], repository_types: set[str]
    ) -> None:
        if not repository_types:
            return
        for parsed in parsed_files:
            for declaration in _type_declarations(parsed.tree.root_node):
                name_node = declaration.child_by_field_name("name")
                body = declaration.child_by_field_name("body")
                if name_node is None or body is None:
                    continue
                owner_name = _node_text(name_node, parsed.source.content)
                owner = f"{parsed.package}.{owner_name}" if parsed.package else owner_name
                fields: dict[str, str] = {}
                for member in body.named_children:
                    if member.type != "field_declaration":
                        continue
                    field_type = member.child_by_field_name("type")
                    if field_type is None:
                        continue
                    type_name = _simple_type(_node_text(field_type, parsed.source.content))
                    for declarator in (
                        child
                        for child in member.named_children
                        if child.type == "variable_declarator"
                    ):
                        field_name = declarator.child_by_field_name("name")
                        if field_name is not None:
                            fields[_node_text(field_name, parsed.source.content)] = type_name

                for member in body.named_children:
                    if member.type == "constructor_declaration":
                        parameters = _parameters(member, parsed.source.content)
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
                            self._add_fact(
                                "spring.repository-binding",
                                f"{owner}#{field_name}",
                                (
                                    ("field", field_name),
                                    ("parameter", parameter_name),
                                    ("repositoryType", repository_type),
                                ),
                                _node_location(parsed.source.path, assignment),
                            )
                    elif member.type == "method_declaration":
                        method_name_node = member.child_by_field_name("name")
                        if method_name_node is None:
                            continue
                        enclosing_method = _node_text(
                            method_name_node, parsed.source.content
                        )
                        for invocation in (
                            node
                            for node in _walk(member)
                            if node.type == "method_invocation"
                        ):
                            receiver_node = invocation.child_by_field_name("object")
                            called_node = invocation.child_by_field_name("name")
                            receiver = _receiver_name(receiver_node, parsed.source.content)
                            if (
                                receiver is None
                                or called_node is None
                                or fields.get(receiver) not in repository_types
                            ):
                                continue
                            called = _node_text(called_node, parsed.source.content)
                            self._add_fact(
                                "java.invocation",
                                f"{owner}#{enclosing_method}:{receiver}.{called}",
                                (
                                    ("method", called),
                                    ("receiver", receiver),
                                    ("repositoryType", fields[receiver]),
                                ),
                                _node_location(parsed.source.path, invocation),
                            )

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
            if not _is_create_table_tokens(statement_tokens):
                self._add_residue(
                    "unsupported-sql",
                    "only explicit CREATE TABLE DDL yields schema facts",
                    statement_tokens[0].text,
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
                (("catalog", table.catalog), ("schema", table.db)),
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
        self._residue.append(AnalysisResidue(code, message, symbol, location))


def _validate_source_path(path: str) -> None:
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        raise JavaSpringAnalysisError("source path must be a relative tracked path")
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


def _node_text(node: Node | None, content: bytes) -> str:
    if node is None:
        return ""
    return content[node.start_byte : node.end_byte].decode("utf-8", errors="strict")


def _node_location(path: str, node: Node) -> SourceLocation:
    return SourceLocation(path, node.start_point.row + 1, node.start_byte, node.end_byte, node.type)


def _whole_file_location(source: JavaSpringSource, ast_kind: str) -> SourceLocation:
    return SourceLocation(source.path, 1, 0, len(source.content), ast_kind)


def _scope_location(sources: tuple[JavaSpringSource, ...]) -> SourceLocation:
    if sources:
        return SourceLocation(sources[0].path, 1, 0, 0, "repository_scope")
    return SourceLocation("<repository>", 1, 0, 0, "repository_scope")


def _text_location(
    source: JavaSpringSource, start_character: int, end_character: int, ast_kind: str
) -> SourceLocation:
    text = source.content.decode("utf-8", errors="strict")
    start_byte = len(text[:start_character].encode())
    end_byte = len(text[:end_character].encode())
    line = text.count("\n", 0, start_character) + 1
    return SourceLocation(source.path, line, start_byte, end_byte, ast_kind)


def _local_xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _gradle_tokens(text: str) -> tuple[tuple[_GradleToken, ...], bool]:
    tokens: list[_GradleToken] = []
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
        tokens.append(_GradleToken("symbol", character, index, index + 1))
        index += 1
    return tuple(tokens), False


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
            "dependencies" in block_stack
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


def _simple_type(type_name: str) -> str:
    return type_name.rsplit(".", 1)[-1].split("<", 1)[0]


def _super_types(
    declaration: Node, content: bytes
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[Node, ...]]:
    extends: list[str] = []
    implements: list[str] = []
    generic_nodes: list[Node] = []
    for child in declaration.named_children:
        if child.type in {"extends_interfaces", "superclass"}:
            types = _direct_supertype_nodes(child)
            extends.extend(_node_text(node, content) for node in types)
            generic_nodes.extend(node for node in types if node.type == "generic_type")
        elif child.type in {"super_interfaces", "implements_interfaces"}:
            types = _direct_supertype_nodes(child)
            implements.extend(_node_text(node, content) for node in types)
            generic_nodes.extend(node for node in types if node.type == "generic_type")
    return tuple(extends), tuple(implements), tuple(generic_nodes)


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
    annotation: Node, content: bytes
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
            result[_node_text(name, content)] = _simple_type(_node_text(type_node, content))
    return result


def _constructor_binding(
    assignment: Node,
    content: bytes,
    parameters: dict[str, str],
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
    if fields.get(field_name) != parameters.get(parameter_name):
        return None
    return field_name, parameter_name, fields[field_name]


def _receiver_name(node: Node | None, content: bytes) -> str | None:
    if node is None:
        return None
    if node.type == "identifier":
        return _node_text(node, content)
    if node.type == "field_access":
        field = node.child_by_field_name("field")
        return _node_text(field, content) if field is not None else None
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


def _is_create_table_tokens(tokens: tuple[Token, ...]) -> bool:
    return (
        len(tokens) >= 2
        and tokens[0].token_type == TokenType.CREATE
        and tokens[1].token_type == TokenType.TABLE
    )


def _silent_sqlglot_parse_one(
    dialect: Dialect, statement: str
) -> exp.Expression | None:
    parser_class = dialect.parser_class

    class _SilentParser(parser_class):  # type: ignore[valid-type, misc]
        def _warn_unsupported(self) -> None:
            return

    parsed = _SilentParser(error_level=ErrorLevel.RAISE, dialect=dialect).parse(
        dialect.tokenizer().tokenize(statement), statement
    )
    return parsed[0] if parsed else None


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
    return SourceLocation(path, token.line, start_byte, end_byte, ast_kind)
