"""Deterministic SQL transformation cell.

Reads transformation statements and emits column-to-column DERIVES edges. It never
executes repository code and never infers a column list it cannot read from the
statement itself: an unprovable shape becomes typed residue, not a guess.
"""

from __future__ import annotations

from dataclasses import dataclass

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
