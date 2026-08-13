# Element-Level Impact Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Given a changed column, compute the blast radius across the composed lineage graph with per-dataset severity — the prototype's "Change a service's output schema, the blast radius updates live."

**Architecture:** Pure graph traversal over the column-level `DERIVES` edges the SQL cell produces and `compose_repository_documents` joins. Seed the changed element, follow every edge whose source is tainted, and classify each reached dataset. No new collection machinery — this reads what already exists.

**Tech Stack:** Python 3.12, pytest, existing `lineage_api.services.composition`.

## Global Constraints

- **Traversal is over proven edges only.** The simulation never invents a path; if the graph has no edge, there is no impact to report.
- **Cycles terminate.** A dataset already visited is not re-expanded. Real estates contain cycles (a table feeding a view feeding back into a rollup).
- **Severity vocabulary is closed:** `SOURCE` (the changed element itself), `BREAK` (a directly derived element), `WARN` (reached transitively, two or more hops). This mirrors the prototype's `origin` / `break` / `warn`.
- **Deterministic output.** Impacted elements are returned in sorted order; the same graph and seed always produce the same result.
- Test command prefix: `uv run --project apps/api python -m pytest`

---

## File Structure

| File | Responsibility |
|---|---|
| `apps/api/src/lineage_api/services/impact_simulation.py` (create) | Taint traversal and severity classification. |
| `apps/api/tests/services/test_impact_simulation.py` (create) | Traversal, severity, cycles, determinism, and an end-to-end run over the two real fixture repositories. |

---

### Task 1: Taint traversal with severity

**Files:**
- Create: `apps/api/src/lineage_api/services/impact_simulation.py`
- Test: `apps/api/tests/services/test_impact_simulation.py`

**Interfaces:**
- Consumes: `ComposedGraph` from `lineage_api.services.composition`.
- Produces:
  - `@dataclass(frozen=True, slots=True) ImpactedElement(urn: str, dataset: str, severity: str, hops: int, via: tuple[str, ...])`
  - `@dataclass(frozen=True, slots=True) ImpactReport(seed: str, impacted: tuple[ImpactedElement, ...], datasets: tuple[str, ...], max_hops: int)`
  - `simulate_impact(graph: ComposedGraph, seed_element: str) -> ImpactReport`

`via` is the tuple of transform expressions on the path that reached the element — the prototype shows the transform on each hop, and without it a blast radius is unexplainable.

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/services/test_impact_simulation.py
from pathlib import Path

from lineage_api.services.analyzer_registry import AnalyzerRegistry, AnalyzerSelection
from lineage_api.services.composition import compose_repository_documents
from lineage_api.services.impact_simulation import (
    ImpactReport,
    simulate_impact,
)
from lineage_api.services.resolver import Resolver

A = "urn:ldp:staging:snowflake:payments:raw.transactions"
B = "urn:ldp:staging:snowflake:payments:analytics.daily_revenue"
C = "urn:ldp:staging:snowflake:payments:risk.customer_features"


def _graph(edges: list[tuple[str, str, str]]):
    return compose_repository_documents(
        (
            {
                "repo": "r",
                "edges": [
                    {
                        "from": [source],
                        "to": target,
                        "edgeType": "DERIVES",
                        "transform": transform,
                        "provenanceId": f"p{index}",
                    }
                    for index, (source, target, transform) in enumerate(edges)
                ],
            },
        )
    )


def test_a_directly_derived_element_is_a_break() -> None:
    graph = _graph([(f"{A}#amount", f"{B}#gross_revenue", "SUM(amount)")])

    report = simulate_impact(graph, f"{A}#amount")

    assert isinstance(report, ImpactReport)
    assert report.seed == f"{A}#amount"
    assert [(item.urn, item.severity, item.hops) for item in report.impacted] == [
        (f"{A}#amount", "SOURCE", 0),
        (f"{B}#gross_revenue", "BREAK", 1),
    ]


def test_a_transitively_reached_element_is_a_warn() -> None:
    graph = _graph(
        [
            (f"{A}#amount", f"{B}#gross_revenue", "SUM(amount)"),
            (f"{B}#gross_revenue", f"{C}#lifetime_value", "SUM(gross_revenue)"),
        ]
    )

    report = simulate_impact(graph, f"{A}#amount")

    severities = {item.urn: item.severity for item in report.impacted}
    assert severities[f"{B}#gross_revenue"] == "BREAK"
    assert severities[f"{C}#lifetime_value"] == "WARN"
    assert report.max_hops == 2


