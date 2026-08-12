# Cross-Repository Dataset Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make lineage from separate repositories compose into one graph, and carry the dataset *system typing* the product prototype navigates by.

**Architecture:** URN identity already joins across repositories — `urn:ldp:{env}:{platform}:{system}:{dataset}` is repo-independent, and `ConsolidationService.edge_key_for` already merges assertions that agree. Two things are genuinely missing: the catalog carries no dataset *kind* (the prototype distinguishes `datastore`, `kafka`, `s3land`, `s3file`, `cache`, `search`, and colours/【groups the graph by it), and nothing proves that two repositories analysed independently produce edges that join. This plan adds typed datasets end to end and a composition harness that proves the join.

**Tech Stack:** Python 3.12, pytest, existing `lineage_api.services.resolver`, `lineage_api.services.consolidation`, `lineage_api.domain.urns`.

## Global Constraints

- **Dataset kind is catalog-owned, never inferred.** A dataset without an explicit `kind` in the catalog resolves with kind `UNKNOWN`; the analyzer never guesses from a name or a platform.
- **Closed kind vocabulary:** `DATASTORE`, `STREAM`, `LAKE_LANDING`, `LAKE_FILE`, `CACHE`, `SEARCH`, `UNKNOWN`. These map to the prototype's `datastore`, `kafka`, `s3land`, `s3file`, `cache`, `search`.
- **URN identity does not change.** Kind is an attribute of a resolved dataset, not part of its URN — changing the URN would break every stored edge.
- **Backwards compatible.** An existing catalog with no `kind` fields must keep resolving exactly as it does today.
- Test command prefix: `uv run --project apps/api python -m pytest`

---

## File Structure

| File | Responsibility |
|---|---|
| `apps/api/src/lineage_api/services/resolver.py` (modify) | Read and validate `kind`; expose it on `ResolvedName`. |
| `fixtures/catalog/catalog-snapshot-v1.json` (modify) | Add `kind` to the existing three datasets; add the datasets the multi-repo fixture needs. |
| `apps/api/src/lineage_api/services/composition.py` (create) | Join per-repository analyzer documents into one consolidated graph and report which datasets are shared. |
| `apps/api/tests/services/test_dataset_kind.py` (create) | Kind resolution and its fail-closed default. |
| `apps/api/tests/services/test_composition.py` (create) | The proof that two repositories compose. |

---

### Task 1: Typed datasets in the resolver

**Files:**
- Modify: `apps/api/src/lineage_api/services/resolver.py`
- Modify: `fixtures/catalog/catalog-snapshot-v1.json`
- Test: `apps/api/tests/services/test_dataset_kind.py`

**Interfaces:**
- Produces: `ResolvedName.kind: str` — one of the closed vocabulary; `UNKNOWN` when the catalog omits it.
- Produces: `DATASET_KINDS: frozenset[str]`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/services/test_dataset_kind.py
from pathlib import Path

import pytest

from lineage_api.services.resolver import (
    DATASET_KINDS,
    RawName,
    ResolveContext,
    ResolvedName,
    Resolver,
)

CATALOG = (
    Path(__file__).resolve().parents[4] / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
)


def _context(resolver: Resolver) -> ResolveContext:
    return ResolveContext(
        env="staging",
        platform="snowflake",
        system="payments",
        repo="r",
        digest="d" * 40,
        config={},
        snapshot_id=resolver.snapshot_id,
    )


def test_the_closed_kind_vocabulary_matches_the_product_model() -> None:
    assert DATASET_KINDS == frozenset(
        {
            "DATASTORE",
            "STREAM",
            "LAKE_LANDING",
            "LAKE_FILE",
            "CACHE",
            "SEARCH",
            "UNKNOWN",
        }
    )


def test_a_catalog_dataset_carries_its_declared_kind() -> None:
    resolver = Resolver.from_path(CATALOG)

    result = resolver.resolve(
        RawName("dataset", "raw.transactions", "SCA", ()), _context(resolver)
    )

    assert isinstance(result, ResolvedName)
    assert result.kind == "DATASTORE"


