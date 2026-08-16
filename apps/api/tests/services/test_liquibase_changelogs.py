from lineage_api.services.schema_migrations import (
    MigrationSource,
    is_liquibase_changelog,
    replay_liquibase,
)

NS = 'xmlns="http://www.liquibase.org/xml/ns/dbchangelog"'


def _source(path: str, text: str) -> MigrationSource:
    return MigrationSource(path=path, content=text.encode(), dialect="postgres", order=())


def _changelog(body: str) -> str:
    return f"<databaseChangeLog {NS}>{body}</databaseChangeLog>"


def test_changelogs_are_identified_by_path_or_name() -> None:
    assert is_liquibase_changelog("src/main/resources/db/changelog/db.changelog.xml")
    assert is_liquibase_changelog("db/changelog/changes/001-init.xml")
    assert is_liquibase_changelog("resources/db.changelog-master.xml")
    assert not is_liquibase_changelog("db/migration/V1__init.sql")
    assert not is_liquibase_changelog("pom.xml")


def test_a_create_table_changeset_yields_a_table_with_columns() -> None:
    schema = replay_liquibase(
        (
            _source(
                "db/changelog/db.changelog.xml",
                _changelog(
                    '<changeSet id="1" author="a">'
                    '<createTable tableName="customers">'
                    '<column name="id" type="int"><constraints primaryKey="true"/></column>'
                    '<column name="last_name" type="varchar(255)"/>'
                    "</createTable></changeSet>"
                ),
            ),
        )
    )

    assert schema.complete is True
    assert [table.name for table in schema.tables] == ["customers"]
    columns = schema.tables[0].columns
    assert [column.name for column in columns] == ["id", "last_name"]
    assert columns[0].primary_key is True
    assert columns[1].data_type == "varchar(255)"


def test_a_later_changeset_adds_and_drops_columns() -> None:
    schema = replay_liquibase(
        (
            _source(
                "db/changelog/db.changelog.xml",
                _changelog(
                    '<changeSet id="1" author="a">'
                    '<createTable tableName="customers">'
                    '<column name="id" type="int"/>'
                    '<column name="telephone" type="varchar(20)"/>'
                    "</createTable></changeSet>"
                    '<changeSet id="2" author="b">'
                    '<addColumn tableName="customers">'
                    '<column name="city" type="varchar(80)"/>'
                    "</addColumn></changeSet>"
                    '<changeSet id="3" author="c">'
                    '<dropColumn tableName="customers" columnName="telephone"/>'
                    "</changeSet>"
                ),
            ),
        )
    )

    assert [column.name for column in schema.tables[0].columns] == ["id", "city"]


def test_a_dropped_table_is_absent() -> None:
    schema = replay_liquibase(
        (
            _source(
                "db/changelog/db.changelog.xml",
                _changelog(
                    '<changeSet id="1" author="a">'
                    '<createTable tableName="legacy"><column name="id" type="int"/></createTable>'
                    "</changeSet>"
                    '<changeSet id="2" author="a"><dropTable tableName="legacy"/></changeSet>'
                ),
            ),
        )
    )

    assert schema.tables == ()
    assert schema.complete is True


def test_includes_are_expanded_in_document_order() -> None:
    schema = replay_liquibase(
        (
            _source(
                "db/changelog/db.changelog-master.xml",
                _changelog(
                    '<include file="db/changelog/changes/001-init.xml"/>'
                    '<include file="db/changelog/changes/002-city.xml"/>'
                ),
            ),
            _source(
                "db/changelog/changes/001-init.xml",
                _changelog(
                    '<changeSet id="1" author="a">'
                    '<createTable tableName="customers"><column name="id" type="int"/></createTable>'
                    "</changeSet>"
                ),
            ),
            _source(
                "db/changelog/changes/002-city.xml",
                _changelog(
                    '<changeSet id="2" author="b">'
                    '<addColumn tableName="customers">'
                    '<column name="city" type="varchar(80)"/></addColumn>'
                    "</changeSet>"
                ),
            ),
        )
    )

    assert schema.complete is True
    assert [column.name for column in schema.tables[0].columns] == ["id", "city"]


def test_an_include_outside_the_scope_makes_the_replay_incomplete() -> None:
    schema = replay_liquibase(
        (
            _source(
                "db/changelog/db.changelog-master.xml",
                _changelog('<include file="db/changelog/changes/missing.xml"/>'),
            ),
        )
    )

    assert schema.complete is False
    assert "missing-changelog-include" in {item.code for item in schema.residue}


