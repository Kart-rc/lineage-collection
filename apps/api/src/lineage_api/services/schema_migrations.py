"""Discover a schema from Flyway migrations.

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


def replay_migrations(sources: tuple[MigrationSource, ...]) -> MigratedSchema:
    tables: dict[str, SchemaTable] = {}
    residue: list[MigrationResidue] = []
    complete = True

    for source in sources:
        text = source.content.decode("utf-8", errors="strict")
        try:
            statements = sqlglot.parse(text, dialect=source.dialect)
        except (ParseError, TokenError):
            residue.append(
                MigrationResidue("malformed-migration-sql", source.path, "")
            )
            complete = False
            continue

        for index, statement in enumerate(statements):
            if statement is None:
                continue
            if isinstance(statement, exp.Create):
                kind = str(statement.args.get("kind", "")).upper()
                if kind == "TABLE":
                    node = statement.this
                    table = node.this if isinstance(node, exp.Schema) else node
                    if not isinstance(table, exp.Table):
                        residue.append(
                            MigrationResidue(
                                "unmodelled-migration-statement", source.path, "CREATE"
                            )
                        )
                        complete = False
                        continue
                    name, schema_name = _table_name(table)
                    if name in tables and statement.args.get("exists"):
                        continue
                    tables[name] = SchemaTable(
                        name=name,
                        schema=schema_name,
                        columns=tuple(_columns_of(statement, source.path, index)),
                        path=source.path,
                        statement_index=index,
                    )
                    continue
                if kind in {"INDEX", "DATABASE", "SCHEMA", "VIEW"}:
                    # Inventory-only: none of these change a table's identity or columns.
                    continue
                residue.append(
                    MigrationResidue(
                        "unmodelled-migration-statement", source.path, kind or "CREATE"
                    )
                )
                complete = False
                continue

            if isinstance(statement, exp.Drop):
                kind = str(statement.args.get("kind", "")).upper()
                target = statement.this
                if kind == "TABLE" and isinstance(target, exp.Table):
                    tables.pop(target.name, None)
                    continue
                if kind in {"INDEX", "VIEW"}:
                    continue
                residue.append(
                    MigrationResidue(
                        "unmodelled-migration-statement", source.path, kind or "DROP"
                    )
                )
                complete = False
                continue

            if isinstance(statement, exp.Alter):
                handled, entry = _apply_alter(statement, tables, source.path, index)
                if not handled:
                    residue.append(entry)
                    complete = False
                continue

            residue.append(
                MigrationResidue(
                    "unmodelled-migration-statement",
                    source.path,
                    type(statement).__name__.upper(),
                )
            )
            complete = False

    ordered_tables = tuple(sorted(tables.values(), key=lambda item: item.name))
    return MigratedSchema(
        tables=ordered_tables,
        residue=tuple(residue),
        complete=complete,
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
