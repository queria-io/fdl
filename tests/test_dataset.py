"""dataset.json is checked against the catalog and filled in from it."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from fdl.dataset import DatasetError, describe, find, read


@pytest.fixture
def frozen(tmp_path: Path) -> Path:
    path = tmp_path / "lake.duckdb"
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{path}' AS lake (DATA_PATH '{tmp_path}/data/', DATA_INLINING_ROW_LIMIT 0)")
    con.execute("CREATE TABLE lake.main.towns AS SELECT * FROM (VALUES (1, 'a'), (2, NULL)) t(code, name)")
    con.execute("COMMENT ON TABLE lake.main.towns IS 'Towns'")
    con.execute("COMMENT ON COLUMN lake.main.towns.code IS 'Town code'")
    con.close()
    return path


def test_the_catalog_fills_in_what_it_knows_but_not_its_comments(frozen: Path) -> None:
    declared = {
        "name": "towns",
        "title": "Towns",
        "tables": [{"schema": "main", "name": "towns", "fields": [{"name": "name", "description": "Its name"}]}],
    }
    (table,) = describe(declared, frozen)["tables"]
    assert "description" not in table
    assert table["rows"] == 2
    assert table["fields"] == [
        {"name": "code", "type": "int32", "nullable": True},
        {"description": "Its name", "name": "name", "type": "varchar", "nullable": True},
    ]


def test_what_the_catalog_lacks_is_refused(frozen: Path) -> None:
    with pytest.raises(DatasetError, match="not in the catalog"):
        describe({"name": "t", "title": "T", "tables": [{"schema": "main", "name": "roads"}]}, frozen)
    with pytest.raises(DatasetError, match="no column"):
        describe(
            {"name": "t", "title": "T", "tables": [{"schema": "main", "name": "towns", "fields": [{"name": "x"}]}]},
            frozen,
        )


@pytest.mark.parametrize(
    ("key", "error"),
    [("code", "list of column names"), (["code", "code"], "more than once"), (["code", "x"], "lacks: x")],
)
def test_a_primary_key_names_columns_of_the_table_once(frozen: Path, key, error: str) -> None:
    with pytest.raises(DatasetError, match=error):
        describe({"name": "t", "tables": [{"schema": "main", "name": "towns", "primary_key": key}]}, frozen)


def test_keys_outside_the_format_are_kept_as_written(frozen: Path, capsys) -> None:
    described = describe(
        {"name": "t", "title_kana": "ティー", "tables": [{"schema": "main", "name": "towns", "themes": ["x"]}]},
        frozen,
    )
    assert described["title_kana"] == "ティー"
    assert described["tables"][0]["themes"] == ["x"]
    assert capsys.readouterr().err == ""
    with pytest.raises(DatasetError, match="needs a name"):
        describe({"title": "T"}, frozen)
    described = describe({"name": "t", "title": "T", "annotations": {"queria": {"themes": ["x"]}}}, frozen)
    assert described["annotations"] == {"queria": {"themes": ["x"]}}


def test_only_dataset_json_is_read(tmp_path: Path) -> None:
    (tmp_path / "dataset.yml").write_text("name: towns\n")
    with pytest.raises(DatasetError, match="no dataset.json"):
        find(tmp_path)
    (tmp_path / "dataset.json").write_text('{"name": "towns", "issued": "2026-10-30"}')
    assert read(find(tmp_path)) == {"name": "towns", "issued": "2026-10-30"}


@pytest.mark.parametrize(
    ("sql", "rows"),
    [
        ("UPDATE lake.main.towns SET name = 'b'", 2),
        ("DELETE FROM lake.main.towns WHERE code = 1", 1),
        ("INSERT INTO lake.main.towns VALUES (3, 'c')", 3),
    ],
)
def test_rows_are_what_a_reader_counts(frozen: Path, sql: str, rows: int) -> None:
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{frozen}' AS lake (DATA_PATH '{frozen.parent}/data/', DATA_INLINING_ROW_LIMIT 0)")
    con.execute(sql)
    assert con.execute("SELECT count(*) FROM lake.main.towns").fetchone() == (rows,)
    con.close()

    (table,) = describe({"name": "towns", "title": "Towns"}, frozen)["tables"]
    assert table["rows"] == rows


def test_views_are_listed_with_their_sql(frozen: Path) -> None:
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{frozen}' AS lake")
    con.execute("CREATE VIEW lake.main.named AS SELECT code FROM lake.main.towns WHERE name IS NOT NULL")
    con.execute("CREATE VIEW lake.main.plain AS SELECT 1 AS one")
    con.execute("COMMENT ON VIEW lake.main.plain IS 'Just one'")
    con.close()

    declared = {
        "name": "t",
        "title": "T",
        "tables": [{"schema": "main", "name": "named", "title": "Named towns", "fields": [{"name": "code", "title": "Code"}]}],
    }
    tables = {t["name"]: t for t in describe(declared, frozen)["tables"]}
    assert "kind" not in tables["towns"]
    named = tables["named"]
    assert named["kind"] == "view" and named["title"] == "Named towns"
    assert "WHERE" in named["sql"] and "rows" not in named
    assert named["fields"] == [{"name": "code", "title": "Code"}]
    assert tables["plain"]["kind"] == "view"
    assert "description" not in tables["plain"]


def test_depends_on_and_catalogs_are_kept_as_written(frozen: Path) -> None:
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{frozen}' AS lake")
    con.execute("CREATE VIEW lake.main.named AS SELECT code FROM lake.main.towns")
    con.close()
    declared = {
        "name": "t",
        "catalogs": {"census": "https://example.org/census/#not-checked"},
        "tables": [{"schema": "main", "name": "towns", "sql": "SELECT * FROM census.main.areas"}],
    }
    described = describe(declared, frozen)
    tables = {t["name"]: t for t in described["tables"]}
    assert described["catalogs"] == declared["catalogs"]
    assert "depends_on" not in tables["towns"] and "depends_on" not in tables["named"]