def test_a_dataset_without_a_declared_kind_resolves_as_unknown() -> None:
    resolver = Resolver(
        {
            "schemaVersion": "1.0.0",
            "snapshotId": "s1",
            "resolverVersion": "1.0.0",
            "vocabulary": {
                "environments": ["staging"],
                "platforms": ["snowflake"],
                "systems": ["payments"],
            },
            "datasets": [
                {
                    "catalogRef": "catalog://payments/untyped",
                    "env": "staging",
                    "platform": "snowflake",
                    "system": "payments",
                    "name": "untyped",
                    "aliases": [],
                    "elements": [],
                }
            ],
        }
    )

    result = resolver.resolve(
        RawName("dataset", "untyped", "SCA", ()),
        ResolveContext(
            env="staging",
            platform="snowflake",
            system="payments",
            repo="r",
            digest="d" * 40,
            config={},
            snapshot_id="s1",
        ),
    )

    assert isinstance(result, ResolvedName)
    assert result.kind == "UNKNOWN"


def test_an_unrecognised_kind_is_rejected_rather_than_passed_through() -> None:
    with pytest.raises(ValueError, match="dataset kind"):
        Resolver(
            {
                "schemaVersion": "1.0.0",
                "snapshotId": "s1",
                "resolverVersion": "1.0.0",
                "vocabulary": {
                    "environments": ["staging"],
                    "platforms": ["snowflake"],
                    "systems": ["payments"],
                },
                "datasets": [
                    {
                        "catalogRef": "catalog://payments/x",
                        "env": "staging",
                        "platform": "snowflake",
                        "system": "payments",
                        "name": "x",
                        "aliases": [],
                        "elements": [],
                        "kind": "MONGO",
                    }
                ],
            }
        )
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_dataset_kind.py -q`
Expected: FAIL — `ImportError: cannot import name 'DATASET_KINDS'`

- [ ] **Step 3: Implement**

In `resolver.py`, add near the top:

```python
DATASET_KINDS = frozenset(
    {"DATASTORE", "STREAM", "LAKE_LANDING", "LAKE_FILE", "CACHE", "SEARCH", "UNKNOWN"}
)
```

Add `kind: str = "UNKNOWN"` as the last field of the `ResolvedName` dataclass (a defaulted field keeps every existing constructor call valid).

In `Resolver.__init__`, validate every dataset's kind before storing:

```python
        for dataset in self._datasets:
            kind = dataset.get("kind", "UNKNOWN")
            if kind not in DATASET_KINDS:
                raise ValueError(f"unrecognised dataset kind: {kind!r}")
```

Where `ResolvedName(...)` is constructed (the `catalog-match` return around `resolver.py:145`), pass the kind through:

```python
            kind=str(dataset.get("kind", "UNKNOWN")),
```

Then add `"kind"` to the three existing datasets in `fixtures/catalog/catalog-snapshot-v1.json`:
- `raw.transactions` → `"DATASTORE"`
- `analytics.daily_revenue` → `"LAKE_FILE"`
- `risk.customer_features` → `"LAKE_FILE"`

- [ ] **Step 4: Run to verify it passes, and that nothing regressed**

Run: `uv run --project apps/api python -m pytest apps/api/tests -q`
Expected: PASS, 900 tests

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/lineage_api/services/resolver.py fixtures/catalog/catalog-snapshot-v1.json apps/api/tests/services/test_dataset_kind.py
git commit -m "feat: carry catalog-declared dataset kind through resolution"
```

---

### Task 2: Multi-repository composition

Prove that two repositories, analysed independently, produce edges that join into one graph through shared dataset identity — and report which datasets are the seams.

**Files:**
- Create: `apps/api/src/lineage_api/services/composition.py`
- Test: `apps/api/tests/services/test_composition.py`

**Interfaces:**
- Consumes: analyzer documents (`AnalyzerRunResult.document`), `ConsolidationService`.
- Produces:
  - `@dataclass(frozen=True, slots=True) RepositoryContribution(repo: str, edge_count: int, datasets: tuple[str, ...])`
  - `@dataclass(frozen=True, slots=True) ComposedGraph(edges: tuple[dict, ...], contributions: tuple[RepositoryContribution, ...], shared_datasets: tuple[str, ...])`
  - `compose_repository_documents(documents: tuple[dict, ...]) -> ComposedGraph`

