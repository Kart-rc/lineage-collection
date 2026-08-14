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
    """A Spring Data repository interface and the entity it is typed on.

    `parents` records the other interfaces in its extends clause -- petclinic-rest's
    `SpringDataOwnerRepository extends OwnerRepository, Repository<Owner, Integer>`
    is injected everywhere as `OwnerRepository`, so instrumentation must be able to
    resolve the project's own abstract interface back to this repository."""

    type_name: str
    entity_type: str
    methods: tuple[str, ...]
    parents: tuple[str, ...] = ()


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
# Every Spring Data base a real repository interface extends -- petclinic's
# VetRepository uses the minimal `Repository` marker, not JpaRepository -- and the
# base may sit anywhere in the extends clause (petclinic-rest puts the project's own
# abstract interface first: `extends OwnerRepository, Repository<Owner, Integer>`).
_SPRING_DATA_BASES = (
    "JpaRepository",
    "ListCrudRepository",
    "CrudRepository",
    "PagingAndSortingRepository",
    "Repository",
)
_REPOSITORY = re.compile(
    r"\binterface\s+([A-Za-z_][A-Za-z0-9_]*)\s+extends\s+([^{]*)\{"
)
_REPOSITORY_BASE = re.compile(
    r"\b(?:" + "|".join(_SPRING_DATA_BASES) + r")\s*<\s*([A-Za-z_][A-Za-z0-9_]*)\s*,"
)
_REPO_METHOD = re.compile(r"^\s*(?:[A-Za-z_][\w<>,.\[\]\s]*?)\s+([a-z][A-Za-z0-9_]*)\s*\(", re.M)
_CONSTRUCTOR_FIELD = re.compile(r"private\s+final\s+([A-Za-z_][A-Za-z0-9_]*)\s+([a-z][A-Za-z0-9_]*)\s*;")
_PUBLIC_METHOD = re.compile(
    r"public\s+(?!class\b|interface\b)[A-Za-z_][\w<>,.\[\]]*\s+([a-z][A-Za-z0-9_]*)\s*\(([^)]*)\)"
)
_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)


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
        for type_name, clause in _REPOSITORY.findall(stripped):
            base = _REPOSITORY_BASE.search(clause)
            if base is None:
                continue
            # The clause's other interfaces (generics erased) are the parents an
            # injection site may be typed against; Spring Data bases are not.
            flattened = clause
            while "<" in flattened:
                reduced = re.sub(r"<[^<>]*>", " ", flattened)
                if reduced == flattened:
                    break
                flattened = reduced
            parents = tuple(
                name
                for name in re.findall(r"\b([A-Z][A-Za-z0-9_]*)\b", flattened)
                if name not in _SPRING_DATA_BASES
            )
            body = stripped.split("{", 1)[1] if "{" in stripped else ""
            methods = tuple(
                sorted({name for name in _REPO_METHOD.findall(body) if name != type_name})
            )
            found.append(
                JavaRepository(
                    type_name=type_name,
                    entity_type=base.group(1),
                    methods=methods,
                    parents=parents,
                )
            )
    return tuple(sorted(found, key=lambda item: item.type_name))[:MAX_TYPES]


def read_injection_sites(
    sources: Mapping[str, str], repositories: Sequence[JavaRepository]
) -> tuple[JavaInjectionSite, ...]:
    known = repository_resolution(repositories)
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


def repository_resolution(
    repositories: Sequence[JavaRepository],
) -> dict[str, JavaRepository]:
    """Map every name an injection site may be typed against -- the Spring Data
    repository itself or one of its declared parent interfaces -- to the repository
    that instruments it. A parent claimed by two repositories resolves to neither:
    guessing which proxy satisfies the field would fabricate evidence."""
    resolution: dict[str, JavaRepository] = {}
    ambiguous: set[str] = set()
    for repository in repositories:
        for name in (repository.type_name, *repository.parents):
            if name in resolution and resolution[name] is not repository:
                ambiguous.add(name)
            resolution[name] = repository
    for name in ambiguous:
        del resolution[name]
    return resolution