def test_a_raw_sql_change_is_replayed_through_the_sql_applier() -> None:
    schema = replay_liquibase(
        (
            _source(
                "db/changelog/db.changelog.xml",
                _changelog(
                    '<changeSet id="1" author="a">'
                    "<sql>create table customers (id integer primary key)</sql>"
                    "</changeSet>"
                ),
            ),
        )
    )

    assert schema.complete is True
    assert [table.name for table in schema.tables] == ["customers"]


def test_inventory_neutral_changes_are_recorded_without_blocking_the_replay() -> None:
    # Constraint, data, sequence and default changes cannot add or remove tables
    # or columns, so they are inventory: recorded as residue, never incomplete.
    schema = replay_liquibase(
        (
            _source(
                "db/changelog/db.changelog.xml",
                _changelog(
                    '<changeSet id="1" author="a">'
                    '<createTable tableName="customers">'
                    '<column name="id" type="int"/>'
                    '<column name="email" type="varchar(255)"/>'
                    "</createTable></changeSet>"
                    '<changeSet id="2" author="a">'
                    '<loadData tableName="customers" file="data/customers.csv"/>'
                    '<addForeignKeyConstraint baseTableName="customers" baseColumnNames="id" '
                    'referencedTableName="accounts" referencedColumnNames="id" constraintName="fk"/>'
                    '<addPrimaryKey tableName="customers" columnNames="id"/>'
                    '<addNotNullConstraint tableName="customers" columnName="email"/>'
                    '<dropDefaultValue tableName="customers" columnName="email"/>'
                    '<createSequence sequenceName="customers_seq"/>'
                    "</changeSet>"
                ),
            ),
        )
    )

    assert schema.complete is True
    assert [table.name for table in schema.tables] == ["customers"]
    assert [column.name for column in schema.tables[0].columns] == ["id", "email"]
    codes = {item.code for item in schema.residue}
    assert codes == {"ignored-changelog-change"}
    ignored = {item.symbol for item in schema.residue}
    assert ignored == {
        "loadData",
        "addForeignKeyConstraint",
        "addPrimaryKey",
        "addNotNullConstraint",
        "dropDefaultValue",
        "createSequence",
    }


def test_an_ignored_change_does_not_mask_a_structural_unknown() -> None:
    schema = replay_liquibase(
        (
            _source(
                "db/changelog/db.changelog.xml",
                _changelog(
                    '<changeSet id="1" author="a">'
                    '<createTable tableName="customers"><column name="id" type="int"/></createTable>'
                    '<loadData tableName="customers" file="data/customers.csv"/>'
                    '<renameColumn tableName="customers" oldColumnName="id" newColumnName="pk"/>'
                    "</changeSet>"
                ),
            ),
        )
    )

    assert schema.complete is False
    codes = {item.code for item in schema.residue}
    assert "unmodelled-changelog-change" in codes
    assert "ignored-changelog-change" in codes


def test_an_unmodelled_change_makes_the_replay_incomplete() -> None:
    schema = replay_liquibase(
        (
            _source(
                "db/changelog/db.changelog.xml",
                _changelog(
                    '<changeSet id="1" author="a">'
                    '<createTable tableName="customers"><column name="id" type="int"/></createTable>'
                    "</changeSet>"
                    '<changeSet id="2" author="a">'
                    '<renameColumn tableName="customers" oldColumnName="id" newColumnName="pk"/>'
                    "</changeSet>"
                ),
            ),
        )
    )

    assert schema.complete is False
    assert "unmodelled-changelog-change" in {item.code for item in schema.residue}


def test_a_non_xml_changelog_format_is_refused_rather_than_guessed() -> None:
    schema = replay_liquibase(
        (_source("db/changelog/db.changelog-master.yaml", "databaseChangeLog: []"),)
    )

    assert schema.complete is False
    assert "unsupported-changelog-format" in {item.code for item in schema.residue}


def test_malformed_xml_is_residue_not_an_exception() -> None:
    schema = replay_liquibase(
        (_source("db/changelog/db.changelog.xml", "<databaseChangeLog><oops>"),)
    )

    assert schema.complete is False
    assert "malformed-changelog" in {item.code for item in schema.residue}