`shared_datasets` is the load-bearing output: a dataset URN that appears in more than one repository's contribution is a proven cross-repository seam. If it is empty, the repositories do not compose and the graph is a set of islands.

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/services/test_composition.py
from lineage_api.services.composition import (
    ComposedGraph,
    compose_repository_documents,
)

A = "urn:ldp:staging:snowflake:payments:raw.transactions"
B = "urn:ldp:staging:snowflake:payments:analytics.daily_revenue"
C = "urn:ldp:staging:snowflake:payments:risk.customer_features"


def _document(repo: str, edges: list[tuple[str, str]]) -> dict:
    return {
        "repo": repo,
        "edges": [
            {
                "from": [source],
                "to": target,
                "edgeType": "DERIVES",
                "transform": "copy",
                "provenanceId": f"prov-{repo}-{index}",
            }
            for index, (source, target) in enumerate(edges)
        ],
    }


def test_two_repositories_sharing_a_dataset_compose_into_one_graph() -> None:
    warehouse = _document("warehouse-sql", [(f"{A}#amount", f"{B}#gross_revenue")])
    risk = _document("risk-model", [(f"{B}#gross_revenue", f"{C}#lifetime_value")])

    graph = compose_repository_documents((warehouse, risk))

    assert isinstance(graph, ComposedGraph)
    assert len(graph.edges) == 2
    assert graph.shared_datasets == (B,)
    assert [c.repo for c in graph.contributions] == ["risk-model", "warehouse-sql"]


def test_repositories_with_no_common_dataset_report_no_seam() -> None:
    one = _document("a", [(f"{A}#amount", f"{B}#gross_revenue")])
    two = _document("b", [(f"{C}#customer_id", f"{C}#lifetime_value")])

    graph = compose_repository_documents((one, two))

    assert graph.shared_datasets == ()


def test_a_chain_across_three_repositories_is_traversable() -> None:
    graph = compose_repository_documents(
        (
            _document("r1", [(f"{A}#amount", f"{B}#gross_revenue")]),
            _document("r2", [(f"{B}#gross_revenue", f"{C}#lifetime_value")]),
        )
    )

    downstream = {edge["to"] for edge in graph.edges}
    upstream = {edge["from"][0] for edge in graph.edges}
    # the seam element is both produced by r1 and consumed by r2
    assert f"{B}#gross_revenue" in downstream
    assert f"{B}#gross_revenue" in upstream


