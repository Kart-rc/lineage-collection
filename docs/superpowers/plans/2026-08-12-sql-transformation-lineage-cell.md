# SQL Transformation Lineage Cell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic analyzer cell that reads SQL transformation statements and emits column-to-column `DERIVES` edges with their transform expressions — the derivation lineage the product prototype is built around, which no current cell produces.

**Architecture:** A new closed cell, `sql-transformation-v1`, parallel to `java-spring-data-jpa-v1` and `python-fixture-v1`. It parses `.sql` files with sqlglot (already a dependency, 27.28.1), reduces each supported statement to a target table, a source table, and a list of target-column-to-projection pairings, resolves both tables through the existing `Resolver` to element URNs, and emits `ScaEdgeEvidence` with `edge_type="DERIVES"`. Every statement shape it cannot prove becomes typed residue rather than a guess — the same fail-closed discipline the Java cell uses.

**Tech Stack:** Python 3.12, sqlglot 27.28.1, pytest, existing `lineage_api.services.resolver` and `lineage_api.domain.evidence`.

## Global Constraints

- **No new edge-kind field is needed.** `ScaEdgeEvidence.edge_type` already discriminates access (`READS`/`WRITES`) from derivation (`DERIVES`). Do not add a discriminator column; use `DERIVES`.
- **Never guess.** Any statement shape whose column ownership is not provable from the statement text alone becomes residue with a typed code. No default schemas, no inferred column lists, no star expansion.
- **Determinism.** The same bytes must always produce the same edges in the same order. No wall-clock, no randomness, no set iteration order in output.
- **Citations are exact.** Every edge and every residue entry carries the source path and the 1-based line where its statement begins, derived from `sqlglot.tokenize`.
- **Repository code is never executed.** This cell only parses text.
- **Residue codes are a closed vocabulary** (defined in Task 3). Adding a code means adding a test.
- Test command prefix throughout: `uv run --project apps/api python -m pytest`
- Commit messages: conventional commits (`feat:`, `test:`, `fix:`).

---

## File Structure

| File | Responsibility |
|---|---|
| `apps/api/src/lineage_api/services/sql_transformation_sca.py` (create) | The whole cell: source model, statement classification, projection pairing, analysis walk, edge compilation. One file, mirroring how `sca.py` holds the Python cell. |
| `apps/api/tests/services/test_sql_transformation_sca.py` (create) | Unit tests for every function and every residue code. |
| `fixtures/repositories/warehouse-sql/` (create) | A fixture repository: transformation SQL plus `expected-lineage.json`. |
| `apps/api/src/lineage_api/services/analyzer_registry.py` (modify) | Register the `sql-transformation-v1` pack, its source scope, and its adapter. |

**Why one module:** the Python cell (`sca.py`, 321 lines) and the Java cell (`java_spring_sca.py`) each live in a single service module. Follow that pattern rather than splitting by layer.

---

### Task 1: Statement classification

Reduce one parsed SQL statement to the three facts an edge needs — target table, source table, target column names — or reject it with a typed residue code.

**Files:**
- Create: `apps/api/src/lineage_api/services/sql_transformation_sca.py`
- Test: `apps/api/tests/services/test_sql_transformation_sca.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `@dataclass(frozen=True, slots=True) SqlResidue(code: str, path: str, line: int, symbol: str)`
  - `@dataclass(frozen=True, slots=True) SqlStatementTarget(target_table: str, source_table: str, target_columns: tuple[str, ...])`
  - `classify_statement(statement: exp.Expression, path: str, line: int) -> SqlStatementTarget | SqlResidue`

Supported shapes: `INSERT INTO t (cols) SELECT …`, `CREATE TABLE t AS SELECT …`, `CREATE VIEW v [(cols)] AS SELECT …`.

For `CREATE` without an explicit column list, target columns come from projection aliases (`alias_or_name`). For `INSERT` without a column list the order depends on table DDL the statement does not contain, so it is rejected.

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/services/test_sql_transformation_sca.py
from sqlglot import parse_one

from lineage_api.services.sql_transformation_sca import (
    SqlResidue,
    SqlStatementTarget,
    classify_statement,
)


def _stmt(sql: str):
    return parse_one(sql, dialect="postgres")


def test_insert_with_explicit_columns_is_classified() -> None:
    result = classify_statement(
        _stmt(
            "INSERT INTO analytics.daily_revenue (customer_id, gross_revenue) "
            "SELECT customer_id, SUM(amount) FROM raw.transactions GROUP BY customer_id"
        ),
        "sql/rev.sql",
        3,
    )

    assert result == SqlStatementTarget(
        target_table="analytics.daily_revenue",
        source_table="raw.transactions",
        target_columns=("customer_id", "gross_revenue"),
    )


def test_create_table_as_select_takes_columns_from_projection_aliases() -> None:
    result = classify_statement(
        _stmt(
            "CREATE TABLE analytics.rev AS "
            "SELECT customer_id, SUM(amount) AS total FROM raw.transactions GROUP BY customer_id"
        ),
        "sql/rev.sql",
        1,
    )

    assert isinstance(result, SqlStatementTarget)
    assert result.target_columns == ("customer_id", "total")


def test_insert_without_a_column_list_is_residue() -> None:
    result = classify_statement(
        _stmt("INSERT INTO a.b SELECT y FROM c.d"), "sql/x.sql", 7
    )

    assert result == SqlResidue("implicit-target-columns", "sql/x.sql", 7, "a.b")


def test_more_than_one_source_table_is_residue() -> None:
    result = classify_statement(
        _stmt("INSERT INTO a.b (x) SELECT y FROM c.d JOIN e.f ON 1 = 1"),
        "sql/x.sql",
        2,
    )

    assert result == SqlResidue("ambiguous-source-table", "sql/x.sql", 2, "a.b")


def test_a_statement_that_is_not_a_transformation_is_residue() -> None:
    result = classify_statement(
        _stmt("CREATE TABLE a.b (id integer)"), "sql/ddl.sql", 1
    )

    assert result == SqlResidue("unsupported-statement", "sql/ddl.sql", 1, "a.b")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_sql_transformation_sca.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lineage_api.services.sql_transformation_sca'`

