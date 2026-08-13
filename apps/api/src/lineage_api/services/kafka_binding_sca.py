"""Read Kafka topic bindings from Spring Cloud Stream configuration.

A stream processor does not name its topics in code. The Java says
`Function<KStream<…>, KStream<…>> process()`; the topics live in configuration under the
documented convention `spring.cloud.stream.bindings.<function>-in-<n>` and
`-out-<n>`. That convention is a literal contract, which is what makes a topic edge
provable rather than guessed.

The reader is deliberately bounded to that one key subset — it is not a YAML parser, and
adding one would pull in a dependency to read four keys. The precedent is the
hand-written Gradle tokenizer in the Java cell, which reads build files without running
Gradle. Anything outside the subset is ignored; anything inside it that is not literal is
typed residue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

import tree_sitter_java
from tree_sitter import Language, Node, Parser

_BINDINGS_PREFIX = "spring.cloud.stream.bindings."
_FUNCTIONAL = re.compile(r"^(?P<function>[A-Za-z_][A-Za-z0-9_]*)-(?P<direction>in|out)-\d+$")
_LEGACY = {"input": "READS", "output": "WRITES"}
_PLACEHOLDER = re.compile(r"\$\{.*\}")
# Keys under a binding that are configuration rather than identity.
_IGNORED_BINDING_KEYS = frozenset(
    {"group", "content-type", "contentType", "consumer", "producer", "binder"}
)


@dataclass(frozen=True, slots=True)
class StreamBinding:
    binding: str
    function: str
    direction: str
    topic: str
    path: str
    line: int


@dataclass(frozen=True, slots=True)
class BindingResidue:
    code: str
    path: str
    line: int
    symbol: str


@dataclass(frozen=True, slots=True)
class BindingAnalysis:
    bindings: tuple[StreamBinding, ...]
    residue: tuple[BindingResidue, ...]


def _flatten(text: str) -> list[tuple[str, str, int]]:
    """Flatten a configuration document into (dotted key, value, 1-based line).

    Handles the three shapes that actually ship: `.properties` `key=value`, YAML with
    flat dotted keys, and fully nested YAML. Keys that are themselves dotted flatten by
    joining, so all three converge on one representation.
    """
    flattened: list[tuple[str, str, int]] = []
    stack: list[tuple[int, str]] = []

    for index, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip() or line.lstrip().startswith("-"):
            continue

        if "=" in line and ":" not in line.split("=", 1)[0]:
            key, _, value = line.partition("=")
            flattened.append((key.strip(), value.strip(), index))
            continue

        if ":" not in line:
            continue

        indent = len(line) - len(line.lstrip())
        key, _, value = line.lstrip().partition(":")
        key = key.strip()
        value = value.strip()

        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([segment for _, segment in stack] + [key])

        if value:
            flattened.append((path, value.strip('"').strip("'"), index))
        else:
            stack.append((indent, key))

    return flattened


def _direction_of(binding: str) -> str | None:
    match = _FUNCTIONAL.fullmatch(binding)
    if match is not None:
        return "READS" if match.group("direction") == "in" else "WRITES"
    return _LEGACY.get(binding)


def _function_of(binding: str) -> str:
    match = _FUNCTIONAL.fullmatch(binding)
    return match.group("function") if match is not None else binding


def read_stream_bindings(sources: dict[str, str]) -> BindingAnalysis:
    bindings: list[StreamBinding] = []
    residue: list[BindingResidue] = []

    for path in sorted(sources):
        for key, value, line in _flatten(sources[path]):
            if not key.startswith(_BINDINGS_PREFIX):
                continue
            remainder = key[len(_BINDINGS_PREFIX) :]
            segments = remainder.split(".")
            if len(segments) == 1:
                binding = segments[0]
            elif len(segments) == 2 and segments[1] == "destination":
                binding = segments[0]
            else:
                # group, content-type, consumer.* and friends carry no identity.
                if len(segments) >= 2 and segments[1] in _IGNORED_BINDING_KEYS:
                    continue
                continue

            direction = _direction_of(binding)
            if direction is None:
                residue.append(
                    BindingResidue("unrecognised-binding-name", path, line, binding)
                )
                continue
            if _PLACEHOLDER.search(value):
                # Resolved at deploy time; the topic is not knowable from source.
                residue.append(
                    BindingResidue("dynamic-destination", path, line, binding)
                )
                continue

            bindings.append(
                StreamBinding(
                    binding=binding,
                    function=_function_of(binding),
                    direction=direction,
                    topic=value,
                    path=path,
                    line=line,
                )
            )

    return BindingAnalysis(
        bindings=tuple(
            sorted(bindings, key=lambda item: (item.path, item.line, item.binding))
        ),
        residue=tuple(
            sorted(residue, key=lambda item: (item.path, item.line, item.code))
        ),
    )


def is_stream_configuration(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.startswith("application") and name.endswith(
        (".yml", ".yaml", ".properties")
    )


# --------------------------------------------------------------------------------------
# Raw Kafka Streams topology API
#
# Not every processor uses Spring Cloud Stream. confluentinc/kafka-streams-examples names
# its topics directly in the builder API — `builder.stream("t")` and `.to("t")` — so a
# config-only reader finds nothing there. Both forms answer the same question, so both
# produce the same StreamBinding.
# --------------------------------------------------------------------------------------

_PARSER = Parser(Language(tree_sitter_java.language()))
_TOPOLOGY_SOURCES = {"stream": "READS", "table": "READS", "globalTable": "READS"}
_TOPOLOGY_SINKS = {"to": "WRITES"}
# `.to(...)` and `.stream(...)` are common method names. Rather than guess from the
# receiver expression — which breaks the moment a chain is split across variables — the
# file must import the Kafka Streams API at all. A file that does not is not a topology,
# so no topic can be invented from it.
_STREAMS_IMPORT = "org.apache.kafka.streams"


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _node_text(node: Node, content: bytes) -> str:
    return content[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _call_name(node: Node, content: bytes) -> str:
    field = node.child_by_field_name("name")
    return _node_text(field, content) if field is not None else ""


def _enclosing_method(node: Node, content: bytes) -> str:
    current = node.parent
    while current is not None:
        if current.type == "method_declaration":
            name = current.child_by_field_name("name")
            return _node_text(name, content) if name is not None else ""
        current = current.parent
    return ""


def read_streams_topology(sources: dict[str, str]) -> BindingAnalysis:
    bindings: list[StreamBinding] = []
    residue: list[BindingResidue] = []

    for path in sorted(sources):
        text = sources[path]
        if _STREAMS_IMPORT not in text:
            continue
        content = text.encode()
        root = _PARSER.parse(content).root_node
        for node in _walk(root):
            if node.type != "method_invocation":
                continue
            called = _call_name(node, content)
            direction = _TOPOLOGY_SOURCES.get(called) or _TOPOLOGY_SINKS.get(called)
            if direction is None:
                continue

            arguments = node.child_by_field_name("arguments")
            first = (
                next((c for c in arguments.children if c.type not in {"(", ")", ","}), None)
                if arguments is not None
                else None
            )
            line = node.start_point[0] + 1
            if first is None:
                continue
            if first.type != "string_literal":
                residue.append(
                    BindingResidue("dynamic-destination", path, line, called)
                )
                continue

            bindings.append(
                StreamBinding(
                    binding=called,
                    function=_enclosing_method(node, content),
                    direction=direction,
                    topic=_node_text(first, content).strip('"'),
                    path=path,
                    line=line,
                )
            )

    return BindingAnalysis(
        bindings=tuple(sorted(bindings, key=lambda i: (i.path, i.line, i.binding))),
        residue=tuple(sorted(residue, key=lambda i: (i.path, i.line, i.code))),
    )
