"""The Java half of the runtime verification loop.

The Python seam worked because the analyzed module declares dataset primitives it never
defines, so binding them *is* the instrumentation. Java is compiled and has no such hole,
so the seam has to be different: a **test-scoped recording proxy** that implements the
repository interface and records every invocation. That is exactly how Spring Data is
stubbed in a test, and it needs no Spring on the classpath at run time.

To compile the production sources in isolation the harness also emits **stub declarations**
for the framework types they import (`jakarta.persistence.Entity` / `Table`,
`org.springframework.data.jpa.repository.JpaRepository` / `Query`). Emitting the stubs is
part of generating the test harness, not a modification of the repository: the production
sources are compiled byte-for-byte as committed.

    entity @Table ──► table name
    repository interface ──► recording proxy ──► observed (method, table, operation)
    service constructor ──► injected proxy ──► generated test calls each public method

Everything here is text generation and comparison. Compiling and running the harness is the
caller's decision, and belongs in the isolated worker for untrusted sources — the same
boundary the Python runner states.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


MAX_TYPES = 256
HARNESS_PACKAGE = "example"

# Spring Data method-name prefixes and the access they imply. Anything outside this map is
# recorded as an observation with an UNKNOWN operation rather than guessed into a lineage
# edge, mirroring the "quarantine over guessing" rule.
_READ_PREFIXES = ("find", "get", "read", "query", "count", "exists", "search", "stream")
_WRITE_PREFIXES = ("save", "insert", "update", "delete", "remove", "persist", "flush")


class JavaHarnessError(RuntimeError):
    """A bounded failure while generating or interpreting the Java harness."""


@dataclass(frozen=True, slots=True)
class JavaEntity:
    """An `@Entity` class and the table its `@Table` annotation pins."""

    type_name: str
    table: str


@dataclass(frozen=True, slots=True)
class JavaRepository:
    """A Spring Data repository interface and the entity it is typed on."""

    type_name: str
    entity_type: str
    methods: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JavaInjectionSite:
    """A class that takes repositories through its constructor, and its callable methods."""

    type_name: str
    repository_fields: tuple[tuple[str, str], ...]  # (field name, repository type)
    methods: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JavaObservation:
    """One repository call witnessed while the generated harness ran."""

    repository_type: str
    method: str
    table: str
    operation: str
    fields: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return (self.table, self.operation)


def classify_operation(method: str) -> str:
    lowered = method.lower()
    if lowered.startswith(_WRITE_PREFIXES):
        return "WRITE"
    if lowered.startswith(_READ_PREFIXES):
        return "READ"
    return "UNKNOWN"


# --------------------------------------------------------------------------------------
# Reading the shape of the analyzed sources
# --------------------------------------------------------------------------------------

_ENTITY = re.compile(
    r"@Entity\b[\s\S]{0,400}?@Table\s*\(\s*name\s*=\s*\"([A-Za-z0-9_.]+)\"[\s\S]{0,400}?"
    r"\b(?:class)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
_REPOSITORY = re.compile(
    r"\binterface\s+([A-Za-z_][A-Za-z0-9_]*)\s+extends\s+JpaRepository\s*<\s*"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*,"
)
_REPO_METHOD = re.compile(r"^\s*(?:[A-Za-z_][\w<>,.\[\]\s]*?)\s+([a-z][A-Za-z0-9_]*)\s*\(", re.M)
_CONSTRUCTOR_FIELD = re.compile(r"private\s+final\s+([A-Za-z_][A-Za-z0-9_]*)\s+([a-z][A-Za-z0-9_]*)\s*;")
_PUBLIC_METHOD = re.compile(
    r"public\s+(?!class\b|interface\b)[A-Za-z_][\w<>,.\[\]]*\s+([a-z][A-Za-z0-9_]*)\s*\(([^)]*)\)"
)


def read_entities(sources: Mapping[str, str]) -> tuple[JavaEntity, ...]:
    found: list[JavaEntity] = []
    for text in sources.values():
        # A commented-out @Entity must not register a table; strip comments first.
        for table, type_name in _ENTITY.findall(_strip_comments(text)):
            found.append(JavaEntity(type_name=type_name, table=table))
    return tuple(sorted(set(found), key=lambda item: item.type_name))[:MAX_TYPES]


def read_repositories(sources: Mapping[str, str]) -> tuple[JavaRepository, ...]:
    found: list[JavaRepository] = []
    for text in sources.values():
        stripped = _strip_comments(text)
        for type_name, entity in _REPOSITORY.findall(stripped):
            body = stripped.split("{", 1)[1] if "{" in stripped else ""
            methods = tuple(
                sorted({name for name in _REPO_METHOD.findall(body) if name != type_name})
            )
            found.append(
                JavaRepository(type_name=type_name, entity_type=entity, methods=methods)
            )
    return tuple(sorted(found, key=lambda item: item.type_name))[:MAX_TYPES]


def read_injection_sites(
    sources: Mapping[str, str], repositories: Sequence[JavaRepository]
) -> tuple[JavaInjectionSite, ...]:
    known = {repository.type_name for repository in repositories}
    sites: list[JavaInjectionSite] = []
    for path, text in sources.items():
        stripped = _strip_comments(text)
        fields = tuple(
            (field, type_name)
            for type_name, field in _CONSTRUCTOR_FIELD.findall(stripped)
            if type_name in known
        )
        if not fields:
            continue
        type_name = path.rsplit("/", 1)[-1].removesuffix(".java")
        methods = tuple(
            sorted(
                {
                    name
                    for name, arguments in _PUBLIC_METHOD.findall(stripped)
                    if name != type_name and _argument_count(arguments) <= 1
                }
            )
        )
        sites.append(
            JavaInjectionSite(type_name=type_name, repository_fields=fields, methods=methods)
        )
    return tuple(sorted(sites, key=lambda item: item.type_name))


def _argument_count(arguments: str) -> int:
    arguments = arguments.strip()
    return 0 if not arguments else arguments.count(",") + 1


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*[\s\S]*?\*/", " ", text)
    return re.sub(r"//[^\n]*", " ", text)


# --------------------------------------------------------------------------------------
# Generating the harness
# --------------------------------------------------------------------------------------


def generate_stub_sources() -> dict[str, str]:
    """Framework declarations so the production sources compile with nothing on the path."""
    return {
        "jakarta/persistence/Entity.java": (
            "package jakarta.persistence;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.TYPE)\n"
            "public @interface Entity {}\n"
        ),
        "jakarta/persistence/Table.java": (
            "package jakarta.persistence;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.TYPE)\n"
            "public @interface Table { String name(); }\n"
        ),
        "org/springframework/data/jpa/repository/JpaRepository.java": (
            "package org.springframework.data.jpa.repository;\n"
            "import java.util.List;\nimport java.util.Optional;\n"
            "public interface JpaRepository<T, ID> {\n"
            "    Optional<T> findById(ID id);\n"
            "    List<T> findAll();\n"
            "    T save(T entity);\n"
            "    void deleteById(ID id);\n"
            "}\n"
        ),
        "org/springframework/data/jpa/repository/Query.java": (
            "package org.springframework.data.jpa.repository;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.METHOD)\n"
            "public @interface Query { String value(); }\n"
        ),
    }


def generate_recorder_source() -> str:
    """The instrumentation: a proxy that records every repository invocation."""
    return (
        "package harness;\n"
        "import java.lang.reflect.*;\nimport java.util.*;\n"
        "import jakarta.persistence.Table;\n\n"
        "/** Records repository calls and resolves the table from the entity's @Table. */\n"
        "public final class Recorder implements InvocationHandler {\n"
        "    public static final List<String> OBSERVED = new ArrayList<>();\n"
        "    private final String repositoryType;\n"
        "    private final String table;\n"
        "    private final String fields;\n\n"
        "    private Recorder(String repositoryType, String table, String fields) {\n"
        "        this.repositoryType = repositoryType;\n"
        "        this.table = table;\n"
        "        this.fields = fields;\n"
        "    }\n\n"
        "    public static Object proxy(Class<?> repository, Class<?> entity) {\n"
        "        Table annotation = entity.getAnnotation(Table.class);\n"
        "        String table = annotation == null ? \"UNMAPPED\" : annotation.name();\n"
        "        List<String> names = new ArrayList<>();\n"
        "        for (Field field : entity.getDeclaredFields()) {\n"
        "            if (field.isSynthetic() || Modifier.isStatic(field.getModifiers())) continue;\n"
        "            names.add(field.getName());\n"
        "        }\n"
        "        String fields = String.join(\",\", names);\n"
        "        return Proxy.newProxyInstance(\n"
        "            repository.getClassLoader(), new Class<?>[]{repository},\n"
        "            new Recorder(repository.getSimpleName(), table, fields));\n"
        "    }\n\n"
        "    @Override\n"
        "    public Object invoke(Object p, Method method, Object[] args) {\n"
        "        OBSERVED.add(repositoryType + \"\\t\" + method.getName() + \"\\t\" + table"
        " + \"\\t\" + fields);\n"
        "        Class<?> type = method.getReturnType();\n"
        "        if (type == Optional.class) return Optional.empty();\n"
        "        if (type == List.class) return List.of();\n"
        "        if (type.isPrimitive()) return type == boolean.class ? Boolean.FALSE : 0;\n"
        "        return null;\n"
        "    }\n"
        "}\n"
    )


def generate_test_source(
    entities: Sequence[JavaEntity],
    repositories: Sequence[JavaRepository],
    sites: Sequence[JavaInjectionSite],
) -> str:
    """A generated harness that exercises every injected repository method."""
    entity_table = {entity.type_name: entity for entity in entities}
    by_name = {repository.type_name: repository for repository in repositories}

    lines = [
        "package harness;",
        "import java.lang.reflect.*;",
        "import example.*;",
        "",
        "/** Generated: constructs each injection site with recording proxies. */",
        "public final class GeneratedRuntimeTest {",
        "    public static void main(String[] args) throws Exception {",
    ]
    for site in sites:
        types = [by_name[type_name] for _field, type_name in site.repository_fields]
        if any(repository.entity_type not in entity_table for repository in types):
            continue
        arguments = ", ".join(
            f"Recorder.proxy({repository.type_name}.class, "
            f"{repository.entity_type}.class)"
            for repository in types
        )
        casts = ", ".join(f"({repository.type_name}) p{index}" for index, repository in enumerate(types))
        for index, repository in enumerate(types):
            lines.append(
                f"        Object p{index} = Recorder.proxy({repository.type_name}.class, "
                f"{repository.entity_type}.class);"
            )
        lines.append(f"        {site.type_name} site = new {site.type_name}({casts});")
        for method in site.methods:
            # A generated call must never fail the harness; a throwing method is still
            # evidence that the call reached the repository.
            lines.append("        try {")
            lines.append(f"            invoke(site, \"{method}\");")
            lines.append("        } catch (Throwable ignored) { }")
        del arguments
    lines += [
        "        for (String observation : Recorder.OBSERVED) {",
        "            System.out.println(\"OBSERVED\\t\" + observation);",
        "        }",
        "    }",
        "",
        "    private static void invoke(Object target, String name) throws Exception {",
        "        for (Method method : target.getClass().getMethods()) {",
        "            if (!method.getName().equals(name) || method.getParameterCount() > 1) continue;",
        "            Object[] arguments = new Object[method.getParameterCount()];",
        "            for (int i = 0; i < arguments.length; i++) {",
        "                Class<?> type = method.getParameterTypes()[i];",
        "                arguments[i] = type == Integer.class || type == int.class ? Integer.valueOf(1)",
        "                    : type == String.class ? \"generated\" : null;",
        "            }",
        "            method.invoke(target, arguments);",
        "            return;",
        "        }",
        "    }",
        "}",
        "",
    ]
    return "\n".join(lines)


def generate_harness(sources: Mapping[str, str]) -> dict[str, str]:
    """Emit every file the harness needs, keyed by relative path."""
    entities = read_entities(sources)
    repositories = read_repositories(sources)
    sites = read_injection_sites(sources, repositories)
    if not repositories or not sites:
        raise JavaHarnessError("no injected Spring Data repository was found to instrument")
    harness = generate_stub_sources()
    harness["harness/Recorder.java"] = generate_recorder_source()
    harness["harness/GeneratedRuntimeTest.java"] = generate_test_source(
        entities, repositories, sites
    )
    return harness


# --------------------------------------------------------------------------------------
# Interpreting the run
# --------------------------------------------------------------------------------------


def parse_observations(stdout: str) -> tuple[JavaObservation, ...]:
    observations: list[JavaObservation] = []
    for line in stdout.splitlines():
        if not line.startswith("OBSERVED\t"):
            continue
        parts = line.split("\t")
        if len(parts) not in (4, 5):
            raise JavaHarnessError("harness emitted a malformed observation")
        fields: tuple[str, ...] = ()
        if len(parts) == 5:
            _marker, repository_type, method, table, raw_fields = parts
            if raw_fields:
                fields = tuple(raw_fields.split(","))
        else:
            _marker, repository_type, method, table = parts
        observations.append(
            JavaObservation(
                repository_type=repository_type,
                method=method,
                table=table,
                operation=classify_operation(method),
                fields=fields,
            )
        )
    return tuple(observations)


@dataclass(frozen=True, slots=True)
class JavaVerification:
    corroborated: tuple[tuple[str, str], ...]
    static_only: tuple[tuple[str, str], ...]
    runtime_only: tuple[JavaObservation, ...]

    @property
    def verdict(self) -> str:
        if not self.corroborated and not self.static_only:
            return "NOT_PROVIDED"
        return "PARTIALLY_CORROBORATED" if self.static_only else "CORROBORATED"


def verify_java_lineage(
    static_access: Sequence[tuple[str, str]],
    observations: Sequence[JavaObservation],
) -> JavaVerification:
    """Compare SCA's (table, operation) claims with what the harness actually invoked.

    Runtime never invents an edge: an observation with no static counterpart is reported as
    runtime-only, and an UNKNOWN operation can never corroborate anything.
    """
    observed = {
        observation.key: observation
        for observation in observations
        if observation.operation != "UNKNOWN"
    }
    corroborated = tuple(claim for claim in static_access if claim in observed)
    static_only = tuple(claim for claim in static_access if claim not in observed)
    matched = set(corroborated)
    runtime_only = tuple(
        observation for key, observation in observed.items() if key not in matched
    )
    return JavaVerification(
        corroborated=corroborated, static_only=static_only, runtime_only=runtime_only
    )