- [ ] **Step 3: Write the minimal implementation**

```python
# apps/api/src/lineage_api/services/sql_transformation_sca.py
"""Deterministic SQL transformation cell.

Reads transformation statements and emits column-to-column DERIVES edges. It never
executes repository code and never infers a column list it cannot read from the
statement itself: an unprovable shape becomes typed residue, not a guess.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp


@dataclass(frozen=True, slots=True)
class SqlResidue:
    code: str
    path: str
    line: int
    symbol: str


@dataclass(frozen=True, slots=True)
class SqlStatementTarget:
    target_table: str
    source_table: str
    target_columns: tuple[str, ...]


def _table_name(table: exp.Table) -> str:
    return f"{table.db}.{table.name}" if table.db else table.name


def classify_statement(
    statement: exp.Expression, path: str, line: int
) -> SqlStatementTarget | SqlResidue:
    if not isinstance(statement, (exp.Insert, exp.Create)):
        return SqlResidue("unsupported-statement", path, line, "")
    if isinstance(statement, exp.Create) and statement.args.get("kind") not in {
        "TABLE",
        "VIEW",
    }:
        return SqlResidue("unsupported-statement", path, line, "")

    node = statement.this
    target_table_node = node.this if isinstance(node, exp.Schema) else node
    if not isinstance(target_table_node, exp.Table):
        return SqlResidue("unsupported-statement", path, line, "")
    target_table = _table_name(target_table_node)

    select = statement.expression
    if not isinstance(select, exp.Select):
        return SqlResidue("unsupported-statement", path, line, target_table)

    sources = sorted({_table_name(item) for item in select.find_all(exp.Table)})
    if len(sources) != 1:
        return SqlResidue("ambiguous-source-table", path, line, target_table)

    if isinstance(node, exp.Schema):
        target_columns = tuple(column.name for column in node.expressions)
    elif isinstance(statement, exp.Create):
        target_columns = tuple(item.alias_or_name for item in select.expressions)
        if not all(target_columns):
            return SqlResidue("unnamed-projection", path, line, target_table)
    else:
        return SqlResidue("implicit-target-columns", path, line, target_table)

    return SqlStatementTarget(target_table, sources[0], target_columns)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_sql_transformation_sca.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/lineage_api/services/sql_transformation_sca.py apps/api/tests/services/test_sql_transformation_sca.py
git commit -m "feat: classify SQL transformation statements into provable targets"
```

---

### Task 2: Projection pairing

Pair each target column with its projection, extract the source columns it references and the transform expression text.

**Files:**
- Modify: `apps/api/src/lineage_api/services/sql_transformation_sca.py`
- Test: `apps/api/tests/services/test_sql_transformation_sca.py`

**Interfaces:**
- Consumes: `SqlResidue` from Task 1.
- Produces:
  - `@dataclass(frozen=True, slots=True) SqlProjection(target_column: str, source_columns: tuple[str, ...], transform: str)`
  - `pair_projections(target_columns: tuple[str, ...], select: exp.Select, path: str, line: int) -> tuple[tuple[SqlProjection, ...], tuple[SqlResidue, ...]]`

A projection referencing several source columns yields one `SqlProjection` carrying all of them — Task 4 turns that into one edge per source column, which is how a column derived from two inputs is represented.

- [ ] **Step 1: Write the failing tests**

```python
# append to apps/api/tests/services/test_sql_transformation_sca.py
from lineage_api.services.sql_transformation_sca import (
    SqlProjection,
    pair_projections,
)


def _select(sql: str):
    return parse_one(sql, dialect="postgres").expression


def test_each_target_column_pairs_with_its_projection() -> None:
    select = _select(
        "INSERT INTO a.b (customer_id, gross_revenue, revenue_date) "
        "SELECT customer_id, SUM(amount), DATE(occurred_at) FROM raw.transactions"
    )

    projections, residue = pair_projections(
        ("customer_id", "gross_revenue", "revenue_date"), select, "sql/rev.sql", 1
    )

    assert residue == ()
    assert projections == (
        SqlProjection("customer_id", ("customer_id",), "customer_id"),
        SqlProjection("gross_revenue", ("amount",), "SUM(amount)"),
        SqlProjection("revenue_date", ("occurred_at",), "DATE(occurred_at)"),
    )


def test_a_projection_over_two_columns_keeps_both_sources() -> None:
    select = _select("INSERT INTO a.b (total) SELECT net + tax FROM raw.t")

    projections, residue = pair_projections(("total",), select, "sql/x.sql", 4)

    assert residue == ()
    assert projections == (SqlProjection("total", ("net", "tax"), "net + tax"),)


def test_a_star_projection_is_residue() -> None:
    select = _select("INSERT INTO a.b (x) SELECT * FROM c.d")

    projections, residue = pair_projections(("x",), select, "sql/x.sql", 9)

    assert projections == ()
    assert residue == (SqlResidue("star-projection", "sql/x.sql", 9, "*"),)


def test_a_column_count_mismatch_is_residue() -> None:
    select = _select("INSERT INTO a.b (x, y) SELECT z FROM c.d")

    projections, residue = pair_projections(("x", "y"), select, "sql/x.sql", 5)

    assert projections == ()
    assert residue == (SqlResidue("projection-arity-mismatch", "sql/x.sql", 5, "x,y"),)


def test_a_constant_projection_is_residue_and_emits_nothing() -> None:
    select = _select("INSERT INTO a.b (x, y) SELECT z, 1 FROM c.d")

    projections, residue = pair_projections(("x", "y"), select, "sql/x.sql", 6)

    assert projections == (SqlProjection("x", ("z",), "z"),)
    assert residue == (SqlResidue("constant-projection", "sql/x.sql", 6, "y"),)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_sql_transformation_sca.py -v -k pair or star or constant or arity or projection`
