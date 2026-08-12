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


# --- Task 2: projection pairing -------------------------------------------------------

from lineage_api.services.sql_transformation_sca import (  # noqa: E402
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


# --- Task 3: file walk with exact citations -------------------------------------------

from lineage_api.services.sql_transformation_sca import (  # noqa: E402
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
