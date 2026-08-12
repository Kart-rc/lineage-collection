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