def _argument_count(arguments: str) -> int:
    arguments = arguments.strip()
    return 0 if not arguments else arguments.count(",") + 1


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*[\s\S]*?\*/", " ", text)
    return re.sub(r"//[^\n]*", " ", text)


def qualified_type_names(sources: Mapping[str, str]) -> dict[str, str]:
    """Map each source file's top-level type's simple name to its real fully-qualified
    name, read off its own `package` declaration -- never a fixed harness package. A
    real checkout scatters entities, repositories, and injection sites across many
    packages, so the generated harness needs each type's real package to load it."""
    qualified: dict[str, str] = {}
    for path, text in sources.items():
        simple = path.rsplit("/", 1)[-1].removesuffix(".java")
        match = _PACKAGE.search(_strip_comments(text))
        qualified[simple] = f"{match.group(1)}.{simple}" if match else simple
    return qualified


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
            "public @interface Table { String name(); UniqueConstraint[] uniqueConstraints() default {}; }\n"
        ),
        "org/springframework/data/jpa/repository/JpaRepository.java": (
            "package org.springframework.data.jpa.repository;\n"
            "import java.util.List;\nimport java.util.Optional;\n"
            "public interface JpaRepository<T, ID> {\n"
            "    Optional<T> findById(ID id);\n"
            "    List<T> findAll();\n"
            "    T save(T entity);\n"
            "    T saveAndFlush(T entity);\n"
            "    void deleteById(ID id);\n"
            "}\n"
        ),
        "org/springframework/data/jpa/repository/Query.java": (
            "package org.springframework.data.jpa.repository;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.METHOD)\n"
            "public @interface Query { String value(); String countQuery() default \"\"; boolean nativeQuery() default false; }\n"
        ),
        # The remaining stubs below are the framework surface a real Spring Data JPA
        # REST controller/entity actually imports (validation, JPA column mapping,
        # web MVC mappings, logging, metrics, JSON formatting) -- annotations only, no
        # runtime behaviour, so the production source compiles unmodified with nothing
        # else on the classpath. Each member exists because a real production source
        # (petclinic's visits-service) references it; none is invented ahead of need.
        "jakarta/persistence/Id.java": (
            "package jakarta.persistence;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface Id {}\n"
        ),
        "jakarta/persistence/GenerationType.java": (
            "package jakarta.persistence;\n"
            "public enum GenerationType { AUTO, IDENTITY, SEQUENCE, TABLE }\n"
        ),
        "jakarta/persistence/GeneratedValue.java": (
            "package jakarta.persistence;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface GeneratedValue {\n"
            "    GenerationType strategy() default GenerationType.AUTO;\n"
            "    String generator() default \"\";\n"
            "}\n"
        ),
        "jakarta/persistence/Column.java": (
            "package jakarta.persistence;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface Column { String name() default \"\"; int length() default 255; String columnDefinition() default \"\"; }\n"
        ),
        "jakarta/persistence/TemporalType.java": (
            "package jakarta.persistence;\n"
            "public enum TemporalType { DATE, TIME, TIMESTAMP }\n"
        ),
        "jakarta/persistence/Temporal.java": (
            "package jakarta.persistence;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface Temporal { TemporalType value(); }\n"
        ),
        "jakarta/validation/Valid.java": (
            "package jakarta.validation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface Valid {}\n"
        ),
        "jakarta/validation/constraints/Min.java": (
            "package jakarta.validation.constraints;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface Min { long value(); }\n"
        ),
        "jakarta/validation/constraints/Size.java": (
            "package jakarta.validation.constraints;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface Size { int min() default 0; int max() default Integer.MAX_VALUE; }\n"
        ),
        "org/springframework/http/HttpStatus.java": (
            "package org.springframework.http;\n"
            "public enum HttpStatus { OK, CREATED, NO_CONTENT, BAD_REQUEST, NOT_FOUND, "
            "INTERNAL_SERVER_ERROR }\n"
        ),
        "org/springframework/web/bind/annotation/RestController.java": (
            "package org.springframework.web.bind.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.TYPE)\n"
            "public @interface RestController {}\n"
        ),
        "org/springframework/web/bind/annotation/RequestMapping.java": (
            "package org.springframework.web.bind.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface RequestMapping { String[] value() default {}; }\n"
        ),
        "org/springframework/web/bind/annotation/GetMapping.java": (
            "package org.springframework.web.bind.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface GetMapping { String[] value() default {}; }\n"
        ),
        "org/springframework/web/bind/annotation/PostMapping.java": (
            "package org.springframework.web.bind.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface PostMapping { String[] value() default {}; }\n"
        ),
        "org/springframework/web/bind/annotation/PutMapping.java": (
            "package org.springframework.web.bind.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface PutMapping { String[] value() default {}; }\n"
        ),
        "org/springframework/web/bind/annotation/DeleteMapping.java": (
            "package org.springframework.web.bind.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface DeleteMapping { String[] value() default {}; }\n"
        ),
        "org/springframework/web/bind/annotation/PathVariable.java": (
            "package org.springframework.web.bind.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface PathVariable { String value() default \"\"; "
            "String name() default \"\"; boolean required() default true; }\n"
        ),
        "org/springframework/web/bind/annotation/RequestParam.java": (
            "package org.springframework.web.bind.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface RequestParam { String value() default \"\"; "
            "String name() default \"\"; String defaultValue() default \"\"; "
            "boolean required() default true; }\n"
        ),
        "org/springframework/web/bind/annotation/RequestBody.java": (
            "package org.springframework.web.bind.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface RequestBody {}\n"
        ),
        "org/springframework/web/bind/annotation/ResponseStatus.java": (
            "package org.springframework.web.bind.annotation;\n"
            "import java.lang.annotation.*;\n"
            "import org.springframework.http.HttpStatus;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface ResponseStatus { HttpStatus value() default HttpStatus.OK; }\n"
        ),
        "org/slf4j/Logger.java": (
            "package org.slf4j;\n"
            "public interface Logger {\n"
            "    void info(String format, Object... arguments);\n"
            "    void warn(String format, Object... arguments);\n"
            "    void error(String format, Object... arguments);\n"
            "    void debug(String format, Object... arguments);\n"
            "}\n"
        ),
        "org/slf4j/LoggerFactory.java": (
            "package org.slf4j;\n"
            "public final class LoggerFactory {\n"
            "    private static final Logger NOOP = new Logger() {\n"
            "        public void info(String format, Object... arguments) {}\n"
            "        public void warn(String format, Object... arguments) {}\n"
            "        public void error(String format, Object... arguments) {}\n"
            "        public void debug(String format, Object... arguments) {}\n"
            "    };\n"
            "    public static Logger getLogger(Class<?> type) { return NOOP; }\n"
            "}\n"
        ),
        "io/micrometer/core/annotation/Timed.java": (
            "package io.micrometer.core.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface Timed { String value() default \"\"; }\n"
        ),
        "com/fasterxml/jackson/annotation/JsonFormat.java": (
            "package com.fasterxml.jackson.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface JsonFormat { String pattern() default \"\"; }\n"
        ),
        # The stubs below are the surface the main spring-petclinic repository's
        # entities and controllers import: superclass mapping (@MappedSuperclass),
        # the full JPA relationship set, Spring Data commons bases and paging,
        # Spring MVC model/binding/redirect types, and the small behavioural
        # classes (ToStringCreator, ModelAndView, PageRequest) their method bodies
        # construct. Behavioural members return `this`/empty values only -- no
        # framework semantics are imitated beyond what compiling requires.
        "jakarta/persistence/MappedSuperclass.java": (
            "package jakarta.persistence;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.TYPE)\n"
            "public @interface MappedSuperclass {}\n"
        ),
        "jakarta/persistence/FetchType.java": (
            "package jakarta.persistence;\n"
            "public enum FetchType { LAZY, EAGER }\n"
        ),
        "jakarta/persistence/CascadeType.java": (
            "package jakarta.persistence;\n"
            "public enum CascadeType { ALL, PERSIST, MERGE, REMOVE, REFRESH, DETACH }\n"
        ),
        "jakarta/persistence/JoinColumn.java": (
            "package jakarta.persistence;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface JoinColumn {\n"
            "    String name() default \"\";\n"
            "    String referencedColumnName() default \"\";\n"
            "    boolean nullable() default true;\n"
            "}\n"
        ),
        "jakarta/persistence/JoinTable.java": (
            "package jakarta.persistence;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface JoinTable {\n"
            "    String name() default \"\";\n"
            "    JoinColumn[] joinColumns() default {};\n"
            "    JoinColumn[] inverseJoinColumns() default {};\n"
            "}\n"
        ),
        "jakarta/persistence/ManyToOne.java": (
            "package jakarta.persistence;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface ManyToOne {\n"
            "    FetchType fetch() default FetchType.EAGER;\n"
            "    CascadeType[] cascade() default {};\n"
            "    boolean optional() default true;\n"
            "}\n"
        ),
        "jakarta/persistence/OneToOne.java": (
            "package jakarta.persistence;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface OneToOne {\n"
            "    FetchType fetch() default FetchType.EAGER;\n"
            "    CascadeType[] cascade() default {};\n"
            "    String mappedBy() default \"\";\n"
            "}\n"
        ),
        "jakarta/persistence/OneToMany.java": (
            "package jakarta.persistence;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface OneToMany {\n"
            "    FetchType fetch() default FetchType.LAZY;\n"
            "    CascadeType[] cascade() default {};\n"
            "    String mappedBy() default \"\";\n"
            "    boolean orphanRemoval() default false;\n"
            "}\n"
        ),
        "jakarta/persistence/ManyToMany.java": (
            "package jakarta.persistence;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface ManyToMany {\n"
            "    FetchType fetch() default FetchType.LAZY;\n"
            "    CascadeType[] cascade() default {};\n"
            "    String mappedBy() default \"\";\n"
            "}\n"
        ),
        "jakarta/persistence/OrderBy.java": (
            "package jakarta.persistence;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface OrderBy { String value() default \"\"; }\n"
        ),
        "jakarta/persistence/Transient.java": (
            "package jakarta.persistence;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface Transient {}\n"
        ),
        "jakarta/validation/constraints/NotBlank.java": (
            "package jakarta.validation.constraints;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface NotBlank { String message() default \"\"; }\n"
        ),
        "jakarta/validation/constraints/NotEmpty.java": (
            "package jakarta.validation.constraints;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface NotEmpty { String message() default \"\"; }\n"
        ),
        "jakarta/validation/constraints/NotNull.java": (
            "package jakarta.validation.constraints;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface NotNull { String message() default \"\"; }\n"
        ),
        "jakarta/validation/constraints/Pattern.java": (
            "package jakarta.validation.constraints;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface Pattern { String regexp(); String message() default \"\"; }\n"
        ),
        "jakarta/validation/constraints/Digits.java": (
            "package jakarta.validation.constraints;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface Digits { int integer(); int fraction(); }\n"
        ),
        "jakarta/xml/bind/annotation/XmlRootElement.java": (
            "package jakarta.xml.bind.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.TYPE)\n"
            "public @interface XmlRootElement { String name() default \"\"; }\n"
        ),
        "jakarta/xml/bind/annotation/XmlElement.java": (
            "package jakarta.xml.bind.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface XmlElement { String name() default \"\"; }\n"
        ),
        "org/springframework/core/style/ToStringCreator.java": (
            "package org.springframework.core.style;\n"
            "public class ToStringCreator {\n"
            "    public ToStringCreator(Object target) {}\n"
            "    public ToStringCreator append(String name, Object value) { return this; }\n"
            "}\n"
        ),
        "org/springframework/format/annotation/DateTimeFormat.java": (
            "package org.springframework.format.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface DateTimeFormat {\n"
            "    String pattern() default \"\";\n"
            "    ISO iso() default ISO.NONE;\n"
            "    enum ISO { DATE, TIME, DATE_TIME, NONE }\n"
            "}\n"
        ),
        "org/springframework/format/Formatter.java": (
            "package org.springframework.format;\n"
            "import java.text.ParseException;\n"
            "import java.util.Locale;\n"
            "public interface Formatter<T> {\n"
            "    T parse(String text, Locale locale) throws ParseException;\n"
            "    String print(T object, Locale locale);\n"
            "}\n"
        ),
        "org/springframework/data/domain/Page.java": (
            "package org.springframework.data.domain;\n"
            "import java.util.List;\n"
            "public interface Page<T> extends Iterable<T> {\n"
            "    List<T> getContent();\n"
            "    int getTotalPages();\n"
            "    long getTotalElements();\n"
            "    boolean isEmpty();\n"
            "}\n"
        ),
        "org/springframework/data/domain/Pageable.java": (
            "package org.springframework.data.domain;\n"
            "public interface Pageable {}\n"
        ),
        "org/springframework/data/domain/PageRequest.java": (
            "package org.springframework.data.domain;\n"
            "public class PageRequest implements Pageable {\n"
            "    private PageRequest() {}\n"
            "    public static PageRequest of(int page, int size) { return new PageRequest(); }\n"
            "}\n"
        ),
        "org/springframework/data/repository/Repository.java": (
            "package org.springframework.data.repository;\n"
            "public interface Repository<T, ID> {}\n"
        ),
        "org/springframework/data/repository/CrudRepository.java": (
            "package org.springframework.data.repository;\n"
            "import java.util.Optional;\n"
            "public interface CrudRepository<T, ID> extends Repository<T, ID> {\n"
            "    Optional<T> findById(ID id);\n"
            "    Iterable<T> findAll();\n"
            "    <S extends T> S save(S entity);\n"
            "    void deleteById(ID id);\n"
            "    long count();\n"
            "    boolean existsById(ID id);\n"
            "}\n"
        ),
        "org/springframework/data/repository/ListCrudRepository.java": (
            "package org.springframework.data.repository;\n"
            "import java.util.List;\n"
            "public interface ListCrudRepository<T, ID> extends CrudRepository<T, ID> {\n"
            "    List<T> findAll();\n"
            "}\n"
        ),
        "org/springframework/data/repository/PagingAndSortingRepository.java": (
            "package org.springframework.data.repository;\n"
            "import org.springframework.data.domain.Page;\n"
            "import org.springframework.data.domain.Pageable;\n"
            "public interface PagingAndSortingRepository<T, ID> extends Repository<T, ID> {\n"
            "    Page<T> findAll(Pageable pageable);\n"
            "}\n"
        ),
        "org/springframework/stereotype/Controller.java": (
            "package org.springframework.stereotype;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.TYPE)\n"
            "public @interface Controller {}\n"
        ),
        "org/springframework/stereotype/Component.java": (
            "package org.springframework.stereotype;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.TYPE)\n"
            "public @interface Component { String value() default \"\"; }\n"
        ),
        "org/springframework/stereotype/Service.java": (
            "package org.springframework.stereotype;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.TYPE)\n"
            "public @interface Service { String value() default \"\"; }\n"
        ),
        "org/springframework/stereotype/Repository.java": (
            "package org.springframework.stereotype;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.TYPE)\n"
            "public @interface Repository { String value() default \"\"; }\n"
        ),
        "org/springframework/ui/Model.java": (
            "package org.springframework.ui;\n"
            "public interface Model {\n"
            "    Model addAttribute(String name, Object value);\n"
            "    Model addAttribute(Object value);\n"
            "}\n"
        ),
        "org/springframework/ui/ModelMap.java": (
            "package org.springframework.ui;\n"
            "public class ModelMap {\n"
            "    public ModelMap addAttribute(String name, Object value) { return this; }\n"
            "}\n"
        ),
        "org/springframework/validation/Errors.java": (
            "package org.springframework.validation;\n"
            "public interface Errors {\n"
            "    boolean hasErrors();\n"
            "    void rejectValue(String field, String errorCode, String defaultMessage);\n"
            "    void rejectValue(String field, String errorCode);\n"
            "}\n"
        ),
        "org/springframework/validation/BindingResult.java": (
            "package org.springframework.validation;\n"
            "public interface BindingResult extends Errors {}\n"
        ),
        "org/springframework/validation/Validator.java": (
            "package org.springframework.validation;\n"
            "public interface Validator {\n"
            "    boolean supports(Class<?> clazz);\n"
            "    void validate(Object target, Errors errors);\n"
            "}\n"
        ),
        "org/springframework/web/bind/WebDataBinder.java": (
            "package org.springframework.web.bind;\n"
            "public class WebDataBinder {\n"
            "    public void setDisallowedFields(String... fields) {}\n"
            "    public void setAllowedFields(String... fields) {}\n"
            "    public void setValidator(Object validator) {}\n"
            "}\n"
        ),
        "org/springframework/web/bind/annotation/InitBinder.java": (
            "package org.springframework.web.bind.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface InitBinder { String[] value() default {}; }\n"
        ),
        "org/springframework/web/bind/annotation/ModelAttribute.java": (
            "package org.springframework.web.bind.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface ModelAttribute { String value() default \"\"; }\n"
        ),
        "org/springframework/web/bind/annotation/ResponseBody.java": (
            "package org.springframework.web.bind.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface ResponseBody {}\n"
        ),
        "org/springframework/web/servlet/ModelAndView.java": (
            "package org.springframework.web.servlet;\n"
            "public class ModelAndView {\n"
            "    public ModelAndView(String viewName) {}\n"
            "    public ModelAndView addObject(Object value) { return this; }\n"
            "    public ModelAndView addObject(String name, Object value) { return this; }\n"
            "}\n"
        ),
        "org/springframework/web/servlet/mvc/support/RedirectAttributes.java": (
            "package org.springframework.web.servlet.mvc.support;\n"
            "public interface RedirectAttributes {\n"
            "    RedirectAttributes addAttribute(String name, Object value);\n"
            "    RedirectAttributes addFlashAttribute(String name, Object value);\n"
            "}\n"
        ),
        "org/springframework/cache/annotation/Cacheable.java": (
            "package org.springframework.cache.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface Cacheable { String[] value() default {}; }\n"
        ),
        "org/springframework/dao/DataAccessException.java": (
            "package org.springframework.dao;\n"
            "public class DataAccessException extends RuntimeException {\n"
            "    public DataAccessException(String message) { super(message); }\n"
            "}\n"
        ),
        "org/springframework/dao/DataIntegrityViolationException.java": (
            "package org.springframework.dao;\n"
            "public class DataIntegrityViolationException extends DataAccessException {\n"
            "    public DataIntegrityViolationException(String message) { super(message); }\n"
            "}\n"
        ),
        "org/springframework/util/Assert.java": (
            "package org.springframework.util;\n"
            "public abstract class Assert {\n"
            "    public static void notNull(Object object, String message) {}\n"
            "    public static void state(boolean expression, String message) {}\n"
            "    public static void isTrue(boolean expression, String message) {}\n"
            "}\n"
        ),
        "org/springframework/util/StringUtils.java": (
            "package org.springframework.util;\n"
            "public abstract class StringUtils {\n"
            "    public static boolean hasText(String text) {\n"
            "        return text != null && !text.isBlank();\n"
            "    }\n"
            "    public static boolean hasLength(String text) {\n"
            "        return text != null && !text.isEmpty();\n"
            "    }\n"
            "}\n"
        ),
        "jakarta/persistence/UniqueConstraint.java": (
            "package jakarta.persistence;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface UniqueConstraint { String[] columnNames(); }\n"
        ),
        "com/fasterxml/jackson/annotation/JsonIgnore.java": (
            "package com.fasterxml.jackson.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface JsonIgnore {}\n"
        ),
        "org/springframework/dao/EmptyResultDataAccessException.java": (
            "package org.springframework.dao;\n"
            "public class EmptyResultDataAccessException extends DataAccessException {\n"
            "    public EmptyResultDataAccessException(String message) { super(message); }\n"
            "}\n"
        ),
        "org/springframework/orm/ObjectRetrievalFailureException.java": (
            "package org.springframework.orm;\n"
            "import org.springframework.dao.DataAccessException;\n"
            "public class ObjectRetrievalFailureException extends DataAccessException {\n"
            "    public ObjectRetrievalFailureException(String message) { super(message); }\n"
            "}\n"
        ),
        "org/springframework/context/annotation/Profile.java": (
            "package org.springframework.context.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface Profile { String[] value(); }\n"
        ),
        "org/springframework/data/repository/query/Param.java": (
            "package org.springframework.data.repository.query;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface Param { String value(); }\n"
        ),
        "org/springframework/transaction/annotation/Transactional.java": (
            "package org.springframework.transaction.annotation;\n"
            "import java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n"
            "public @interface Transactional { boolean readOnly() default false; }\n"
        ),
    }


