"""dataset.json is checked against the catalog and filled in from it."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from fdl.dataset import DatasetError, describe, gather


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


def test_the_catalog_fills_in_what_it_knows(frozen: Path) -> None:
    declared = {
        "name": "towns",
        "title": "Towns",
        "tables": [{"schema": "main", "name": "towns", "fields": [{"name": "name", "description": "Its name"}]}],
    }
    (table,) = describe(declared, frozen)["tables"]
    assert table["description"] == "Towns"
    assert table["rows"] == 2
    assert table["fields"] == [
        {"description": "Town code", "name": "code", "type": "int32", "nullable": True},
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


def test_keys_outside_the_format_are_kept_with_a_warning(frozen: Path, capsys) -> None:
    described = describe({"name": "t", "title": "T", "title_kana": "ティー"}, frozen)
    assert described["title_kana"] == "ティー"
    assert "title_kana" in capsys.readouterr().err
    with pytest.raises(DatasetError, match="needs a title"):
        describe({"name": "t"}, frozen)
    described = describe({"name": "t", "title": "T", "annotations": {"queria": {"themes": ["x"]}}}, frozen)
    assert described["annotations"] == {"queria": {"themes": ["x"]}}


def test_extensions_is_read_as_annotations(frozen: Path) -> None:
    described = describe(
        {
            "name": "t",
            "title": "T",
            "extensions": {"queria": {"themes": ["x"]}},
            "tables": [
                {
                    "schema": "main",
                    "name": "towns",
                    "extensions": {"queria": {"published": False}},
                    "fields": [{"name": "code", "extensions": {"queria": {"semantic": {}}}}],
                }
            ],
        },
        frozen,
    )
    towns = described["tables"][0]
    assert described["annotations"] == {"queria": {"themes": ["x"]}}
    assert towns["annotations"] == {"queria": {"published": False}}
    assert towns["fields"][0]["annotations"] == {"queria": {"semantic": {}}}
    assert "extensions" not in described and "extensions" not in towns


def test_a_description_can_be_yaml(tmp_path: Path, frozen: Path) -> None:
    from fdl.dataset import find, read

    (tmp_path / "dataset.yml").write_text("name: towns\ntitle: Towns\nissued: 2026-10-30\nlicenses: [CC-BY-4.0]\n")
    path = find(tmp_path)
    assert path.name == "dataset.yml"
    described = describe(read(path), frozen)
    assert described["licenses"] == ["CC-BY-4.0"]
    from fdl.dataset import to_json

    assert b'"issued": "2026-10-30"' in to_json(described)


def test_tables_can_be_described_in_their_own_files(tmp_path: Path, frozen: Path) -> None:
    (tmp_path / "dataset.yml").write_text("name: towns\ntitle: Towns\n")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "towns.table.yml").write_text(
        "schema: main\nname: towns\ntitle: The towns\n"
    )
    (tmp_path / ".fdl").mkdir()
    (tmp_path / ".fdl" / "stale.table.yml").write_text("schema: main\nname: gone\n")
    (table,) = describe(gather(tmp_path / "dataset.yml"), frozen)["tables"]
    assert table["title"] == "The towns"


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
    assert tables["plain"]["description"] == "Just one"


def _views(frozen: Path, *statements: str) -> None:
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{frozen}' AS lake")
    for statement in statements:
        con.execute(statement)
    con.close()


def test_depends_on_is_read_from_the_sql(frozen: Path) -> None:
    _views(
        frozen,
        "CREATE VIEW lake.main.named AS SELECT code FROM lake.main.towns WHERE name IS NOT NULL",
        "CREATE VIEW lake.main.top AS WITH n AS (SELECT * FROM named) SELECT * FROM n JOIN towns USING (code)",
    )
    tables = {t["name"]: t for t in describe({"name": "t", "title": "T"}, frozen)["tables"]}
    assert "depends_on" not in tables["towns"]
    assert tables["named"]["depends_on"] == ["main.towns"]
    assert tables["top"]["depends_on"] == ["main.named", "main.towns"]


def test_other_datasets_are_named_by_their_repository(frozen: Path) -> None:
    declared = {
        "name": "t",
        "title": "T",
        "catalogs": {"census": "https://example.org/census/"},
        "tables": [
            {"schema": "main", "name": "towns", "sql": "SELECT * FROM census.small_area.population JOIN census.areas USING (code) JOIN unknown.main.x USING (code)"}
        ],
    }
    (table,) = describe(declared, frozen)["tables"]
    assert table["depends_on"] == [
        "https://example.org/census/#main.areas",
        "https://example.org/census/#small_area.population",
    ]


def test_the_writer_wins_and_unreadable_sql_is_left_alone(frozen: Path) -> None:
    written = {"schema": "main", "name": "towns", "sql": "SELECT 1", "depends_on": ["main.other"]}
    (table,) = describe({"name": "t", "title": "T", "tables": [written]}, frozen)["tables"]
    assert table["depends_on"] == ["main.other"]
    broken = {"schema": "main", "name": "towns", "sql": "{{ ref('x') }}"}
    (table,) = describe({"name": "t", "title": "T", "tables": [broken]}, frozen)["tables"]
    assert "depends_on" not in table


def test_catalogs_map_aliases_to_repositories(frozen: Path) -> None:
    with pytest.raises(DatasetError, match="catalogs"):
        describe({"name": "t", "title": "T", "catalogs": {"census": "https://example.org/census/#main.x"}}, frozen)


def test_names_are_read_as_duckdb_binds_them(frozen: Path) -> None:
    _views(
        frozen,
        "CREATE SCHEMA lake.s",
        "CREATE TABLE lake.s.towns AS SELECT 1 AS code",
        "USE lake.s",
        "CREATE VIEW lake.s.near AS SELECT * FROM towns "
        "UNION ALL SELECT * FROM (WITH towns AS (SELECT 2 AS code) SELECT * FROM towns)",
    )
    declared = {
        "name": "t",
        "title": "T",
        "catalogs": {"census": "https://example.org/census/"},
        "tables": [
            {
                "schema": "main",
                "name": "towns",
                "sql": "SELECT * FROM Census.main.areas JOIN S.Towns USING (code)",
            }
        ],
    }
    tables = {(t["schema"], t["name"]): t for t in describe(declared, frozen)["tables"]}
    assert tables[("s", "near")]["depends_on"] == ["s.towns"]
    assert tables[("main", "towns")]["depends_on"] == ["https://example.org/census/#main.areas", "s.towns"]
