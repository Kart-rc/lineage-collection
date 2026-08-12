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
