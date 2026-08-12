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
