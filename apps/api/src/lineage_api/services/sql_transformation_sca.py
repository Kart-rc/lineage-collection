"""Deterministic SQL transformation cell.

Reads transformation statements and emits column-to-column DERIVES edges. It never
executes repository code and never infers a column list it cannot read from the
statement itself: an unprovable shape becomes typed residue, not a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import hashlib

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError
from sqlglot.tokens import TokenType

from lineage_api.domain.evidence import ScaEdgeEvidence
from lineage_api.services.resolver import (
    RawName,
    ResolveContext,
    ResolvedName,
    Resolver,
)


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
    # A join has several sources. The alias map is what makes each projected column
    # attributable to exactly one of them; without it the column is not provable.
    sources: tuple[str, ...] = ()
    aliases: tuple[tuple[str, str], ...] = ()


def _union_branches(union: exp.Union) -> list[exp.Select]:
    """Flatten a possibly-nested UNION into its SELECT branches, in written order."""
    branches: list[exp.Select] = []
    for side in (union.this, union.expression):
        if isinstance(side, exp.Union):
            branches.extend(_union_branches(side))
        elif isinstance(side, exp.Select):
            branches.append(side)
    return branches


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
    if isinstance(select, exp.Union):
        # Every branch writes the same target, so the target column names come from the
        # first branch and each branch is classified against them separately.
        select = _union_branches(select)[0]
    if not isinstance(select, exp.Select):
        return SqlResidue("unsupported-statement", path, line, target_table)

    tables = list(select.find_all(exp.Table))
    sources = sorted({_table_name(item) for item in tables})
    if not sources:
        return SqlResidue("ambiguous-source-table", path, line, target_table)
    aliases = tuple(
        (item.alias, _table_name(item)) for item in tables if item.alias
    ) + tuple((_table_name(item), _table_name(item)) for item in tables)

    if isinstance(node, exp.Schema):
        target_columns = tuple(column.name for column in node.expressions)
    elif isinstance(statement, exp.Create):
        target_columns = tuple(item.alias_or_name for item in select.expressions)
        if not all(target_columns):
            return SqlResidue("unnamed-projection", path, line, target_table)
    else:
        # An INSERT without a column list maps positionally onto the table's columns.
        # That order is not in the statement, so it is left empty here and resolved by
        # the caller from the catalog — the authority on column order. If no catalog
        # answer is available the caller turns this back into residue.
        target_columns = ()

    return SqlStatementTarget(
        target_table, sources[0], target_columns, tuple(sources), aliases
    )


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
    aliases: tuple[tuple[str, str], ...] = (),
    multi_source: bool = False,
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
        if multi_source:
            alias_map = dict(aliases)
            qualified: list[str] = []
            unqualified = False
            for column in expression.find_all(exp.Column):
                table = column.table
                if not table or table not in alias_map:
                    unqualified = True
                    continue
                qualified.append(f"{alias_map[table]}.{column.name}")
            if unqualified:
                # The column could come from either side of the join. Picking one would
                # invent lineage, so the projection is refused.
                residue.append(
                    SqlResidue("ambiguous-join-column", path, line, target_column)
                )
                continue
            source_columns = tuple(sorted(set(qualified)))
        else:
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
    table_columns: "Callable[[str], tuple[str, ...] | None] | None" = None,
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
            branches = (
                _union_branches(statement.expression)
                if isinstance(statement.expression, exp.Union)
                else [statement.expression]
            )
            if len(branches) > 1:
                for branch in branches:
                    branch_target = classify_statement(statement, source.path, line)
                    if isinstance(branch_target, SqlResidue):
                        residue.append(branch_target)
                        break
                    branch_sources = sorted(
                        {_table_name(item) for item in branch.find_all(exp.Table)}
                    )
                    if len(branch_sources) != 1:
                        residue.append(
                            SqlResidue(
                                "ambiguous-source-table", source.path, line,
                                branch_target.target_table,
                            )
                        )
                        continue
                    projections, branch_residue = pair_projections(
                        branch_target.target_columns, branch, source.path, line
                    )
                    residue.extend(branch_residue)
                    if projections:
                        shapes.append(
                            SqlStatementShape(
                                branch_target.target_table,
                                branch_sources[0],
                                projections,
                                source.path,
                                line,
                            )
                        )
                continue

            select = statement.expression
            star = len(select.expressions) == 1 and isinstance(
                select.expressions[0], exp.Star
            )
            if star:
                # `SELECT *` is deterministic once the source columns are declared: each
                # source column maps to the same-named target column. Without a declared
                # source it stays residue, because the column set is unknowable.
                source_columns = (
                    table_columns(classified.source_table)
                    if table_columns is not None
                    else None
                )
                if source_columns is None:
                    residue.append(
                        SqlResidue("star-projection", source.path, line, "*")
                    )
                    continue
                shapes.append(
                    SqlStatementShape(
                        classified.target_table,
                        classified.source_table,
                        tuple(
                            SqlProjection(column, (column,), column)
                            for column in source_columns
                        ),
                        source.path,
                        line,
                    )
                )
                continue

            target_columns = classified.target_columns
            if not target_columns:
                # Positional INSERT: resolve the declared column order, and only accept
                # it when the arity matches the projection. A mismatch means the
                # statement and the catalog disagree, which is a finding, not a guess.
                declared = (
                    table_columns(classified.target_table)
                    if table_columns is not None
                    else None
                )
                projection_count = len(statement.expression.expressions)
                if declared is None or len(declared) != projection_count:
                    residue.append(
                        SqlResidue(
                            "implicit-target-columns",
                            source.path,
                            line,
                            classified.target_table,
                        )
                    )
                    continue
                target_columns = declared

            projections, projection_residue = pair_projections(
                target_columns,
                statement.expression,
                source.path,
                line,
                classified.aliases,
                len(classified.sources) > 1,
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
        # A join qualifies each column with its own table, so group by table and resolve
        # each independently rather than assuming one source for the whole statement.
        by_table: dict[str, set[str]] = {}
        for projection in shape.projections:
            for name in projection.source_columns:
                table, _, column = name.rpartition(".")
                by_table.setdefault(table or shape.source_table, set()).add(column)

        multi = len(by_table) > 1 or set(by_table) != {shape.source_table}
        if multi:
            resolved_sources: dict[str, dict[str, str]] = {}
            failed = False
            for table, columns in sorted(by_table.items()):
                result = _resolve_table(resolver, context, table, tuple(sorted(columns)))
                if not isinstance(result, ResolvedName):
                    code = (
                        "unresolved-element"
                        if getattr(result, "reason", "") == "UNKNOWN_ELEMENT"
                        else "unresolved-dataset"
                    )
                    residue.append(SqlResidue(code, shape.path, shape.line, table))
                    failed = True
                    continue
                resolved_sources[table] = {
                    urn.element: str(urn) for urn in result.element_urns
                }
            if failed:
                continue
            target = _resolve_table(
                resolver,
                context,
                shape.target_table,
                tuple(sorted({item.target_column for item in shape.projections})),
            )
            if not isinstance(target, ResolvedName):
                residue.append(
                    SqlResidue(
                        "unresolved-dataset", shape.path, shape.line, shape.target_table
                    )
                )
                continue
            target_urns = {urn.element: str(urn) for urn in target.element_urns}
            for projection in shape.projections:
                to_urn = target_urns[projection.target_column]
                for name in projection.source_columns:
                    table, _, column = name.rpartition(".")
                    from_urn = resolved_sources[table or shape.source_table][column]
                    identity = "|".join(
                        (repo, digest, shape.path, str(shape.line), from_urn, to_urn,
                         projection.transform, ruleset_version)
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
            continue

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
