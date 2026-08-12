"""Element-level blast radius over the composed lineage graph.

Seed a changed element and follow every proven derivation out of it. Severity is a
function of distance: the seed is the SOURCE, anything derived directly from it will
BREAK, and anything further downstream is a WARN because a transform in between may
absorb the change.

The traversal only follows edges that exist. It never infers a path, so an empty
radius means the graph has no evidence of downstream use — not that none exists.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from lineage_api.services.composition import ComposedGraph


@dataclass(frozen=True, slots=True)
class ImpactedElement:
    urn: str
    dataset: str
    severity: str
    hops: int
    via: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImpactReport:
    seed: str
    impacted: tuple[ImpactedElement, ...]
    datasets: tuple[str, ...]
    max_hops: int


def _dataset_of(urn: str) -> str:
    return urn.rsplit("#", 1)[0]


def _severity(hops: int) -> str:
    if hops == 0:
        return "SOURCE"
    return "BREAK" if hops == 1 else "WARN"


def simulate_impact(graph: ComposedGraph, seed_element: str) -> ImpactReport:
    downstream: dict[str, list[tuple[str, str]]] = {}
    for edge in graph.edges:
        transform = str(edge.get("transform", ""))
        target = str(edge["to"])
        for source in edge.get("from", ()):
            downstream.setdefault(str(source), []).append((target, transform))

    seen: dict[str, ImpactedElement] = {
        seed_element: ImpactedElement(
            urn=seed_element,
            dataset=_dataset_of(seed_element),
            severity=_severity(0),
            hops=0,
            via=(),
        )
    }
    queue: deque[str] = deque([seed_element])

    while queue:
        current = queue.popleft()
        current_record = seen[current]
        for target, transform in sorted(downstream.get(current, ())):
            if target in seen:
                continue
            hops = current_record.hops + 1
            seen[target] = ImpactedElement(
                urn=target,
                dataset=_dataset_of(target),
                severity=_severity(hops),
                hops=hops,
                via=current_record.via + (transform,),
            )
            queue.append(target)

    impacted = tuple(sorted(seen.values(), key=lambda item: (item.hops, item.urn)))
    return ImpactReport(
        seed=seed_element,
        impacted=impacted,
        datasets=tuple(sorted({item.dataset for item in impacted})),
        max_hops=max(item.hops for item in impacted),
    )
