"""Discover a schema from Flyway migrations and Liquibase changelogs.

Most production Spring estates define their schema as an ordered sequence of migrations
rather than a single `schema.sql`. The schema is therefore the *result of replaying*
that sequence, not the union of its statements: a set containing `DROP TABLE` cannot be
reduced by appending facts without emitting a table that no longer exists.

Ordering is proven from the `V<version>__` prefix, never guessed. A statement the replay
cannot model marks the whole schema incomplete, so a partial replay never presents
itself as the schema — the same fail-closed discipline the rest of the cell uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

import xml.etree.ElementTree as ET

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

_VERSIONED = re.compile(r"^V(?P<version>\d+(?:[._]\d+)*)__(?P<name>.+)\.sql$")
_REPEATABLE = re.compile(r"^R__(?P<name>.+)\.sql$")


@dataclass(frozen=True, slots=True)
class MigrationResidue:
    code: str
    path: str
    symbol: str


@dataclass(frozen=True, slots=True)
class MigrationSource:
    path: str
    content: bytes
    dialect: str
    order: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SchemaColumn:
    name: str
    data_type: str
    primary_key: bool
    path: str
    statement_index: int


@dataclass(frozen=True, slots=True)
class SchemaTable:
    name: str
    schema: str
    columns: tuple[SchemaColumn, ...]
    path: str
    statement_index: int


@dataclass(frozen=True, slots=True)
class MigratedSchema:
    tables: tuple[SchemaTable, ...]
    residue: tuple[MigrationResidue, ...]
    complete: bool


def _in_migration_directory(path: str) -> bool:
    return "migration" in PurePosixPath(path).parts


def is_flyway_migration(path: str) -> bool:
    name = PurePosixPath(path).name
    return _in_migration_directory(path) and _VERSIONED.fullmatch(name) is not None


def _version_tuple(name: str) -> tuple[int, ...] | None:
    match = _VERSIONED.fullmatch(name)
    if match is None:
        return None
    raw = match.group("version").replace("_", ".")
    try:
        return tuple(int(part) for part in raw.split("."))
    except ValueError:
        return None


def order_migrations(
    sources: tuple[MigrationSource, ...],
) -> tuple[tuple[MigrationSource, ...], tuple[MigrationResidue, ...]]:
    residue: list[MigrationResidue] = []
    versioned: list[MigrationSource] = []

    for source in sources:
        name = PurePosixPath(source.path).name
        if _REPEATABLE.fullmatch(name) is not None:
            # Repeatable migrations re-run whenever their checksum changes and carry no
            # position in the sequence, so replaying them would be a guess about order.
            residue.append(
                MigrationResidue("repeatable-migration-excluded", source.path, name)
            )
            continue
        order = _version_tuple(name)
        if order is None:
            residue.append(
                MigrationResidue("unrecognised-migration-name", source.path, name)
            )
            continue
        versioned.append(
            MigrationSource(source.path, source.content, source.dialect, order)
        )

    seen: dict[tuple[int, ...], str] = {}
    for source in versioned:
        if source.order in seen:
            residue.append(
                MigrationResidue(
                    "ambiguous-migration-version",
                    source.path,
                    ".".join(str(part) for part in source.order),
                )
            )
            return (), tuple(residue)
        seen[source.order] = source.path

    ordered = tuple(sorted(versioned, key=lambda item: (item.order, item.path)))
    return ordered, tuple(residue)


def _table_name(table: exp.Table) -> tuple[str, str]:
    return table.name, table.db or ""


def _columns_of(statement: exp.Create, path: str, index: int) -> list[SchemaColumn]:
    schema = statement.this
    if not isinstance(schema, exp.Schema):
        return []
    columns: list[SchemaColumn] = []
    for definition in schema.expressions:
        if not isinstance(definition, exp.ColumnDef):
            continue
        primary_key = any(
            isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint)
            for constraint in definition.constraints
        )
        columns.append(
            SchemaColumn(
                name=definition.this.name,
                data_type=definition.args["kind"].sql() if definition.args.get("kind") else "",
                primary_key=primary_key,
                path=path,
                statement_index=index,
            )
        )
    return columns


def apply_sql_text(
    text: str,
    dialect: str,
    tables: dict[str, SchemaTable],
    path: str,
) -> list[MigrationResidue]:
    """Apply every DDL statement in `text` to `tables`, returning what it could not model."""
    try:
        statements = sqlglot.parse(text, dialect=dialect)
    except (ParseError, TokenError):
        return [MigrationResidue("malformed-migration-sql", path, "")]

    residue: list[MigrationResidue] = []
    for index, statement in enumerate(statements):
        if statement is None:
            continue
        entry = _apply_sql_statement(statement, tables, path, index)
        if entry is not None:
            residue.append(entry)
    return residue


def replay_migrations(sources: tuple[MigrationSource, ...]) -> MigratedSchema:
    tables: dict[str, SchemaTable] = {}
    residue: list[MigrationResidue] = []
    complete = True

    for source in sources:
        text = source.content.decode("utf-8", errors="strict")
        entries = apply_sql_text(text, source.dialect, tables, source.path)
        if entries:
            residue.extend(entries)
            complete = False

    ordered_tables = tuple(sorted(tables.values(), key=lambda item: item.name))
    return MigratedSchema(
        tables=ordered_tables, residue=tuple(residue), complete=complete
    )


def _apply_sql_statement(
    statement: exp.Expression,
    tables: dict[str, SchemaTable],
    path: str,
    index: int,
) -> MigrationResidue | None:
    """Apply one DDL statement, returning residue only for what it cannot model."""
    if isinstance(statement, exp.Create):
        kind = str(statement.args.get("kind", "")).upper()
        if kind == "TABLE":
            node = statement.this
            table = node.this if isinstance(node, exp.Schema) else node
            if not isinstance(table, exp.Table):
                return MigrationResidue("unmodelled-migration-statement", path, "CREATE")
            name, schema_name = _table_name(table)
            if name in tables and statement.args.get("exists"):
                return None
            tables[name] = SchemaTable(
                name=name,
                schema=schema_name,
                columns=tuple(_columns_of(statement, path, index)),
                path=path,
                statement_index=index,
            )
            return None
        if kind in {"INDEX", "DATABASE", "SCHEMA", "VIEW"}:
            # Inventory-only: none of these change a table's identity or its columns.
            return None
        return MigrationResidue("unmodelled-migration-statement", path, kind or "CREATE")

    if isinstance(statement, exp.Drop):
        kind = str(statement.args.get("kind", "")).upper()
        target = statement.this
        if kind == "TABLE" and isinstance(target, exp.Table):
            tables.pop(target.name, None)
            return None
        if kind in {"INDEX", "VIEW"}:
            return None
        return MigrationResidue("unmodelled-migration-statement", path, kind or "DROP")

    if isinstance(statement, exp.Alter):
        handled, entry = _apply_alter(statement, tables, path, index)
        return None if handled else entry

    return MigrationResidue(
        "unmodelled-migration-statement", path, type(statement).__name__.upper()
    )


def _apply_alter(
    statement: exp.Alter,
    tables: dict[str, SchemaTable],
    path: str,
    index: int,
) -> tuple[bool, MigrationResidue]:
    unmodelled = MigrationResidue("unmodelled-migration-statement", path, "ALTER")
    target = statement.this
    if not isinstance(target, exp.Table):
        return False, unmodelled
    table = tables.get(target.name)
    if table is None:
        return False, MigrationResidue("unknown-migration-table", path, target.name)

    for action in statement.args.get("actions", ()):
        if isinstance(action, exp.ColumnDef):
            primary_key = any(
                isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint)
                for constraint in action.constraints
            )
            column = SchemaColumn(
                name=action.this.name,
                data_type=action.args["kind"].sql() if action.args.get("kind") else "",
                primary_key=primary_key,
                path=path,
                statement_index=index,
            )
            table = SchemaTable(
                table.name,
                table.schema,
                table.columns + (column,),
                table.path,
                table.statement_index,
            )
            continue
        if isinstance(action, exp.Drop) and str(
            action.args.get("kind", "")
        ).upper() in {"COLUMN", ""}:
            dropped = action.this.name if action.this is not None else ""
            table = SchemaTable(
                table.name,
                table.schema,
                tuple(item for item in table.columns if item.name != dropped),
                table.path,
                table.statement_index,
            )
            continue
        return False, unmodelled

    tables[table.name] = table
    return True, unmodelled


# --------------------------------------------------------------------------------------
# Liquibase changelogs
#
# Liquibase is declarative: `<createTable tableName="owners">` is unambiguous structured
# data, so unlike Flyway it needs no SQL dialect parsing at all. Only the `<sql>` escape
# hatch falls back to the shared statement applier, which keeps both sources semantically
# identical.
# --------------------------------------------------------------------------------------

_CHANGELOG_SUFFIXES = frozenset({".yaml", ".yml", ".json"})

# Liquibase change types that can never alter the relational inventory (tables or
# columns): data loads, constraint marking, defaults, and sequences. `sqlFile` is
# deliberately absent — it references SQL this replay cannot see, so it stays
# fail-closed as `unmodelled-changelog-change`.
_INVENTORY_NEUTRAL_CHANGES = frozenset(
    {
        "loadData",
        "loadUpdateData",
        "addForeignKeyConstraint",
        "dropForeignKeyConstraint",
        "addPrimaryKey",
        "dropPrimaryKey",
        "addNotNullConstraint",
        "dropNotNullConstraint",
        "addDefaultValue",
        "dropDefaultValue",
        "addUniqueConstraint",
        "dropUniqueConstraint",
        "createSequence",
        "dropSequence",
        "alterSequence",
        "tagDatabase",
    }
)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def is_liquibase_changelog(path: str) -> bool:
    pure = PurePosixPath(path)
    if pure.suffix.lower() not in {".xml", *_CHANGELOG_SUFFIXES}:
        return False
    if "changelog" in pure.name.lower():
        return True
    return any("changelog" in part.lower() for part in pure.parts[:-1])


def _changelog_includes(root: ET.Element) -> list[str]:
    return [
        element.attrib["file"]
        for element in root
        if _local_name(element.tag) == "include" and "file" in element.attrib
    ]


def _apply_change(
    change: ET.Element,
    tables: dict[str, SchemaTable],
    path: str,
    index: int,
    dialect: str,
) -> list[MigrationResidue]:
    name = _local_name(change.tag)
    table_name = change.attrib.get("tableName", "")

    if name == "createTable" and table_name:
        columns: list[SchemaColumn] = []
        for column in change:
            if _local_name(column.tag) != "column":
                continue
            constraints = next(
                (c for c in column if _local_name(c.tag) == "constraints"), None
            )
            primary_key = (
                constraints is not None
                and constraints.attrib.get("primaryKey", "").lower() == "true"
            )
            columns.append(
                SchemaColumn(
                    name=column.attrib.get("name", ""),
                    data_type=column.attrib.get("type", ""),
                    primary_key=primary_key,
                    path=path,
                    statement_index=index,
                )
            )
        tables[table_name] = SchemaTable(
            name=table_name,
            schema=change.attrib.get("schemaName", ""),
            columns=tuple(columns),
            path=path,
            statement_index=index,
        )
        return []

    if name == "addColumn" and table_name in tables:
        table = tables[table_name]
        added = tuple(
            SchemaColumn(
                name=column.attrib.get("name", ""),
                data_type=column.attrib.get("type", ""),
                primary_key=False,
                path=path,
                statement_index=index,
            )
            for column in change
            if _local_name(column.tag) == "column"
        )
        tables[table_name] = SchemaTable(
            table.name, table.schema, table.columns + added, table.path,
            table.statement_index,
        )
        return []

    if name == "dropColumn" and table_name in tables:
        table = tables[table_name]
        dropped = {change.attrib.get("columnName", "")} | {
            column.attrib.get("name", "")
            for column in change
            if _local_name(column.tag) == "column"
        }
        tables[table_name] = SchemaTable(
            table.name,
            table.schema,
            tuple(item for item in table.columns if item.name not in dropped),
            table.path,
            table.statement_index,
        )
        return []

    if name == "dropTable" and table_name:
        tables.pop(table_name, None)
        return []

    if name == "sql":
        return apply_sql_text(change.text or "", dialect, tables, path)

    if name in {"createIndex", "dropIndex", "comment", "rollback", "preConditions"}:
        # Inventory-only or non-schema: none change a table's identity or columns.
        return []

    if name in _INVENTORY_NEUTRAL_CHANGES:
        # Constraint, data, sequence and default changes cannot add or remove
        # tables or columns. They are recorded so the evidence stays complete,
        # but — like `ignored-schema-statement` on the SQL side — they never
        # mark the replay incomplete.
        return [MigrationResidue("ignored-changelog-change", path, name)]

    return [MigrationResidue("unmodelled-changelog-change", path, name)]


def replay_liquibase(sources: tuple[MigrationSource, ...]) -> MigratedSchema:
    by_path = {source.path: source for source in sources}
    residue: list[MigrationResidue] = []
    complete = True
    parsed: dict[str, ET.Element] = {}

    for source in sources:
        if PurePosixPath(source.path).suffix.lower() in _CHANGELOG_SUFFIXES:
            # Only the XML schema is closed enough to read without guessing at structure.
            residue.append(
                MigrationResidue(
                    "unsupported-changelog-format",
                    source.path,
                    PurePosixPath(source.path).suffix,
                )
            )
            complete = False
            continue
        try:
            parsed[source.path] = ET.fromstring(
                source.content.decode("utf-8", errors="strict")
            )
        except ET.ParseError:
            residue.append(MigrationResidue("malformed-changelog", source.path, ""))
            complete = False

    included = {
        target for root in parsed.values() for target in _changelog_includes(root)
    }
    roots = sorted(path for path in parsed if path not in included)

    tables: dict[str, SchemaTable] = {}
    visited: set[str] = set()

    def walk(path: str) -> None:
        nonlocal complete
        if path in visited:
            return
        visited.add(path)
        root = parsed.get(path)
        if root is None:
            residue.append(MigrationResidue("missing-changelog-include", path, path))
            complete = False
            return
        dialect = by_path[path].dialect
        for index, element in enumerate(root):
            tag = _local_name(element.tag)
            if tag == "include":
                walk(element.attrib.get("file", ""))
                continue
            if tag == "includeAll":
                prefix = element.attrib.get("path", "")
                for candidate in sorted(p for p in parsed if p.startswith(prefix)):
                    walk(candidate)
                continue
            if tag != "changeSet":
                continue
            for change in element:
                entries = _apply_change(change, tables, path, index, dialect)
                if entries:
                    residue.extend(entries)
                    if any(
                        entry.code != "ignored-changelog-change" for entry in entries
                    ):
                        complete = False

    for root_path in roots:
        walk(root_path)

    return MigratedSchema(
        tables=tuple(sorted(tables.values(), key=lambda item: item.name)),
        residue=tuple(residue),
        complete=complete,
    )
