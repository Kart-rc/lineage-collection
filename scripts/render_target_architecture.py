"""Render the canonical target AWS architecture to an offline browser-ready page.

The Mermaid block in ``docs/architecture/lineage-platform-target.md`` is normative. This
renderer parses that block and emits a self-contained HTML file so the two artifacts can
never drift: ``--check`` fails when the committed HTML is stale.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "architecture" / "lineage-platform-target.md"
TARGET = ROOT / "docs" / "architecture" / "lineage-platform-target.html"

_MERMAID_BLOCK = re.compile(r"```mermaid\n(?P<body>.*?)\n```", re.DOTALL)
_CLASS_DEF = re.compile(r"^classDef\s+(?P<name>[A-Za-z][A-Za-z0-9_]*)\s+(?P<style>.+)$")
_SUBGRAPH = re.compile(r"^subgraph\s+(?P<id>[A-Za-z][A-Za-z0-9_]*)\[\"(?P<title>[^\"]+)\"\]$")
_NODE = re.compile(
    r"^(?P<id>[A-Za-z][A-Za-z0-9_]*)"
    r"(?:\[\"(?P<box>[^\"]+)\"\]|\{\{\"(?P<gate>[^\"]+)\"\}\})"
    r":::(?P<status>[A-Za-z][A-Za-z0-9_]*)$"
)
_EDGE = re.compile(
    r"^(?P<source>[A-Za-z][A-Za-z0-9_]*)\s+"
    r"(?P<arrow>-->|-\.->)\|\"(?P<label>[^\"]+)\"\|\s+"
    r"(?P<target>[A-Za-z][A-Za-z0-9_]*)$"
)


class ArchitectureSourceError(ValueError):
    """The normative Mermaid source is not parseable or not internally consistent."""


@dataclass(frozen=True, slots=True)
class StatusClass:
    name: str
    fill: str
    stroke: str
    color: str


@dataclass(frozen=True, slots=True)
class Node:
    identifier: str
    title: str
    detail: str
    status: str
    shape: str
    layer: str


@dataclass(frozen=True, slots=True)
class Edge:
    source: str
    target: str
    label: str
    durable: bool


@dataclass(frozen=True, slots=True)
class Layer:
    identifier: str
    title: str
    nodes: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class Architecture:
    statuses: tuple[StatusClass, ...]
    layers: tuple[Layer, ...]
    edges: tuple[Edge, ...]

    @property
    def nodes(self) -> tuple[Node, ...]:
        return tuple(node for layer in self.layers for node in layer.nodes)


def _style_value(style: str, key: str) -> str:
    for part in style.split(","):
        name, _, value = part.partition(":")
        if name.strip() == key:
            return value.strip()
    raise ArchitectureSourceError(f"classDef style is missing {key}")


def _split_label(label: str) -> tuple[str, str]:
    title, separator, detail = label.partition("<br/>")
    if separator and not detail.strip():
        raise ArchitectureSourceError(f"node label has an empty detail line: {label}")
    return title.strip(), detail.strip()


def parse_architecture(markdown: str) -> Architecture:
    match = _MERMAID_BLOCK.search(markdown)
    if match is None:
        raise ArchitectureSourceError("no normative Mermaid block was found")
    lines = [line.strip() for line in match.group("body").splitlines()]
    if not lines or lines[0] != "flowchart LR":
        raise ArchitectureSourceError("the Mermaid block must declare 'flowchart LR'")

    statuses: list[StatusClass] = []
    layers: list[Layer] = []
    edges: list[Edge] = []
    current_layer: tuple[str, str] | None = None
    current_nodes: list[Node] = []

    for line in lines[1:]:
        if not line:
            continue
        if class_def := _CLASS_DEF.match(line):
            if current_layer is not None:
                raise ArchitectureSourceError("classDef must be declared outside a subgraph")
            style = class_def.group("style")
            statuses.append(
                StatusClass(
                    name=class_def.group("name"),
                    fill=_style_value(style, "fill"),
                    stroke=_style_value(style, "stroke"),
                    color=_style_value(style, "color"),
                )
            )
            continue
        if subgraph := _SUBGRAPH.match(line):
            if current_layer is not None:
                raise ArchitectureSourceError("nested subgraphs are not supported")
            current_layer = (subgraph.group("id"), subgraph.group("title"))
            current_nodes = []
            continue
        if line == "end":
            if current_layer is None:
                raise ArchitectureSourceError("unbalanced 'end' without a subgraph")
            if not current_nodes:
                raise ArchitectureSourceError(f"layer {current_layer[0]} declares no nodes")
            layers.append(
                Layer(
                    identifier=current_layer[0],
                    title=current_layer[1],
                    nodes=tuple(current_nodes),
                )
            )
            current_layer = None
            continue
        if node := _NODE.match(line):
            if current_layer is None:
                raise ArchitectureSourceError(f"node {node.group('id')} is outside a layer")
            label = node.group("box") or node.group("gate")
            title, detail = _split_label(label)
            current_nodes.append(
                Node(
                    identifier=node.group("id"),
                    title=title,
                    detail=detail,
                    status=node.group("status"),
                    shape="gate" if node.group("gate") else "box",
                    layer=current_layer[0],
                )
            )
            continue
        if edge := _EDGE.match(line):
            if current_layer is not None:
                raise ArchitectureSourceError("edges must be declared outside a subgraph")
            edges.append(
                Edge(
                    source=edge.group("source"),
                    target=edge.group("target"),
                    label=edge.group("label"),
                    durable=edge.group("arrow") == "-.->",
                )
            )
            continue
        raise ArchitectureSourceError(f"unrecognized Mermaid statement: {line}")

    if current_layer is not None:
        raise ArchitectureSourceError("unbalanced subgraph without a matching 'end'")

    architecture = Architecture(tuple(statuses), tuple(layers), tuple(edges))
    _validate(architecture)
    return architecture


def _validate(architecture: Architecture) -> None:
    if not architecture.statuses:
        raise ArchitectureSourceError("the status legend declares no classes")
    if not architecture.layers:
        raise ArchitectureSourceError("the diagram declares no layers")
    if not architecture.edges:
        raise ArchitectureSourceError("the diagram declares no edges")

    known_statuses = {status.name for status in architecture.statuses}
    identifiers: set[str] = set()
    for node in architecture.nodes:
        if node.identifier in identifiers:
            raise ArchitectureSourceError(f"duplicate node identifier: {node.identifier}")
        identifiers.add(node.identifier)
        if node.status not in known_statuses:
            raise ArchitectureSourceError(f"node {node.identifier} uses an undeclared status")
        if not node.detail:
            raise ArchitectureSourceError(f"node {node.identifier} has no explanatory detail")

    used_statuses = {node.status for node in architecture.nodes}
    if used_statuses != known_statuses:
        unused = ", ".join(sorted(known_statuses - used_statuses))
        raise ArchitectureSourceError(f"declared but unused status classes: {unused}")

    seen: set[tuple[str, str, str]] = set()
    connected: set[str] = set()
    for edge in architecture.edges:
        for endpoint in (edge.source, edge.target):
            if endpoint not in identifiers:
                raise ArchitectureSourceError(f"edge references undeclared node: {endpoint}")
        if edge.source == edge.target:
            raise ArchitectureSourceError(f"self-referencing edge on {edge.source}")
        key = (edge.source, edge.target, edge.label)
        if key in seen:
            raise ArchitectureSourceError(f"duplicate edge: {edge.source} -> {edge.target}")
        seen.add(key)
        connected.update((edge.source, edge.target))

    isolated = sorted(identifiers - connected)
    if isolated:
        raise ArchitectureSourceError(f"nodes with no arrows: {', '.join(isolated)}")


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


def _node_markup(node: Node) -> str:
    shape = " architecture-node--gate" if node.shape == "gate" else ""
    return (
        f'        <li class="architecture-node architecture-node--{node.status}{shape}"'
        f' data-node="{_escape(node.identifier)}" data-status="{_escape(node.status)}">\n'
        f'          <span class="architecture-node__title">{_escape(node.title)}</span>\n'
        f'          <span class="architecture-node__detail">{_escape(node.detail)}</span>\n'
        f"        </li>"
    )


def _edge_markup(edge: Edge, titles: dict[str, str]) -> str:
    kind = "durable" if edge.durable else "synchronous"
    symbol = "&#8674;" if edge.durable else "&#8594;"
    return (
        f'        <tr class="architecture-edge architecture-edge--{kind}"'
        f' data-edge="{_escape(f"{edge.source}->{edge.target}")}" data-kind="{kind}">\n'
        f'          <td class="architecture-edge__endpoint">{_escape(titles[edge.source])}</td>\n'
        f'          <td class="architecture-edge__arrow">{symbol}</td>\n'
        f'          <td class="architecture-edge__endpoint">{_escape(titles[edge.target])}</td>\n'
        f'          <td class="architecture-edge__label">{_escape(edge.label)}</td>\n'
        f'          <td class="architecture-edge__kind">{kind}</td>\n'
        f"        </tr>"
    )


_STATUS_MEANING = {
    "verified": "Implemented and verified by fresh executable evidence.",
    "synthesized": "Implemented as production-shaped infrastructure; synthesis evidence only.",
    "partial": "Partially wired; required behavior remains.",
    "planned": "Planned or deferred; not configured.",
}


def render(architecture: Architecture) -> str:
    status_rules = "\n".join(
        f"    .architecture-node--{status.name} {{\n"
        f"      background: {status.fill};\n"
        f"      border-color: {status.stroke};\n"
        f"      color: {status.color};\n"
        f"    }}"
        for status in architecture.statuses
    )
    legend = "\n".join(
        f'      <li class="architecture-legend__item architecture-node--{status.name}"'
        f' data-legend="{_escape(status.name)}">\n'
        f'        <span class="architecture-legend__name">{_escape(status.name)}</span>\n'
        f"        <span class=\"architecture-legend__meaning\">"
        f"{_escape(_STATUS_MEANING.get(status.name, 'Undocumented status class.'))}</span>\n"
        f"      </li>"
        for status in architecture.statuses
    )
    layers = "\n".join(
        f'    <section class="architecture-layer" data-layer="{_escape(layer.identifier)}">\n'
        f"      <h3>{_escape(layer.title)}</h3>\n"
        f'      <ul class="architecture-layer__nodes">\n'
        + "\n".join(_node_markup(node) for node in layer.nodes)
        + "\n      </ul>\n"
        "    </section>"
        for layer in architecture.layers
    )
    titles = {node.identifier: node.title for node in architecture.nodes}
    rows = "\n".join(_edge_markup(edge, titles) for edge in architecture.edges)
    counts = {
        status.name: sum(1 for node in architecture.nodes if node.status == status.name)
        for status in architecture.statuses
    }
    tally = " &middot; ".join(f"{name}: {count}" for name, count in counts.items())

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lineage Platform Target AWS Architecture</title>
<style>
    :root {{
      color-scheme: light;
      --page: #ffffff;
      --ink: #14161a;
      --muted: #55606e;
      --rule: #d5dae2;
      --surface: #f7f8fa;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 2rem 1.25rem 4rem;
      background: var(--page);
      color: var(--ink);
      font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }}
    main {{ max-width: 76rem; margin: 0 auto; }}
    h1 {{ font-size: 1.75rem; margin: 0 0 0.25rem; }}
    h2 {{ font-size: 1.25rem; margin: 2.5rem 0 0.75rem; }}
    h3 {{ font-size: 1rem; margin: 0 0 0.75rem; letter-spacing: 0.02em; text-transform: uppercase; color: var(--muted); }}
    p {{ margin: 0 0 0.75rem; }}
    .architecture-subtitle {{ color: var(--muted); margin-bottom: 1.5rem; }}
    .architecture-boundary {{
      border: 2px solid #a86a00;
      background: #fdf3e2;
      color: #3d2600;
      border-radius: 0.5rem;
      padding: 0.85rem 1rem;
      margin: 0 0 1.5rem;
    }}
    .architecture-legend {{
      list-style: none;
      display: grid;
      gap: 0.75rem;
      grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
      margin: 0 0 1rem;
      padding: 0;
    }}
    .architecture-legend__item {{ border: 2px solid; border-radius: 0.5rem; padding: 0.6rem 0.75rem; }}
    .architecture-legend__name {{ display: block; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; font-size: 0.78rem; }}
    .architecture-legend__meaning {{ display: block; font-size: 0.85rem; }}
    .architecture-tally {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 2rem; }}
    .architecture-layer {{
      border: 1px solid var(--rule);
      border-radius: 0.75rem;
      background: var(--surface);
      padding: 1rem;
      margin: 0 0 1rem;
    }}
    .architecture-layer__nodes {{
      list-style: none;
      display: grid;
      gap: 0.75rem;
      grid-template-columns: repeat(auto-fit, minmax(17rem, 1fr));
      margin: 0;
      padding: 0;
    }}
    .architecture-node {{ border: 2px solid; border-radius: 0.5rem; padding: 0.65rem 0.75rem; }}
    .architecture-node--gate {{ border-style: double; border-width: 4px; }}
    .architecture-node__title {{ display: block; font-weight: 650; }}
    .architecture-node__detail {{ display: block; font-size: 0.85rem; opacity: 0.85; }}
{status_rules}
    .architecture-edges {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
    .architecture-edges caption {{ text-align: left; color: var(--muted); padding-bottom: 0.5rem; }}
    .architecture-edges th, .architecture-edges td {{
      border-bottom: 1px solid var(--rule);
      padding: 0.4rem 0.5rem;
      text-align: left;
      vertical-align: top;
    }}
    .architecture-edge--durable {{ background: #f7f9fd; }}
    .architecture-edge__arrow {{ font-size: 1.1rem; color: var(--muted); }}
    .architecture-edge__kind {{ color: var(--muted); white-space: nowrap; }}
    .architecture-scroll {{ overflow-x: auto; }}
</style>
</head>
<body>
  <main>
    <h1>Lineage Platform Target AWS Architecture</h1>
    <p class="architecture-subtitle">
      Production AWS target only. No SQLite, local filesystem, Vite, or in-process worker appears as
      an architecture component. Generated from the normative Mermaid source in
      <code>docs/architecture/lineage-platform-target.md</code>.
    </p>
    <p class="architecture-boundary">
      <strong>Evidence boundary:</strong> no component below carries live AWS evidence. Every AWS
      runtime path remains <code>AWS_REQUIRED</code> until the approved ephemeral deploy, smoke, and
      cleanup workflow runs. <em>Verified</em> means proven by executable evidence, not observed in AWS.
    </p>

    <h2>Status legend</h2>
    <ul class="architecture-legend">
{legend}
    </ul>
    <p class="architecture-tally">Component tally &mdash; {tally}</p>

    <h2>Layers and components</h2>
{layers}

    <h2>Arrows</h2>
    <p>
      <strong>&#8594; synchronous</strong> request/response or in-process invocation within one
      deployable unit. <strong>&#8674; durable asynchronous</strong> handoff &mdash; a queued message,
      stream record, durable command, event, or immutable object; the producer does not block.
    </p>
    <div class="architecture-scroll">
      <table class="architecture-edges">
        <caption>Every arrow carries an explicit artifact or event meaning.</caption>
        <thead>
          <tr><th>From</th><th></th><th>To</th><th>Artifact or event</th><th>Kind</th></tr>
        </thead>
        <tbody>
{rows}
        </tbody>
      </table>
    </div>
  </main>
</body>
</html>
"""


def build(source: Path = SOURCE) -> str:
    return render(parse_architecture(source.read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when the committed HTML rendering is stale",
    )
    arguments = parser.parse_args(argv)
    rendered = build()
    if arguments.check:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != rendered:
            print(
                "docs/architecture/lineage-platform-target.html is stale; "
                "run 'python scripts/render_target_architecture.py'",
                file=sys.stderr,
            )
            return 1
        return 0
    TARGET.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
