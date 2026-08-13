# Migration-Based Schema Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discover the production schema from Flyway and Liquibase migrations, not only from a single `db/<profile>/schema.sql` — the way most real Spring estates actually define their schema.

**Architecture:** Migrations are an *ordered* sequence of DDL, so the schema is the result of replaying them, not the union of their statements. A set containing `DROP TABLE` or `DROP COLUMN` cannot be reduced by appending facts: appending would emit a table that no longer exists. A dedicated module replays ordered sources into a normalised schema, keeping the exact citation of the statement that created each table and column, and the Java cell emits its existing `sql.table` / `sql.column` facts from that result. No change to the fact contract, the resolver, or the edge compiler.

**Tech Stack:** Python 3.12, sqlglot 27.28.1, `xml.etree` (already used for pom parsing), pytest.

## Global Constraints

- **Replay, never accumulate.** The emitted schema is the state after the last migration.
- **Order is proven, never guessed.** Flyway order comes from the `V<version>__` prefix parsed as a numeric tuple. Two migrations with the same version are an ambiguity (`ambiguous-migration-version`), not an arbitrary tie-break.
- **Repeatable migrations (`R__`) are excluded.** They re-run on checksum change and are typically views or procedures; including them in a schema replay would be a guess about ordering.
- **A statement the replay cannot model is residue and makes the schema incomplete.** Fail closed: a partial replay must not present itself as the schema.
- **Citations stay exact.** A table cites the migration and statement that created it; a column cites the statement that added it.
- **No execution.** Migrations are parsed, never run.
- Test command prefix: `uv run --project apps/api python -m pytest`

---

## File Structure

| File | Responsibility |
|---|---|
| `apps/api/src/lineage_api/services/schema_migrations.py` (create) | Identify, order, and replay migration sources into a normalised schema. |
| `apps/api/src/lineage_api/services/java_spring_sca.py` (modify) | Emit `sql.table`/`sql.column` from a replayed schema when migrations are in scope. |
| `apps/api/src/lineage_api/services/analyzer_registry.py` (modify) | Select migration paths into scope and assign their dialect. |
| `apps/api/tests/services/test_schema_migrations.py` (create) | Ordering, replay, drops, and every residue code. |

---

### Task 1: Identify and order Flyway migrations

**Interfaces:**
- `FLYWAY_MIGRATION = re.compile(r"^V(?P<version>\d+(?:[._]\d+)*)__(?P<name>.+)\.sql$")`
- `@dataclass(frozen=True, slots=True) MigrationSource(path: str, content: bytes, dialect: str, order: tuple[int, ...])`
- `is_flyway_migration(path: str) -> bool` — any `db/migration/**` path whose basename matches the pattern
- `order_migrations(sources) -> tuple[tuple[MigrationSource, ...], tuple[MigrationResidue, ...]]`

- [ ] **Step 1:** Write tests: `V1__init.sql` before `V2__add.sql` before `V10__more.sql` (numeric, not lexical); `V1.1__x.sql` and `V1_1__x.sql` both parse to `(1, 1)`; `R__views.sql` excluded; two files at the same version yield `ambiguous-migration-version` and no ordering; a non-matching name under `db/migration` is `unrecognised-migration-name`.
- [ ] **Step 2:** Run; confirm `ModuleNotFoundError`.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run; green.
- [ ] **Step 5:** `git commit -m "feat: identify and order Flyway migrations deterministically"`

---

### Task 2: Replay DDL into a normalised schema

**Interfaces:**
- `@dataclass(frozen=True, slots=True) SchemaColumn(name: str, data_type: str, primary_key: bool, path: str, statement_index: int)`
- `@dataclass(frozen=True, slots=True) SchemaTable(name: str, schema: str, columns: tuple[SchemaColumn, ...], path: str, statement_index: int)`
- `@dataclass(frozen=True, slots=True) MigratedSchema(tables: tuple[SchemaTable, ...], residue: tuple[MigrationResidue, ...], complete: bool)`
- `replay_migrations(sources: tuple[MigrationSource, ...]) -> MigratedSchema`

Statements modelled: `CREATE TABLE`, `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ADD COLUMN`, `ALTER TABLE DROP COLUMN`, `DROP TABLE`, `DROP TABLE IF EXISTS`. Session statements (`CREATE DATABASE`, `USE`) and `CREATE INDEX` are inventory-only. Anything else — `ALTER TABLE RENAME`, `ALTER COLUMN`, DML — is `unmodelled-migration-statement` and sets `complete = False`.

- [ ] **Step 1:** Write tests: two migrations where the second adds a column, yielding one table with both columns; a dropped column absent from the result; a dropped table absent entirely; `DROP TABLE IF EXISTS` on an unknown table is a no-op, not an error; `ALTER TABLE RENAME` sets `complete=False` with `unmodelled-migration-statement`; the replay is deterministic across input orderings.
- [ ] **Step 2–4:** Red, implement, green.
- [ ] **Step 5:** `git commit -m "feat: replay ordered migrations into a normalised schema"`

---

### Task 3: Liquibase XML changelogs

**Interfaces:**
- `is_liquibase_changelog(path: str) -> bool` — `**/db/changelog/**/*.xml` or a basename containing `changelog`
- `replay_liquibase(sources) -> MigratedSchema`

Elements modelled: `createTable`, `addColumn`, `dropColumn`, `dropTable`. `include`/`includeAll` are followed **only** to a file present in scope; a missing include sets `complete = False` with `missing-changelog-include` rather than silently analysing a partial changelog. `changeSet` ordering is file order, then document order — Liquibase's own semantics.

- [ ] **Step 1:** Write tests: a `createTable` with two columns; an `addColumn` in a later changeSet; a `dropColumn`; a master changelog with two `include`s replayed in order; an include naming a file not in scope sets `complete=False`; YAML/JSON changelogs are `unsupported-changelog-format` (deliberate: only the XML schema is closed enough to parse without guessing).
- [ ] **Step 2–4:** Red, implement, green.
- [ ] **Step 5:** `git commit -m "feat: replay Liquibase XML changelogs into a normalised schema"`

---

### Task 4: Wire into the Java cell and the scope

**Interfaces:** no new public API; `_is_profile_schema_path` gains migration siblings.

Rules:
- When a `db/<profile>/schema.sql` **and** migrations are both present, the explicit profile schema wins and migrations are `skipped` — it is the more specific declaration.
- When only migrations are present, the replayed schema supplies `sql.table` / `sql.column`.
- An incomplete replay emits no schema facts, so downstream resolution fails closed with `missing-schema-table` rather than binding to a partial schema.

- [ ] **Step 1:** Write a test analysing a fixture repository with `db/migration/V1__init.sql` + `V2__add_city.sql`, an entity, a repository and a caller, asserting a complete edge over the migrated table including the column added by the second migration.
- [ ] **Step 2–4:** Red, implement, green.
- [ ] **Step 5:** Run the full suite; `git commit -m "feat: resolve entity mappings against migration-derived schemas"`

---

## Final verification

- [ ] Full suite green
- [ ] A Flyway repository and a Liquibase repository each produce edges
- [ ] `docs/prototype-coverage.md` L04 records migrations as a supported schema source