Expected: FAIL — `ImportError: cannot import name 'SqlProjection'`

- [ ] **Step 3: Write the minimal implementation**

```python
# add to apps/api/src/lineage_api/services/sql_transformation_sca.py

@dataclass(frozen=True, slots=True)
class SqlProjection:
    target_column: str
    source_columns: tuple[str, ...]
    transform: str


def pair_projections(
    target_columns: tuple[str, ...],
    select: exp.Select,
    path: str,
    line: int,
) -> tuple[tuple[SqlProjection, ...], tuple[SqlResidue, ...]]:
    if any(isinstance(item, exp.Star) for item in select.expressions):
        return (), (SqlResidue("star-projection", path, line, "*"),)
    if len(target_columns) != len(select.expressions):
        return (), (
            SqlResidue(
                "projection-arity-mismatch", path, line, ",".join(target_columns)
            ),
        )

    projections: list[SqlProjection] = []
    residue: list[SqlResidue] = []
    for target_column, item in zip(target_columns, select.expressions):
        expression = item.this if isinstance(item, exp.Alias) else item
        source_columns = tuple(
            sorted({column.name for column in expression.find_all(exp.Column)})
        )
        if not source_columns:
            residue.append(SqlResidue("constant-projection", path, line, target_column))
            continue
        projections.append(
            SqlProjection(target_column, source_columns, expression.sql())
        )
    return tuple(projections), tuple(residue)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_sql_transformation_sca.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/lineage_api/services/sql_transformation_sca.py apps/api/tests/services/test_sql_transformation_sca.py
git commit -m "feat: pair SQL target columns with their projections and transforms"
```

---

### Task 3: File walk with exact line citations

Walk `.sql` sources, split them into statements, attach the 1-based start line of each, and collect shapes and residue. Malformed SQL becomes residue instead of raising.

**Files:**
- Modify: `apps/api/src/lineage_api/services/sql_transformation_sca.py`
- Test: `apps/api/tests/services/test_sql_transformation_sca.py`

**Interfaces:**
- Consumes: `classify_statement` (Task 1), `pair_projections` (Task 2).
- Produces:
  - `@dataclass(frozen=True, slots=True) SqlTransformationSource(path: str, content: bytes, dialect: str)`
  - `@dataclass(frozen=True, slots=True) SqlStatementShape(target_table: str, source_table: str, projections: tuple[SqlProjection, ...], path: str, line: int)`
  - `@dataclass(frozen=True, slots=True) SqlTransformationAnalysis(shapes: tuple[SqlStatementShape, ...], residue: tuple[SqlResidue, ...], files_analyzed: int)`
  - `analyze_sql_sources(sources: tuple[SqlTransformationSource, ...]) -> SqlTransformationAnalysis`

Closed residue vocabulary after this task: `unsupported-statement`, `implicit-target-columns`, `ambiguous-source-table`, `unnamed-projection`, `star-projection`, `projection-arity-mismatch`, `constant-projection`, `malformed-sql`. Task 4 adds `unresolved-dataset` and `unresolved-element`.

- [ ] **Step 1: Write the failing tests**

```python
# append to apps/api/tests/services/test_sql_transformation_sca.py
from lineage_api.services.sql_transformation_sca import (
    SqlTransformationSource,
    analyze_sql_sources,
)


def _source(text: str, path: str = "sql/rev.sql") -> SqlTransformationSource:
    return SqlTransformationSource(path, text.encode(), "postgres")


def test_each_statement_is_cited_at_the_line_it_starts_on() -> None:
    analysis = analyze_sql_sources(
        (
            _source(
                "-- daily rollup\n"
                "INSERT INTO analytics.daily_revenue (customer_id)\n"
                "SELECT customer_id FROM raw.transactions;\n"
                "\n"
                "INSERT INTO analytics.other (cid) SELECT customer_id FROM raw.transactions;\n"
            ),
        )
    )

    assert [shape.line for shape in analysis.shapes] == [2, 5]
    assert analysis.files_analyzed == 1


def test_malformed_sql_is_residue_and_does_not_raise() -> None:
    analysis = analyze_sql_sources((_source("CREATE TABLE ???"),))

    assert analysis.shapes == ()
    assert [item.code for item in analysis.residue] == ["malformed-sql"]


def test_shapes_and_residue_are_deterministically_ordered() -> None:
    text = (
        "INSERT INTO b.b (x) SELECT y FROM c.d;\n"
        "INSERT INTO a.a (x) SELECT y FROM c.d;\n"
    )

    first = analyze_sql_sources((_source(text, "sql/z.sql"), _source(text, "sql/a.sql")))
    second = analyze_sql_sources((_source(text, "sql/a.sql"), _source(text, "sql/z.sql")))

    assert [(s.path, s.line) for s in first.shapes] == [
        ("sql/a.sql", 1),
        ("sql/a.sql", 2),
        ("sql/z.sql", 1),
        ("sql/z.sql", 2),
    ]
    assert first.shapes == second.shapes
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_sql_transformation_sca.py -v -k "cited or malformed or deterministically"`
Expected: FAIL — `ImportError: cannot import name 'analyze_sql_sources'`