def test_the_path_transforms_explain_the_blast_radius() -> None:
    graph = _graph(
        [
            (f"{A}#amount", f"{B}#gross_revenue", "SUM(amount)"),
            (f"{B}#gross_revenue", f"{C}#lifetime_value", "SUM(gross_revenue)"),
        ]
    )

    report = simulate_impact(graph, f"{A}#amount")
    reached = next(i for i in report.impacted if i.urn == f"{C}#lifetime_value")

    assert reached.via == ("SUM(amount)", "SUM(gross_revenue)")


def test_an_element_with_no_downstream_impacts_only_itself() -> None:
    graph = _graph([(f"{A}#amount", f"{B}#gross_revenue", "SUM(amount)")])

    report = simulate_impact(graph, f"{A}#occurred_at")

    assert [item.urn for item in report.impacted] == [f"{A}#occurred_at"]
    assert report.datasets == (A,)


def test_a_cycle_terminates() -> None:
    graph = _graph(
        [
            (f"{A}#x", f"{B}#y", "x"),
            (f"{B}#y", f"{A}#x", "y"),
        ]
    )

    report = simulate_impact(graph, f"{A}#x")

    assert {item.urn for item in report.impacted} == {f"{A}#x", f"{B}#y"}


def test_impacted_datasets_are_reported_and_sorted() -> None:
    graph = _graph(
        [
            (f"{A}#amount", f"{B}#gross_revenue", "SUM(amount)"),
            (f"{B}#gross_revenue", f"{C}#lifetime_value", "SUM(gross_revenue)"),
        ]
    )

    report = simulate_impact(graph, f"{A}#amount")

    assert report.datasets == (B, C, A)[0:0] + tuple(sorted({A, B, C}))
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_impact_simulation.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# apps/api/src/lineage_api/services/impact_simulation.py
"""Element-level blast radius over the composed lineage graph.

Seed a changed element and follow every proven derivation out of it. Severity is a
function of distance: the seed is the SOURCE, anything derived directly from it will
BREAK, anything further downstream is a WARN because a transform in between may
absorb the change.
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_impact_simulation.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/lineage_api/services/impact_simulation.py apps/api/tests/services/test_impact_simulation.py
git commit -m "feat: simulate element-level blast radius over the composed graph"
```

---

### Task 2: Blast radius across real repositories

Prove the simulation crosses a repository boundary — the case a per-repo graph cannot answer.

**Files:**
- Test: `apps/api/tests/services/test_impact_simulation.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to apps/api/tests/services/test_impact_simulation.py
ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
SELECTION = AnalyzerSelection(
    "sql-transformation-v1",
    "sql-transformation-rules-v1",
    "git-checkout",
    "sql-transformation",
    "postgres",
)


class _RepoSnapshot:
    environment = "staging"
    platform = "snowflake"
    system = "payments"
    analyzer_pack = "sql-transformation-v1"
    ruleset = "sql-transformation-rules-v1"
    revision = "d" * 40
    scope_digest = "sha256:" + "2" * 64

    def __init__(self, repo: str, paths: tuple[str, ...]) -> None:
        self.repository = repo
        self.paths = paths

    def read_bytes(self, relative_path: str) -> bytes:
        return (
            ROOT / "fixtures" / "repositories" / self.repository / relative_path
        ).read_bytes()


def test_changing_a_source_column_breaks_across_two_repositories() -> None:
    registry = AnalyzerRegistry.default(sql_resolver=Resolver.from_path(CATALOG))
    warehouse = registry.analyze(
        _RepoSnapshot("warehouse-sql", ("sql/daily_revenue.sql",)),
        SELECTION,
        "run-1",
        "corr-1",
    )
    risk = registry.analyze(
        _RepoSnapshot("risk-model-sql", ("sql/customer_features.sql",)),
        SELECTION,
        "run-2",
        "corr-2",
    )
    graph = compose_repository_documents((warehouse.document, risk.document))

    report = simulate_impact(graph, f"{A}#amount")

    by_urn = {item.urn: item for item in report.impacted}
    assert by_urn[f"{B}#gross_revenue"].severity == "BREAK"
    assert by_urn[f"{C}#lifetime_value"].severity == "WARN"
    assert by_urn[f"{C}#lifetime_value"].via == ("SUM(amount)", "SUM(gross_revenue)")
    assert report.max_hops == 2
```

- [ ] **Step 2: Run to verify it fails, then passes**

If Task 1 is complete this test should pass immediately — which would mean it proves nothing new. Confirm it fails first by temporarily analysing only `warehouse-sql`; the `lifetime_value` assertion must fail, demonstrating that the cross-repository join is what makes the two-hop radius visible.

- [ ] **Step 3: Commit**

```bash
git add apps/api/tests/services/test_impact_simulation.py
git commit -m "test: prove blast radius crosses a repository boundary"
```

---

## Final verification

- [ ] Full suite green
- [ ] A change to `raw.transactions#amount` reports a two-hop radius ending in `risk.customer_features#lifetime_value`, with both transforms on the path
