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
    con.execute(f"ATTACH 'ducklake:{path}' AS lake (DATA_PATH '{tmp_path}/data/')")
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


def test_keys_outside_the_format_go_under_extensions(frozen: Path) -> None:
    with pytest.raises(DatasetError, match="title_kana"):
        describe({"name": "t", "title": "T", "title_kana": "ティー"}, frozen)
    with pytest.raises(DatasetError, match="needs a title"):
        describe({"name": "t"}, frozen)
    described = describe({"name": "t", "title": "T", "extensions": {"queria": {"themes": ["x"]}}}, frozen)
    assert described["extensions"] == {"queria": {"themes": ["x"]}}


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