- [ ] **Step 3: Write the minimal implementation**

```python
# add imports at the top of apps/api/src/lineage_api/services/sql_transformation_sca.py
import sqlglot
from sqlglot.errors import ParseError, TokenError
from sqlglot.tokens import TokenType


# add to apps/api/src/lineage_api/services/sql_transformation_sca.py

@dataclass(frozen=True, slots=True)
class SqlTransformationSource:
    path: str
    content: bytes
    dialect: str


@dataclass(frozen=True, slots=True)
class SqlStatementShape:
    target_table: str
    source_table: str
    projections: tuple[SqlProjection, ...]
    path: str
    line: int


@dataclass(frozen=True, slots=True)
class SqlTransformationAnalysis:
    shapes: tuple[SqlStatementShape, ...]
    residue: tuple[SqlResidue, ...]
    files_analyzed: int


def _statement_start_lines(text: str, dialect: str) -> list[int]:
    tokens = sqlglot.tokenize(text, dialect=dialect)
    if not tokens:
        return []
    lines = [tokens[0].line]
    for index, token in enumerate(tokens):
        if token.token_type is TokenType.SEMICOLON and index + 1 < len(tokens):
            lines.append(tokens[index + 1].line)
    return lines


def analyze_sql_sources(
    sources: tuple[SqlTransformationSource, ...],
) -> SqlTransformationAnalysis:
    shapes: list[SqlStatementShape] = []
    residue: list[SqlResidue] = []

    for source in sorted(sources, key=lambda item: item.path):
        text = source.content.decode("utf-8", errors="strict")
        try:
            statements = sqlglot.parse(text, dialect=source.dialect)
            lines = _statement_start_lines(text, source.dialect)
        except (ParseError, TokenError):
            residue.append(SqlResidue("malformed-sql", source.path, 1, ""))
            continue

        for index, statement in enumerate(statements):
            if statement is None:
                continue
            line = lines[index] if index < len(lines) else 1
            classified = classify_statement(statement, source.path, line)
            if isinstance(classified, SqlResidue):
                residue.append(classified)
                continue
            projections, projection_residue = pair_projections(
                classified.target_columns, statement.expression, source.path, line
            )
            residue.extend(projection_residue)
            if not projections:
                continue
            shapes.append(
                SqlStatementShape(
                    classified.target_table,
                    classified.source_table,
                    projections,
                    source.path,
                    line,
                )
            )

    return SqlTransformationAnalysis(
        shapes=tuple(shapes),
        residue=tuple(sorted(residue, key=lambda item: (item.path, item.line, item.code))),
        files_analyzed=len(sources),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_sql_transformation_sca.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/lineage_api/services/sql_transformation_sca.py apps/api/tests/services/test_sql_transformation_sca.py
git commit -m "feat: walk SQL sources into cited transformation shapes"
```

---

### Task 4: Resolve URNs and emit DERIVES edges

Turn shapes into `ScaEdgeEvidence`. One edge per (source column, target column) pair. A table the resolver quarantines, or a column absent from the resolved dataset, becomes residue.

**Files:**
- Modify: `apps/api/src/lineage_api/services/sql_transformation_sca.py`
- Test: `apps/api/tests/services/test_sql_transformation_sca.py`

**Interfaces:**
- Consumes: `SqlTransformationAnalysis` (Task 3); `Resolver`, `RawName`, `ResolveContext`, `ResolvedName` from `lineage_api.services.resolver`; `ScaEdgeEvidence` from `lineage_api.domain.evidence`.
- Produces:
  - `compile_derivation_edges(analysis: SqlTransformationAnalysis, resolver: Resolver, context: ResolveContext, *, repo: str, digest: str, run_id: str, correlation_id: str, ruleset_version: str) -> tuple[tuple[ScaEdgeEvidence, ...], tuple[SqlResidue, ...]]`

`ScaEdgeEvidence` field order (from `apps/api/src/lineage_api/domain/evidence.py:25`): `provenance_id, from_urn, to_urn, edge_type, transform, mechanism, exact, file, line, ast_path, repo, digest, run_id, correlation_id, resolver_version, snapshot_id`.

The provenance identity mirrors `sca.py:253-265` so both cells produce stable, comparable ids.

- [ ] **Step 1: Write the failing tests**

