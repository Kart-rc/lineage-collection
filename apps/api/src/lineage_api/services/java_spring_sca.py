from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from bisect import bisect_right
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
_ENTITY_FQN = "jakarta.persistence.Entity"
_TABLE_FQN = "jakarta.persistence.Table"
_JPA_REPOSITORY_FQN = "org.springframework.data.jpa.repository.JpaRepository"
_REPOSITORY_FQN = "org.springframework.data.repository.Repository"
_QUERY_FQN = "org.springframework.data.jpa.repository.Query"
_AUTOWIRED_FQN = "org.springframework.beans.factory.annotation.Autowired"
_APPROVED_FRAMEWORK_SYMBOLS = frozenset(
    {
        _ENTITY_FQN,
        _TABLE_FQN,
        _JPA_REPOSITORY_FQN,
        _REPOSITORY_FQN,
        _QUERY_FQN,
        _AUTOWIRED_FQN,
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
_DYNAMIC_BUILD_TOKEN = re.compile(r"(?:\$\{|\$[A-Za-z_]|\+)")
_SEMANTIC_VERSION = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")
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
        "ambiguous-framework-evidence",
        "ambiguous-framework-symbol",
        "ambiguous-repository-injection",
        "dynamic-framework-evidence",
        "dynamic-query",
        "dynamic-sql-identifier",
        "dynamic-table-mapping",
        "incompatible-framework-cell",
        "invalid-build-encoding",
        "invalid-java-encoding",
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
        "shadowed-framework-symbol",
        "shadowed-repository-receiver",
        "unbound-repository-receiver",
        "unknown-framework",
        "unresolved-framework-symbol",
        "unresolved-repository-entity",
        "unresolved-table-mapping",
        "unsupported-boot-version",
        "unsupported-direct-spring-data-jpa",
        "unsupported-h2-construct",
        "unsupported-jpa-version",
        "unsupported-sql",
        "wildcard-framework-symbol",
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

        for source in ordered:
            if source.path.endswith(".java"):
                parsed = self._parse_java(source)
                if parsed is not None:
                    parsed_java.append(parsed)
            elif source.path.endswith(".sql"):
                if self._parse_sql(source):
                    sql_files_parsed += 1

        repository_types, symbols = self._extract_java_facts(parsed_java, framework)
        if framework.status == "supported":
            self._extract_repository_usage(parsed_java, repository_types, symbols)

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
            if source.path.endswith(".sql"):
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
                closure = self._maven_framework_evidence(source, text)
            else:
                closure = self._gradle_framework_evidence(source, text)
            closures.append(closure)

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
            if not closure.jpa:
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
        self, source: JavaSpringSource, text: str
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
        parent = _xml_direct_child(root, "parent")
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
                boot_versions = {item.version for item in boot}
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
            source.path, tuple(boot), tuple(jpa), relevant, invalid
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
                    source.path, "data-jpa", parts[0], parts[1], parts[2]
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
                self._emit_members(
                    parsed,
                    declaration,
                    qualified_name,
                    symbols,
                    framework_supported=framework.status == "supported",
                )
        return repository_types, symbols

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
            literal_values, dynamic_values = _annotation_values(
                annotation, parsed.source.content
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
        entity = next(
            (item for item in annotations if item.resolved_fqn == _ENTITY_FQN),
            None,
        )
        if entity is None:
            return
        syntactic_table = next(
            (item for item in annotations if _simple_type(item.raw_name) == "Table"),
            None,
        )
        table_name = qualified_name.rsplit(".", 1)[-1]
        explicit = "false"
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
            values = dict(syntactic_table.literal_values)
            if syntactic_table.dynamic_values:
                self._add_residue(
                    "dynamic-table-mapping",
                    "present @Table name is dynamic and cannot default safely",
                    syntactic_table.dynamic_values[0],
                    self._node_location(source.path, syntactic_table.node),
                )
                return
            if values.get("name"):
                table_name = values["name"]
                explicit = "true"
                location = self._node_location(source.path, syntactic_table.node)
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
                        self._add_fact(
                            "spring.query",
                            subject,
                            (("literal", "true"), ("query", query_value)),
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
            self._add_residue(
                "wildcard-framework-symbol",
                "wildcard imports cannot prove a framework-sensitive symbol",
                simple_name,
                location,
            )
            return None
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
                            self._add_fact(
                                "java.invocation",
                                f"{owner}#{enclosing_method}:{receiver}.{called}",
                                (
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
                    ("dialect", sqlglot_dialect),
                    (
                        "dialectMode",
                        "h2-postgres-compatibility"
                        if source.sql_dialect == "h2"
                        else "native",
                    ),
                    ("schema", table.db),
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
            scope = _pattern_binding_scope(node, body)
            if scope is not None:
                add(name, name.end_byte if name is not None else 0, scope.end_byte)

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


def _pattern_binding_scope(pattern: Node, boundary: Node) -> Node | None:
    current = pattern.parent
    while current is not None and current.id != boundary.id:
        if current.type == "switch_rule":
            return current
        if current.type == "if_statement":
            return current.child_by_field_name("consequence") or current
        if current.type in {"for_statement", "while_statement"}:
            return current.child_by_field_name("body") or current
        current = current.parent
    return None


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
    if fields.get(field_name) != parameters.get(parameter_name):
        return None
    return field_name, parameter_name, fields[field_name]


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
    return SourceLocation(
        path,
        token.line,
        start_byte,
        end_byte,
        ast_kind,
        f"sql.token[{token.start}:{token.end + 1}]/{ast_kind}",
    )
