"""Static lineage for Java Spark batch jobs.

Spark is where an estate's object-store lineage actually happens: a job reads a landing
prefix, reshapes columns, and writes a curated prefix. This cell makes the *static*
claim. That claim is not optional — runtime evidence never invents an edge, so without
it the OpenLineage events a real Spark run emits would be discarded as runtime-only and
nothing would ever reach the corroborated band.

Only literal paths are read. A path assembled from a variable is residue: the dataset it
names is not knowable without running the job, and guessing it would invent an edge.
"""

from __future__ import annotations

from dataclasses import dataclass

import tree_sitter_java
from tree_sitter import Language, Node, Parser

_PARSER = Parser(Language(tree_sitter_java.language()))

_READ_FORMATS = {"parquet", "json", "csv", "orc", "load", "text"}
_WRITE_FORMATS = {"parquet", "json", "csv", "orc", "save", "text"}


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    target_column: str
    source_columns: tuple[str, ...]
    transform: str


@dataclass(frozen=True, slots=True)
class SparkJob:
    source: str
    target: str
    method: str
    mappings: tuple[ColumnMapping, ...]
    path: str
    line: int


@dataclass(frozen=True, slots=True)
class SparkResidue:
    code: str
    path: str
    line: int
    symbol: str


@dataclass(frozen=True, slots=True)
class SparkAnalysis:
    jobs: tuple[SparkJob, ...]
    residue: tuple[SparkResidue, ...]


def _text(node: Node, content: bytes) -> str:
    return content[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _name(node: Node, content: bytes) -> str:
    field = node.child_by_field_name("name")
    return _text(field, content) if field is not None else ""


def _receiver_chain(node: Node, content: bytes) -> list[str]:
    """Method names on the receiver chain, nearest first."""
    names: list[str] = []
    current = node.child_by_field_name("object")
    while current is not None and current.type == "method_invocation":
        names.append(_name(current, content))
        current = current.child_by_field_name("object")
    return names


def _arguments(node: Node) -> list[Node]:
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return []
    return [c for c in arguments.children if c.type not in {"(", ")", ","}]


def _string_literal(node: Node, content: bytes) -> str | None:
    if node.type != "string_literal":
        return None
    return _text(node, content).strip('"')


def _referenced_columns(node: Node, content: bytes) -> tuple[str, ...]:
    """Column names named by `col("x")` / `column("x")` inside an expression."""
    found: list[str] = []
    for candidate in _walk(node):
        if candidate.type != "method_invocation":
            continue
        if _name(candidate, content) not in {"col", "column"}:
            continue
        arguments = _arguments(candidate)
        if arguments:
            literal = _string_literal(arguments[0], content)
            if literal is not None and literal not in found:
                found.append(literal)
    return tuple(found)


def _transform_text(node: Node, content: bytes) -> str:
    """A compact, dialect-free rendering of the expression."""
    rendered = _text(node, content)
    for noise in ("functions.", "org.apache.spark.sql."):
        rendered = rendered.replace(noise, "")
    return " ".join(rendered.split()).replace('"', "")


def _enclosing_method(node: Node, content: bytes) -> str:
    current = node.parent
    method = ""
    while current is not None:
        if current.type == "method_declaration" and not method:
            method = _name(current, content)
        if current.type == "class_declaration":
            owner = next(
                (
                    _text(child, content)
                    for child in current.children
                    if child.type == "identifier"
                ),
                "",
            )
            return f"{owner}#{method}" if method else owner
        current = current.parent
    return method


def analyze_spark_sources(sources: dict[str, str]) -> SparkAnalysis:
    jobs: list[SparkJob] = []
    residue: list[SparkResidue] = []

    for path in sorted(sources):
        content = sources[path].encode()
        root = _PARSER.parse(content).root_node

        reads: dict[str, str] = {}
        writes: list[tuple[str, str, int, Node]] = []

        for node in _walk(root):
            if node.type != "method_invocation":
                continue
            called = _name(node, content)
            chain = _receiver_chain(node, content)
            arguments = _arguments(node)
            if not arguments:
                continue
            literal = _string_literal(arguments[0], content)
            method = _enclosing_method(node, content)
            line = node.start_point[0] + 1

            # Structured streaming names its endpoints in options rather than in a
            # positional path argument: `.option("subscribe", topic)` for a Kafka source
            # and `.option("path", ...)` for a file sink.
            if called == "option" and len(arguments) == 2:
                key = _string_literal(arguments[0], content)
                value = _string_literal(arguments[1], content)
                if key in {"subscribe", "path"} and value is None:
                    residue.append(SparkResidue("dynamic-path", path, line, key or ""))
                    continue
                if key == "subscribe":
                    reads.setdefault(method, value)
                elif key == "path" and any(
                    item in {"write", "writeStream"} for item in chain
                ):
                    writes.append((method, value, line, node))
                continue

            if called in _READ_FORMATS and any(
                item in {"read", "readStream"} for item in chain
            ):
                if literal is None:
                    residue.append(
                        SparkResidue("dynamic-path", path, line, called)
                    )
                    continue
                reads[method] = literal
            elif called in _WRITE_FORMATS and any(
                item in {"write", "writeStream"} for item in chain
            ):
                if literal is None:
                    residue.append(
                        SparkResidue("dynamic-path", path, line, called)
                    )
                    continue
                writes.append((method, literal, line, node))

        for method, target, line, node in writes:
            source = reads.get(method)
            if source is None:
                residue.append(
                    SparkResidue("unresolved-spark-source", path, line, target)
                )
                continue
            jobs.append(
                SparkJob(
                    source=source,
                    target=target,
                    method=method,
                    mappings=_column_mappings(root, content, method),
                    path=path,
                    line=line,
                )
            )

    return SparkAnalysis(
        jobs=tuple(sorted(jobs, key=lambda item: (item.path, item.target))),
        residue=tuple(sorted(residue, key=lambda item: (item.path, item.line, item.code))),
    )


def _column_mappings(
    root: Node, content: bytes, method: str
) -> tuple[ColumnMapping, ...]:
    """`withColumn("target", expr)` pairs inside one method."""
    mappings: list[ColumnMapping] = []
    for node in _walk(root):
        if node.type != "method_invocation" or _name(node, content) != "withColumn":
            continue
        if _enclosing_method(node, content) != method:
            continue
        arguments = _arguments(node)
        if len(arguments) != 2:
            continue
        target = _string_literal(arguments[0], content)
        if target is None:
            continue
        sources = _referenced_columns(arguments[1], content)
        if not sources:
            continue
        mappings.append(
            ColumnMapping(target, sources, _transform_text(arguments[1], content))
        )
    return tuple(mappings)
