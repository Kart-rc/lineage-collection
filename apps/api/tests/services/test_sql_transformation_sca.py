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

    assert isinstance(result, SqlStatementTarget)
    assert result.target_table == "analytics.daily_revenue"
    assert result.source_table == "raw.transactions"
    assert result.target_columns == ("customer_id", "gross_revenue")
    assert result.sources == ("raw.transactions",)


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


def test_insert_without_a_column_list_defers_the_column_order() -> None:
    """The order is not in the statement, so classification leaves it empty."""
    result = classify_statement(
        _stmt("INSERT INTO a.b SELECT y FROM c.d"), "sql/x.sql", 7
    )

    assert isinstance(result, SqlStatementTarget)
    assert result.target_table == "a.b"
    assert result.target_columns == ()


def test_implicit_columns_are_residue_when_no_catalog_can_supply_the_order() -> None:
    analysis = analyze_sql_sources(
        (SqlTransformationSource("sql/x.sql", b"INSERT INTO a.b SELECT y FROM c.d;", "postgres"),)
    )

    assert analysis.shapes == ()
    assert [item.code for item in analysis.residue] == ["implicit-target-columns"]


def test_implicit_columns_resolve_positionally_from_the_declared_order() -> None:
    analysis = analyze_sql_sources(
        (SqlTransformationSource("sql/x.sql", b"INSERT INTO a.b SELECT y, z FROM c.d;", "postgres"),),
        table_columns=lambda table: ("first", "second") if table == "a.b" else None,
    )

    assert [p.target_column for p in analysis.shapes[0].projections] == ["first", "second"]


def test_an_arity_mismatch_against_the_declared_order_is_a_finding() -> None:
    """Statement and catalog disagreeing is a finding, not something to guess through."""
    analysis = analyze_sql_sources(
        (SqlTransformationSource("sql/x.sql", b"INSERT INTO a.b SELECT y FROM c.d;", "postgres"),),
        table_columns=lambda table: ("first", "second", "third"),
    )

    assert analysis.shapes == ()
    assert [item.code for item in analysis.residue] == ["implicit-target-columns"]


def test_more_than_one_source_table_is_carried_not_refused() -> None:
    """A join is resolvable when its columns are qualified, so classification keeps both."""
    result = classify_statement(
        _stmt("INSERT INTO a.b (x) SELECT d.y FROM c.d JOIN e.f ON 1 = 1"),
        "sql/x.sql",
        2,
    )

    assert isinstance(result, SqlStatementTarget)
    assert result.sources == ("c.d", "e.f")


def test_a_statement_with_no_source_table_is_residue() -> None:
    result = classify_statement(
        _stmt("INSERT INTO a.b (x) SELECT 1"), "sql/x.sql", 2
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


# --- Task 4: resolution and DERIVES edges ---------------------------------------------

from pathlib import Path  # noqa: E402

from lineage_api.services.resolver import ResolveContext, Resolver  # noqa: E402
from lineage_api.services.sql_transformation_sca import (  # noqa: E402
    compile_derivation_edges,
)

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


# --- Task 5: fixture parity with the Python cell --------------------------------------

import json  # noqa: E402

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


# --- SELECT * expansion ----------------------------------------------------------------


def test_select_star_expands_against_the_declared_source_columns() -> None:
    """`SELECT *` is deterministic once the source columns are known — not a guess."""
    analysis = analyze_sql_sources(
        (SqlTransformationSource("q.sql", b"CREATE TABLE xxx AS SELECT * FROM t1;", "hive"),),
        table_columns=lambda table: ("a", "b", "c") if table == "t1" else None,
    )

    assert analysis.residue == ()
    projections = analysis.shapes[0].projections
    assert [(p.target_column, p.source_columns) for p in projections] == [
        ("a", ("a",)),
        ("b", ("b",)),
        ("c", ("c",)),
    ]


def test_select_star_without_known_source_columns_stays_residue() -> None:
    analysis = analyze_sql_sources(
        (SqlTransformationSource("q.sql", b"CREATE TABLE xxx AS SELECT * FROM t1;", "hive"),)
    )

    assert analysis.shapes == ()
    assert [r.code for r in analysis.residue] == ["star-projection"]


def test_star_expansion_uses_the_declared_order() -> None:
    analysis = analyze_sql_sources(
        (SqlTransformationSource("q.sql", b"INSERT INTO t2 SELECT * FROM t1;", "hive"),),
        table_columns=lambda table: ("x", "y") if table in {"t1", "t2"} else None,
    )

    assert [p.target_column for p in analysis.shapes[0].projections] == ["x", "y"]


# --- joins -----------------------------------------------------------------------------


def test_a_join_resolves_each_column_to_the_source_its_alias_names() -> None:
    """`SELECT a.id, b.name FROM t1 a JOIN t2 b` is unambiguous: the alias says which."""
    analysis = analyze_sql_sources(
        (
            SqlTransformationSource(
                "q.sql",
                b"CREATE TABLE xxx AS SELECT a.id AS id, b.name AS name "
                b"FROM t1 a JOIN t2 b ON a.id = b.id;",
                "hive",
            ),
        )
    )

    assert analysis.residue == ()
    shape = analysis.shapes[0]
    assert {(p.target_column, p.source_columns) for p in shape.projections} == {
        ("id", ("t1.id",)),
        ("name", ("t2.name",)),
    }


def test_an_unqualified_column_in_a_join_is_ambiguous_and_refused() -> None:
    """Without a qualifier the column could come from either side; that is not provable."""
    analysis = analyze_sql_sources(
        (
            SqlTransformationSource(
                "q.sql",
                b"CREATE TABLE xxx AS SELECT id AS id FROM t1 a JOIN t2 b ON a.id = b.id;",
                "hive",
            ),
        )
    )

    assert analysis.shapes == ()
    assert "ambiguous-join-column" in {r.code for r in analysis.residue}