```python
# append to apps/api/tests/services/test_sql_transformation_sca.py
from pathlib import Path

from lineage_api.services.resolver import ResolveContext, Resolver
from lineage_api.services.sql_transformation_sca import compile_derivation_edges

CATALOG = (
    Path(__file__).resolve().parents[4] / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
)


def _resolver() -> Resolver:
    return Resolver.from_path(CATALOG)


def _context() -> ResolveContext:
    return ResolveContext(
        env="staging",
        platform="snowflake",
        system="payments",
        repo="warehouse-sql",
        digest="d" * 40,
        config={},
        snapshot_id="catalog-demo-v1",
    )


def _compile(text: str):
    analysis = analyze_sql_sources((_source(text, "sql/rev.sql"),))
    return compile_derivation_edges(
        analysis,
        _resolver(),
        _context(),
        repo="warehouse-sql",
        digest="d" * 40,
        run_id="run-1",
        correlation_id="corr-1",
        ruleset_version="sql-transformation-rules-v1",
    )


def test_a_derivation_edge_carries_element_urns_and_the_transform() -> None:
    edges, residue = _compile(
        "INSERT INTO analytics.daily_revenue (gross_revenue) "
        "SELECT SUM(amount) FROM raw.transactions;"
    )

    assert residue == ()
    assert len(edges) == 1
    assert edges[0].from_urn == (
        "urn:ldp:staging:snowflake:payments:raw.transactions#amount"
    )
    assert edges[0].to_urn == (
        "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue"
    )
    assert edges[0].edge_type == "DERIVES"
    assert edges[0].transform == "SUM(amount)"
    assert edges[0].mechanism == "SCA"


def test_a_projection_over_two_columns_emits_one_edge_per_source_column() -> None:
    edges, _ = _compile(
        "INSERT INTO analytics.daily_revenue (gross_revenue) "
        "SELECT amount + customer_id FROM raw.transactions;"
    )

    assert sorted(edge.from_urn.rsplit("#", 1)[-1] for edge in edges) == [
        "amount",
        "customer_id",
    ]
    assert {edge.to_urn.rsplit("#", 1)[-1] for edge in edges} == {"gross_revenue"}


def test_an_unknown_table_is_residue_and_emits_nothing() -> None:
    edges, residue = _compile(
        "INSERT INTO analytics.daily_revenue (gross_revenue) SELECT amount FROM raw.nope;"
    )

    assert edges == ()
    assert [item.code for item in residue] == ["unresolved-dataset"]


def test_a_column_absent_from_the_catalog_is_residue() -> None:
    edges, residue = _compile(
        "INSERT INTO analytics.daily_revenue (gross_revenue) "
        "SELECT no_such_column FROM raw.transactions;"
    )

    assert edges == ()
    assert [item.code for item in residue] == ["unresolved-element"]


def test_edges_are_deterministically_ordered() -> None:
    edges, _ = _compile(
        "INSERT INTO analytics.daily_revenue (revenue_date, customer_id) "
        "SELECT occurred_at, customer_id FROM raw.transactions;"
    )

    assert [edge.to_urn for edge in edges] == sorted(edge.to_urn for edge in edges)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_sql_transformation_sca.py -v -k "derivation or two_columns or unknown_table or absent or deterministically_ordered"`
Expected: FAIL — `ImportError: cannot import name 'compile_derivation_edges'`

- [ ] **Step 3: Write the minimal implementation**