def generate_recorder_source() -> str:
    """The instrumentation: a proxy that records every repository invocation."""
    return (
        "package harness;\n"
        "import java.lang.reflect.*;\nimport java.util.*;\n"
        "import jakarta.persistence.Column;\n"
        "import jakarta.persistence.JoinColumn;\n"
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
        "        // Entity fields live on @MappedSuperclass ancestors too (petclinic's\n"
        "        // Owner.lastName is declared on Person), so walk the hierarchy.\n"
        "        for (Class<?> level = entity; level != null && level != Object.class;\n"
        "                level = level.getSuperclass()) {\n"
        "            for (Field field : level.getDeclaredFields()) {\n"
        "                if (field.isSynthetic() || Modifier.isStatic(field.getModifiers())) continue;\n"
        "                String name = field.getName();\n"
        "                // The physical column name is the witnessable element: SCA\n"
        "                // resolves a derived `findByPetId` to the association's own\n"
        "                // @JoinColumn (`pet_id`), never to the Java field `pet`. A\n"
        "                // collection-typed field's @JoinColumn names a column on the\n"
        "                // TARGET table, so it never renames the field here.\n"
        "                Column column = field.getAnnotation(Column.class);\n"
        "                if (column != null && !column.name().isEmpty()) name = column.name();\n"
        "                JoinColumn join = field.getAnnotation(JoinColumn.class);\n"
        "                boolean toMany = Collection.class.isAssignableFrom(field.getType())\n"
        "                    || Map.class.isAssignableFrom(field.getType());\n"
        "                if (join != null && !join.name().isEmpty() && !toMany) name = join.name();\n"
        "                names.add(name);\n"
        "            }\n"
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
    qualified: Mapping[str, str] = {},
) -> str:
    """A generated harness that exercises every injected repository method.

    Every production type is loaded and constructed through reflection
    (`Class.forName` + `setAccessible`) instead of a compile-time `new <Type>(...)`:
    a real injection site is routinely package-private (Spring only ever needs
    container visibility, never public), so a direct reference from the harness's own
    `harness` package would fail to compile on exactly the classes this stage most
    needs to instrument. `qualified` maps each type's simple name to its real
    fully-qualified name, read off its own `package` declaration by
    `qualified_type_names` -- a real multi-module checkout scatters entities,
    repositories, and injection sites across many packages, never one fixed package.
    """
    entity_table = {entity.type_name: entity for entity in entities}
    by_name = repository_resolution(repositories)

    def fqcn(simple: str) -> str:
        return qualified.get(simple, simple)

    lines = [
        "package harness;",
        "import java.lang.reflect.*;",
        "",
        "/** Generated: constructs each injection site with recording proxies. */",
        "public final class GeneratedRuntimeTest {",
        "    public static void main(String[] args) throws Exception {",
    ]
    for site_index, site in enumerate(sites):
        if any(type_name not in by_name for _field, type_name in site.repository_fields):
            continue
        types = [by_name[type_name] for _field, type_name in site.repository_fields]
        if any(repository.entity_type not in entity_table for repository in types):
            continue
        lines.append("        try {")
        proxy_vars = []
        ctor_class_vars = []
        for index, (repository, (_field, declared)) in enumerate(
            zip(types, site.repository_fields)
        ):
            # The proxy implements the Spring Data repository type; the constructor
            # is looked up by the field's own declared type (petclinic-rest injects
            # the parent interface, which the Spring Data type extends).
            repo_var = f"repoClass{site_index}_{index}"
            declared_var = f"declaredClass{site_index}_{index}"
            entity_var = f"entityClass{site_index}_{index}"
            proxy_var = f"proxy{site_index}_{index}"
            lines.append(f"            Class<?> {repo_var} = Class.forName(\"{fqcn(repository.type_name)}\");")
            lines.append(f"            Class<?> {declared_var} = Class.forName(\"{fqcn(declared)}\");")
            lines.append(f"            Class<?> {entity_var} = Class.forName(\"{fqcn(repository.entity_type)}\");")
            lines.append(f"            Object {proxy_var} = Recorder.proxy({repo_var}, {entity_var});")
            proxy_vars.append(proxy_var)
            ctor_class_vars.append(declared_var)
        site_class_var = f"siteClass{site_index}"
        ctor_var = f"ctor{site_index}"
        obj_var = f"site{site_index}"
        lines.append(f"            Class<?> {site_class_var} = Class.forName(\"{fqcn(site.type_name)}\");")
        lines.append(
            f"            Constructor<?> {ctor_var} = {site_class_var}.getDeclaredConstructor("
            + ", ".join(ctor_class_vars) + ");"
        )
        lines.append(f"            {ctor_var}.setAccessible(true);")
        lines.append(
            f"            Object {obj_var} = {ctor_var}.newInstance(" + ", ".join(proxy_vars) + ");"
        )
        for method in site.methods:
            # A generated call must never fail the harness; a throwing method is still
            # evidence that the call reached the repository.
            lines.append(f"            invokeQuiet({obj_var}, \"{method}\");")
        lines.append("        } catch (Throwable ignored) { }")
    lines += [
        "        for (String observation : Recorder.OBSERVED) {",
        "            System.out.println(\"OBSERVED\\t\" + observation);",
        "        }",
        "    }",
        "",
        "    private static void invokeQuiet(Object target, String name) {",
        "        try {",
        "            invoke(target, name);",
        "        } catch (Throwable ignored) { }",
        "    }",
        "",
        "    private static void invoke(Object target, String name) throws Exception {",
        "        for (Method method : target.getClass().getDeclaredMethods()) {",
        "            if (!method.getName().equals(name) || method.getParameterCount() > 1) continue;",
        "            method.setAccessible(true);",
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
        entities, repositories, sites, qualified_type_names(sources)
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
