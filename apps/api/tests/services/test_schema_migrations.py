import pytest

from lineage_api.services.schema_migrations import (
    MigrationSource,
    is_flyway_migration,
    order_migrations,
    replay_migrations,
)


def _source(path: str, text: str = "", dialect: str = "postgres") -> MigrationSource:
    return MigrationSource(path=path, content=text.encode(), dialect=dialect, order=())


# --- Task 1: identification and ordering ----------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/main/resources/db/migration/V1__init.sql", True),
        ("db/migration/V1.1__add.sql", True),
        ("db/migration/postgres/V2__more.sql", True),
        ("db/migration/R__views.sql", False),
        ("db/migration/notes.md", False),
        ("src/main/resources/db/postgres/schema.sql", False),
    ],
)
def test_flyway_migrations_are_identified_by_path_and_name(path, expected) -> None:
    assert is_flyway_migration(path) is expected


def test_migrations_order_numerically_not_lexically() -> None:
    ordered, residue = order_migrations(
        (
            _source("db/migration/V10__third.sql"),
            _source("db/migration/V2__second.sql"),
            _source("db/migration/V1__first.sql"),
        )
    )

    assert residue == ()
    assert [item.path.rsplit("/", 1)[-1] for item in ordered] == [
        "V1__first.sql",
        "V2__second.sql",
        "V10__third.sql",
    ]


def test_dotted_and_underscored_versions_are_equivalent() -> None:
    ordered, _ = order_migrations(
        (_source("db/migration/V1.1__a.sql"), _source("db/migration/V1_2__b.sql"))
    )

    assert [item.order for item in ordered] == [(1, 1), (1, 2)]


def test_two_migrations_at_the_same_version_are_ambiguous() -> None:
    ordered, residue = order_migrations(
        (_source("db/migration/V1__a.sql"), _source("db/migration/V1__b.sql"))
    )

    assert ordered == ()
    assert [item.code for item in residue] == ["ambiguous-migration-version"]


def test_repeatable_migrations_are_excluded() -> None:
    ordered, residue = order_migrations(
        (_source("db/migration/V1__a.sql"), _source("db/migration/R__views.sql"))
    )

    assert [item.path for item in ordered] == ["db/migration/V1__a.sql"]
    assert [item.code for item in residue] == ["repeatable-migration-excluded"]


# --- Task 2: replay --------------------------------------------------------------------


def _replay(*files: tuple[str, str]):
    ordered, _ = order_migrations(tuple(_source(path, text) for path, text in files))
    return replay_migrations(ordered)


def test_a_later_migration_adds_a_column_to_an_earlier_table() -> None:
    schema = _replay(
        (
            "db/migration/V1__init.sql",
            "create table owners (id integer primary key, last_name varchar(30));",
        ),
        ("db/migration/V2__city.sql", "alter table owners add column city varchar(80);"),
    )

    assert schema.complete is True
    assert [table.name for table in schema.tables] == ["owners"]
    assert [column.name for column in schema.tables[0].columns] == [
        "id",
        "last_name",
        "city",
    ]


def test_a_dropped_column_is_absent_from_the_result() -> None:
    schema = _replay(
        (
            "db/migration/V1__init.sql",
            "create table owners (id integer primary key, telephone varchar(20));",
        ),
        ("db/migration/V2__drop.sql", "alter table owners drop column telephone;"),
    )

    assert [column.name for column in schema.tables[0].columns] == ["id"]


def test_a_dropped_table_is_absent_entirely() -> None:
    schema = _replay(
        ("db/migration/V1__init.sql", "create table legacy (id integer);"),
        ("db/migration/V2__drop.sql", "drop table legacy;"),
    )

    assert schema.tables == ()
    assert schema.complete is True


def test_dropping_an_unknown_table_if_exists_is_a_no_op() -> None:
    schema = _replay(
        ("db/migration/V1__init.sql", "drop table if exists nothing;"),
        ("db/migration/V2__make.sql", "create table owners (id integer);"),
    )

    assert [table.name for table in schema.tables] == ["owners"]
    assert schema.complete is True


def test_a_statement_the_replay_cannot_model_makes_it_incomplete() -> None:
    schema = _replay(
        ("db/migration/V1__init.sql", "create table owners (id integer);"),
        ("db/migration/V2__rename.sql", "alter table owners rename to people;"),
    )

    assert schema.complete is False
    assert "unmodelled-migration-statement" in {item.code for item in schema.residue}


def test_the_column_citation_points_at_the_migration_that_added_it() -> None:
    schema = _replay(
        ("db/migration/V1__init.sql", "create table owners (id integer);"),
        ("db/migration/V2__city.sql", "alter table owners add column city varchar(80);"),
    )

    by_name = {column.name: column for column in schema.tables[0].columns}
    assert by_name["id"].path.endswith("V1__init.sql")
    assert by_name["city"].path.endswith("V2__city.sql")


def test_replay_is_independent_of_input_order() -> None:
    files = (
        (
            "db/migration/V1__init.sql",
            "create table owners (id integer primary key);",
        ),
        ("db/migration/V2__city.sql", "alter table owners add column city varchar(80);"),
    )

    assert _replay(*files) == _replay(*reversed(files))


def test_session_and_index_statements_do_not_break_a_replay() -> None:
    schema = _replay(
        (
            "db/migration/V1__init.sql",
            "create table owners (id integer);\ncreate index idx_owners on owners (id);",
        ),
    )

    assert [table.name for table in schema.tables] == ["owners"]
    assert schema.complete is True