```python
# add imports at the top of apps/api/src/lineage_api/services/sql_transformation_sca.py
import hashlib

from lineage_api.domain.evidence import ScaEdgeEvidence
from lineage_api.services.resolver import (
    RawName,
    ResolveContext,
    ResolvedName,
    Resolver,
)


# add to apps/api/src/lineage_api/services/sql_transformation_sca.py

def _resolve_table(
    resolver: Resolver,
    context: ResolveContext,
    table: str,
    elements: tuple[str, ...],
):
    """Resolve a table with the exact elements the statement touches.

    The resolver validates elements against the catalog and quarantines the whole
    dataset when one is unknown, so the quarantine reason — not a per-column check
    afterwards — is what distinguishes a missing table from a missing column.
    """
    return resolver.resolve(RawName("dataset", table, "SCA", elements), context)


def compile_derivation_edges(
    analysis: SqlTransformationAnalysis,
    resolver: Resolver,
    context: ResolveContext,
    *,
    repo: str,
    digest: str,
    run_id: str,
    correlation_id: str,
    ruleset_version: str,
) -> tuple[tuple[ScaEdgeEvidence, ...], tuple[SqlResidue, ...]]:
    edges: list[ScaEdgeEvidence] = []
    residue: list[SqlResidue] = []

    for shape in analysis.shapes:
        source_elements = tuple(
            sorted({name for item in shape.projections for name in item.source_columns})
        )
        target_elements = tuple(
            sorted({item.target_column for item in shape.projections})
        )
        source = _resolve_table(resolver, context, shape.source_table, source_elements)
        target = _resolve_table(resolver, context, shape.target_table, target_elements)

        unresolved = False
        for result, table in ((source, shape.source_table), (target, shape.target_table)):
            if isinstance(result, ResolvedName):
                continue
            unresolved = True
            code = (
                "unresolved-element"
                if getattr(result, "reason", "") == "UNKNOWN_ELEMENT"
                else "unresolved-dataset"
            )
            residue.append(SqlResidue(code, shape.path, shape.line, table))
        if unresolved:
            continue

        # Every requested element has a URN here: the resolver would have quarantined
        # the dataset otherwise, so no per-column existence check is reachable.
        source_urns = {urn.element: str(urn) for urn in source.element_urns}
        target_urns = {urn.element: str(urn) for urn in target.element_urns}

        for projection in shape.projections:
            to_urn = target_urns[projection.target_column]
            for source_column in projection.source_columns:
                from_urn = source_urns[source_column]
                identity = "|".join(
                    (
                        repo,
                        digest,
                        shape.path,
                        str(shape.line),
                        from_urn,
                        to_urn,
                        projection.transform,
                        ruleset_version,
                    )
                )
                edges.append(
                    ScaEdgeEvidence(
                        provenance_id=(
                            f"prov-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
                        ),
                        from_urn=from_urn,
                        to_urn=to_urn,
                        edge_type="DERIVES",
                        transform=projection.transform,
                        mechanism="SCA",
                        exact=True,
                        file=shape.path,
                        line=shape.line,
                        ast_path=f"statement[{shape.line}].{projection.target_column}",
                        repo=repo,
                        digest=digest,
                        run_id=run_id,
                        correlation_id=correlation_id,
                        resolver_version=target.resolver_version,
                        snapshot_id=target.snapshot_id,
                    )
                )

    ordered_edges = tuple(
        sorted(edges, key=lambda edge: (edge.to_urn, edge.from_urn, edge.transform))
    )
    ordered_residue = tuple(
        sorted(residue, key=lambda item: (item.path, item.line, item.code, item.symbol))
    )
    return ordered_edges, ordered_residue
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_sql_transformation_sca.py -v`
Expected: PASS (18 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/lineage_api/services/sql_transformation_sca.py apps/api/tests/services/test_sql_transformation_sca.py
git commit -m "feat: emit column-level DERIVES edges from SQL transformations"
```

---

### Task 5: Fixture repository and cross-producer parity test

Create a SQL fixture that expresses the same transformation the Python fixture expresses, and assert both cells produce the identical three edges. This is the test that proves the cell produces real derivation lineage rather than plausible output.

**Files:**
- Create: `fixtures/repositories/warehouse-sql/sql/daily_revenue.sql`
- Create: `fixtures/repositories/warehouse-sql/expected-lineage.json`
- Test: `apps/api/tests/services/test_sql_transformation_sca.py`

**Interfaces:**
- Consumes: `analyze_sql_sources`, `compile_derivation_edges` (Tasks 3-4).
- Produces: a fixture repository other plans can reuse.

The expected edges are copied verbatim from `fixtures/repositories/payments-pipeline/expected-lineage.json`.

- [ ] **Step 1: Write the failing test**

```python
# append to apps/api/tests/services/test_sql_transformation_sca.py
import json

FIXTURE = (
    Path(__file__).resolve().parents[4] / "fixtures" / "repositories" / "warehouse-sql"
)


def test_the_sql_fixture_reproduces_the_python_fixture_lineage() -> None:
    sql_path = FIXTURE / "sql" / "daily_revenue.sql"
    analysis = analyze_sql_sources(
        (
            SqlTransformationSource(
                "sql/daily_revenue.sql", sql_path.read_bytes(), "postgres"
            ),
        )
    )
    edges, residue = compile_derivation_edges(
        analysis,
        _resolver(),
        _context(),
        repo="warehouse-sql",
        digest="d" * 40,
        run_id="run-1",
        correlation_id="corr-1",
        ruleset_version="sql-transformation-rules-v1",
    )

    assert residue == ()
    expected = json.loads((FIXTURE / "expected-lineage.json").read_text())
    assert [
        {
            "from": edge.from_urn,
            "to": edge.to_urn,
            "type": edge.edge_type,
            "transform": edge.transform,
        }
        for edge in edges
    ] == expected["edges"]


def test_the_sql_fixture_matches_the_python_fixture_edge_set() -> None:
    python_expected = json.loads(
        (
            Path(__file__).resolve().parents[4]
            / "fixtures"
            / "repositories"
            / "payments-pipeline"
            / "expected-lineage.json"
        ).read_text()
    )
    sql_expected = json.loads((FIXTURE / "expected-lineage.json").read_text())

    assert {(e["from"], e["to"], e["type"]) for e in sql_expected["edges"]} == {
        (e["from"], e["to"], e["type"]) for e in python_expected["edges"]
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_sql_transformation_sca.py -v -k fixture`
Expected: FAIL — `FileNotFoundError` for `fixtures/repositories/warehouse-sql/sql/daily_revenue.sql`

- [ ] **Step 3: Create the fixture**

```sql
-- fixtures/repositories/warehouse-sql/sql/daily_revenue.sql
-- Seeded analysis fixture; statements are declarations for the static analyzer.
INSERT INTO analytics.daily_revenue (customer_id, gross_revenue, revenue_date)
SELECT customer_id, SUM(amount), DATE(occurred_at)
FROM raw.transactions
GROUP BY customer_id, DATE(occurred_at);
```

```json
{
  "schemaVersion": "1.0.0",
  "repo": "warehouse-sql",
  "edges": [
    {
      "from": "urn:ldp:staging:snowflake:payments:raw.transactions#customer_id",
      "to": "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#customer_id",
      "type": "DERIVES",
      "transform": "customer_id"
    },
    {
      "from": "urn:ldp:staging:snowflake:payments:raw.transactions#amount",
      "to": "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue",
      "type": "DERIVES",
      "transform": "SUM(amount)"
    },
    {
      "from": "urn:ldp:staging:snowflake:payments:raw.transactions#occurred_at",
      "to": "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#revenue_date",
      "type": "DERIVES",
      "transform": "DATE(occurred_at)"
    }
  ]
}
```

Write `expected-lineage.json` to `fixtures/repositories/warehouse-sql/expected-lineage.json`.

Note on ordering: `compile_derivation_edges` sorts by `(to_urn, from_urn, transform)`. For this fixture that gives `customer_id` < `gross_revenue` < `revenue_date` on the target element, which is exactly the order written above — copy the JSON verbatim and it will match.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_sql_transformation_sca.py -v`
Expected: PASS (20 tests)

- [ ] **Step 5: Commit**

```bash
git add fixtures/repositories/warehouse-sql apps/api/tests/services/test_sql_transformation_sca.py
git commit -m "test: prove the SQL cell reproduces the Python cell's derivation lineage"
```

---

### Task 6: Register the analyzer pack

Wire the cell into `AnalyzerRegistry` so it can be selected by a durable command, with its own source-scope rules.

**Files:**
- Modify: `apps/api/src/lineage_api/services/analyzer_registry.py`
- Test: `apps/api/tests/services/test_analyzer_registry.py`

**Interfaces:**
- Consumes: `analyze_sql_sources`, `compile_derivation_edges` (Tasks 3-4).
- Produces: pack `sql-transformation-v1`, ruleset `sql-transformation-rules-v1`, source kind `git-checkout`, framework `sql-transformation`, schema profiles `("postgres", "snowflake")`.
- Changed signature: `AnalyzerRegistry.default(python_analyzer: ScaAnalyzer | None = None, sql_resolver: Resolver | None = None) -> AnalyzerRegistry`

The resolver is **injected**, not loaded from a hardcoded path — this matches how `python-fixture-v1` receives its analyzer, and keeps the registry free of filesystem knowledge. When `sql_resolver` is `None` the pack still resolves but its `analyze` is `None`, exactly like the Python pack.

Source scope: `.sql` files under `sql/` are `selected`; `.md` and `expected-lineage.json` are `skipped`; everything else is `unsupported`.

- [ ] **Step 1: Write the failing test**

```python
# append to apps/api/tests/services/test_analyzer_registry.py
from pathlib import Path

from lineage_api.services.analyzer_registry import (
    AnalyzerRegistry,
    AnalyzerSelection,
)
from lineage_api.services.resolver import Resolver

SQL_CATALOG = (
    Path(__file__).resolve().parents[4] / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
)


def test_the_sql_transformation_pack_resolves_and_scopes_sql_files() -> None:
    registry = AnalyzerRegistry.default(sql_resolver=Resolver.from_path(SQL_CATALOG))
    selection = AnalyzerSelection(
        analyzer_pack="sql-transformation-v1",
        ruleset="sql-transformation-rules-v1",
        source_kind="git-checkout",
        framework="sql-transformation",
        schema_profile="snowflake",
    )

    definition = registry.resolve(selection)

    assert definition.analyzer_pack == "sql-transformation-v1"
    assert definition.analyze is not None


def test_the_sql_pack_selects_only_sql_under_the_sql_directory() -> None:
    class _Snapshot:
        repository = "warehouse-sql"
        revision = "d" * 40
        scope_digest = "sha256:" + "2" * 64
        environment = "staging"
        platform = "snowflake"
        system = "payments"
        analyzer_pack = "sql-transformation-v1"
        ruleset = "sql-transformation-rules-v1"
        paths = (
            "sql/daily_revenue.sql",
            "README.md",
            "expected-lineage.json",
            "scripts/seed.sql",
        )

        def read_bytes(self, relative_path: str) -> bytes:
            return b""

    scope = AnalyzerRegistry.default().source_scope(
        _Snapshot(),
        AnalyzerSelection(
            "sql-transformation-v1",
            "sql-transformation-rules-v1",
            "git-checkout",
            "sql-transformation",
            "snowflake",
        ),
    )

    assert scope.selected_scope == ("sql/daily_revenue.sql",)
    assert scope.skipped_scope == ("README.md", "expected-lineage.json")
    assert scope.unsupported_scope == ("scripts/seed.sql",)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/test_analyzer_registry.py -v -k sql`
Expected: FAIL — `AnalyzerSelectionError: UNKNOWN_ANALYZER_PACK`

- [ ] **Step 3: Write the minimal implementation**

In `analyzer_registry.py`, extend the existing resolver import (the module currently imports only `ResolveContext`) and add the cell import:

```python
from lineage_api.services.resolver import ResolveContext, Resolver
from lineage_api.services.sql_transformation_sca import (
    SqlTransformationSource,
    analyze_sql_sources,
    compile_derivation_edges,
)
```

Change the `default` classmethod signature and add the handler:

```python
    @classmethod
    def default(
        cls,
        python_analyzer: ScaAnalyzer | None = None,
        sql_resolver: Resolver | None = None,
    ) -> "AnalyzerRegistry":
        python_handler = (
            _PythonAnalyzerAdapter(python_analyzer).analyze
            if python_analyzer is not None
            else None
        )
        sql_handler = (
            _SqlTransformationAnalyzerAdapter(sql_resolver).analyze
            if sql_resolver is not None
            else None
        )
```

Add a third `AnalyzerDefinition` inside that tuple, after the Java entry:

```python
                AnalyzerDefinition(
                    "sql-transformation-v1",
                    "sql-transformation-rules-v1",
                    "git-checkout",
                    "sql-transformation",
                    ("postgres", "snowflake"),
                    (
                        sql_resolver.resolver_version
                        if sql_resolver is not None
                        else "catalog-resolver-v1"
                    ),
                    (
                        sql_resolver.snapshot_id
                        if sql_resolver is not None
                        else "catalog-snapshot-v1"
                    ),
                    sql_handler,
                ),
```

In `AnalyzerRegistry.source_scope`, replace the `if selection.analyzer_pack == "python-fixture-v1":` branch head with a three-way split by adding this branch before the existing `else`:

```python
        elif selection.analyzer_pack == "sql-transformation-v1":
            selected = tuple(
                path
                for path in expected
                if PurePosixPath(path).suffix == ".sql"
                and PurePosixPath(path).parts[:1] == ("sql",)
            )
            skipped = tuple(
                path
                for path in expected
                if PurePosixPath(path).suffix == ".md"
                or PurePosixPath(path).name == "expected-lineage.json"
            )
            unsupported = tuple(
                path
                for path in expected
                if path not in set(selected) and path not in set(skipped)
            )
```

Add the adapter class next to `_PythonAnalyzerAdapter`:

```python
class _SqlTransformationAnalyzerAdapter:
    def __init__(self, resolver: Resolver) -> None:
        self._resolver = resolver

    def analyze(
        self,
        snapshot: AnalyzerSnapshot,
        schema_profile: str,
        run_id: str,
        correlation_id: str,
    ) -> AnalyzerRunResult:
        resolver = self._resolver
        sources = tuple(
            SqlTransformationSource(path, snapshot.read_bytes(path), schema_profile)
            for path in snapshot.paths
            if PurePosixPath(path).suffix == ".sql"
        )
        analysis = analyze_sql_sources(sources)
        edges, resolution_residue = compile_derivation_edges(
            analysis,
            resolver,
            ResolveContext(
                env=snapshot.environment,
                platform=schema_profile,
                system=snapshot.system,
                repo=snapshot.repository,
                digest=snapshot.revision,
                config={},
                snapshot_id=resolver.snapshot_id,
            ),
            repo=snapshot.repository,
            digest=snapshot.revision,
            run_id=run_id,
            correlation_id=correlation_id,
            ruleset_version=snapshot.ruleset,
        )
        residue = analysis.residue + resolution_residue
        status = "INTEGRATION_REQUIRED" if residue else "COMPLETE"
        status_reasons = tuple(sorted({item.code for item in residue}))
        document = {
            "schemaVersion": "1.0.0",
            "repo": snapshot.repository,
            "digest": snapshot.revision,
            "runId": run_id,
            "correlationId": correlation_id,
            "rulesetVersion": snapshot.ruleset,
            "resolverVersion": resolver.resolver_version,
            "snapshotId": resolver.snapshot_id,
            "status": status,
            "statusReasons": list(status_reasons),
            "edges": [
                {
                    "provenanceId": edge.provenance_id,
                    "from": [edge.from_urn],
                    "to": edge.to_urn,
                    "edgeType": edge.edge_type,
                    "transform": edge.transform,
                    "mechanism": edge.mechanism,
                    "exact": edge.exact,
                    "file": edge.file,
                    "line": edge.line,
                }
                for edge in edges
            ],
            "residue": [
                {
                    "code": item.code,
                    "location": {"path": item.path, "line": item.line},
                    "symbol": item.symbol,
                }
                for item in residue
            ],
            "datasetsSeen": sorted(
                {edge.to_urn.rsplit("#", 1)[0] for edge in edges}
                | {edge.from_urn.rsplit("#", 1)[0] for edge in edges}
            ),
            "stats": {
                "filesAnalyzed": analysis.files_analyzed,
                "edgesEmitted": len(edges),
                "residueCount": len(residue),
                "quarantinedCount": 0,
            },
        }
        return AnalyzerRunResult(
            document=document,
            status=status,
            status_reasons=status_reasons,
            edge_count=len(edges),
            read_count=0,
            write_count=0,
            residue_count=len(residue),
            unresolved_count=0,
        )
```

Also extend `AnalyzerRegistry.classification_evidence` — `sql-transformation` is a `DATA_PIPELINE`, which the existing `else` branch already returns, so no change is needed there. Verify this rather than assuming.

- [ ] **Step 4: Run the full suite**

Run: `uv run --project apps/api python -m pytest apps/api/tests/services/ -q`
Expected: PASS, including the pre-existing registry tests (the new pack must not perturb `python-fixture-v1` or `java-spring-data-jpa-v1` scoping).

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/lineage_api/services/analyzer_registry.py apps/api/tests/services/test_analyzer_registry.py
git commit -m "feat: register the SQL transformation analyzer pack"
```

---

## Final verification

- [ ] Run the whole backend suite: `uv run --project apps/api python -m pytest apps/api/tests -q`
- [ ] Confirm output is pristine — no warnings, no skips other than the pre-existing JDK-guarded Java test.
- [ ] Confirm `docs/prototype-coverage.md` L04 row needs an update to mention the new cell, and make it.

---

## Out of scope — follow-on plans

These are the remaining gaps between the platform and the product prototype. Each needs its own plan; none is a prerequisite for this one.

1. **Cross-repository dataset identity** — pin an enterprise catalog snapshot as a first-class collection input and carry dataset system typing (`datastore`, `kafka`, `s3land`, `s3file`, `cache`, `search`) through the URN model. Without this every cell produces per-repository islands and the domain rollups never compose. *Highest priority after this plan.*
2. **Runtime on the collection path** — promote `services/runtime_verification.py` from a library to a collection stage with session binding and the consolidation join, so `runtimeStatus` stops being structurally `NOT_PROVIDED`.
3. **Real confidence** — stop hardcoding `exact=True` / `executed=False` (`java_spring_sca.py:1313-1314`), carry a signal set and last-observed timestamp per edge, and band them the way the prototype does (Verified ≥90 / Probable ≥65 / Inferred <65).
4. **Liveness and frequency** — per-edge observation counts and last-seen windows, and a policy decision to let runtime *demote* an unobserved static edge (the prototype's `HOT`/`WARM`/`COLD`/`DEAD?`). This changes the consolidation rule and needs an explicit decision, not just code.
5. **The Interactions plane** — a new `interaction-observation` contract (the current dataset-centric runtime contract cannot express it), inbound/outbound HTTP extraction in the Java cell, and OTel span enrichment beyond the host-only `CONNECTIVITY` edge at `runtime/adapters/otel.py:255-262`.
6. **Impact simulation** — element-level taint propagation and BREAK/WARN severity. Cheapest of the six, and mostly a graph query once this plan's `DERIVES` edges exist.