def test_composition_is_order_independent() -> None:
    one = _document("a", [(f"{A}#amount", f"{B}#gross_revenue")])
    two = _document("b", [(f"{B}#gross_revenue", f"{C}#lifetime_value")])

    assert compose_repository_documents((one, two)) == compose_repository_documents(
        (two, one)
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_composition.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'lineage_api.services.composition'`

- [ ] **Step 3: Implement**

```python
# apps/api/src/lineage_api/services/composition.py
"""Join per-repository analyzer documents into one graph.

Repository boundaries are an artefact of how code is stored, not of how data flows.
Two repositories compose when they name the same dataset URN; this module makes that
seam explicit so an empty seam set is a visible finding rather than a silent island.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RepositoryContribution:
    repo: str
    edge_count: int
    datasets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ComposedGraph:
    edges: tuple[dict, ...]
    contributions: tuple[RepositoryContribution, ...]
    shared_datasets: tuple[str, ...]


def _dataset_of(urn: str) -> str:
    return urn.rsplit("#", 1)[0]


def compose_repository_documents(documents: tuple[dict, ...]) -> ComposedGraph:
    contributions: list[RepositoryContribution] = []
    edges: list[dict] = []
    datasets_by_repo: dict[str, set[str]] = {}

    for document in documents:
        repo = str(document.get("repo", ""))
        document_edges = list(document.get("edges", ()))
        datasets: set[str] = set()
        for edge in document_edges:
            datasets.add(_dataset_of(str(edge["to"])))
            for source in edge.get("from", ()):
                datasets.add(_dataset_of(str(source)))
        datasets_by_repo.setdefault(repo, set()).update(datasets)
        edges.extend(document_edges)

    for repo in sorted(datasets_by_repo):
        contributions.append(
            RepositoryContribution(
                repo=repo,
                edge_count=sum(
                    1
                    for document in documents
                    if str(document.get("repo", "")) == repo
                    for _ in document.get("edges", ())
                ),
                datasets=tuple(sorted(datasets_by_repo[repo])),
            )
        )

    shared = sorted(
        dataset
        for dataset in {name for names in datasets_by_repo.values() for name in names}
        if sum(1 for names in datasets_by_repo.values() if dataset in names) > 1
    )

    return ComposedGraph(
        edges=tuple(
            sorted(edges, key=lambda edge: (str(edge["to"]), str(edge["from"][0])))
        ),
        contributions=tuple(contributions),
        shared_datasets=tuple(shared),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_composition.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/lineage_api/services/composition.py apps/api/tests/services/test_composition.py
git commit -m "feat: compose per-repository lineage into one graph with explicit seams"
```

---

### Task 3: A real multi-repository fixture that composes

Add a second SQL repository that consumes what `warehouse-sql` produces, and assert the two compose through a real analyzer run rather than hand-built documents.

**Files:**
- Create: `fixtures/repositories/risk-model-sql/sql/customer_features.sql`
- Test: `apps/api/tests/services/test_composition.py` (append)
- Modify: `fixtures/catalog/catalog-snapshot-v1.json` (add the elements the new repo needs)

**Interfaces:**
- Consumes: `AnalyzerRegistry` (`sql-transformation-v1`), `compose_repository_documents`.

- [ ] **Step 1: Write the failing test**

```python
# append to apps/api/tests/services/test_composition.py
from pathlib import Path

from lineage_api.services.analyzer_registry import AnalyzerRegistry, AnalyzerSelection
from lineage_api.services.resolver import Resolver

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json"

SELECTION = AnalyzerSelection(
    "sql-transformation-v1",
    "sql-transformation-rules-v1",
    "git-checkout",
    "sql-transformation",
    "snowflake",
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
        return (ROOT / "fixtures" / "repositories" / self.repository / relative_path).read_bytes()


def test_two_real_sql_repositories_compose_through_a_shared_dataset() -> None:
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

    assert warehouse.status == "COMPLETE"
    assert risk.status == "COMPLETE"

    graph = compose_repository_documents((warehouse.document, risk.document))

    assert graph.shared_datasets == (
        "urn:ldp:staging:snowflake:payments:analytics.daily_revenue",
    )
    assert len(graph.edges) == warehouse.edge_count + risk.edge_count
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_composition.py -q -k real_sql`
Expected: FAIL — `FileNotFoundError` for `risk-model-sql/sql/customer_features.sql`

- [ ] **Step 3: Create the fixture and extend the catalog**

```sql
-- fixtures/repositories/risk-model-sql/sql/customer_features.sql
-- Consumes what warehouse-sql produces: the cross-repository seam.
INSERT INTO risk.customer_features (customer_id, lifetime_value)
SELECT customer_id, SUM(gross_revenue)
FROM analytics.daily_revenue
GROUP BY customer_id;
```

`risk.customer_features` already exists in the catalog with elements `customer_id` and `lifetime_value`, and `analytics.daily_revenue` already has `customer_id` and `gross_revenue` — no catalog change is required. Verify this before editing anything; if a required element is missing, add only that element.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --project apps/api python -m pytest apps/api/tests -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fixtures/repositories/risk-model-sql apps/api/tests/services/test_composition.py
git commit -m "test: prove two SQL repositories compose through a shared dataset"
```

---

## Final verification

- [ ] `uv run --project apps/api python -m pytest apps/api/tests -q` — all green
- [ ] `compose_repository_documents` reports a non-empty `shared_datasets` for the two fixture repositories
- [ ] Update `docs/prototype-coverage.md` L01 to state that dataset kind is catalog-owned and that cross-repository composition has a proof
