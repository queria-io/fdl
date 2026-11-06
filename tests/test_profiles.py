"""Profiles: a summary of each table, made from the data at commit."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from fdl import catalog, layout, profiles, repo
from fdl.cli import app
from fdl.stores import LocalStore

runner = CliRunner()


def run(*args) -> str:
    result = runner.invoke(app, [str(a) for a in args])
    assert result.exit_code == 0, result.output
    return result.output


def write(working: Path, store: LocalStore, sql: str) -> None:
    con = duckdb.connect()
    for statement in catalog.attach_sql(working, alias="lake", data_path=repo.data_url(store)):
        if statement:
            con.execute(statement)
    con.execute(sql)
    con.close()


@pytest.fixture
def repository(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    run("init", remote)
    run("checkout", remote, working)
    (tmp_path / "dataset.yml").write_text("name: towns\ntitle: Towns\n")
    return LocalStore(remote), working


def profile_of(store: LocalStore, table: str = "towns") -> dict:
    head = repo.head(store)
    return json.loads(store.get(f"{layout.version_dir(head.id)}profiles/main/{table}.json"))


def test_a_commit_profiles_each_table(repository) -> None:
    store, working = repository
    write(
        working,
        store,
        "CREATE TABLE lake.main.towns AS SELECT i AS id, 'town ' || (i % 3) AS name, "
        "DATE '2020-01-01' + i::INTEGER AS day, i % 2 = 0 AS even FROM range(20) t(i)",
    )
    run("commit", working, "-m", "m", "--author", "t")
    got = profile_of(store)

    assert got["rows"] == 20
    assert len(got["head"]) == profiles.HEAD_ROWS
    by_name = {c["name"]: c for c in got["columns"]}
    assert by_name["id"]["min"] == 0 and by_name["id"]["max"] == 19
    assert by_name["name"]["distinct_count"] == 3
    assert {v["value"]: v["count"] for v in by_name["name"]["values"]} == {"town 0": 7, "town 1": 7, "town 2": 6}
    assert by_name["day"]["min"] == "2020-01-01"
    assert by_name["even"]["true_count"] == 10


def test_many_values_list_only_the_most_common(repository) -> None:
    store, working = repository
    write(working, store, f"CREATE TABLE lake.main.towns AS SELECT 'v' || i AS code FROM range({profiles.ALL_VALUES + 5}) t(i)")
    run("commit", working, "-m", "m", "--author", "t")
    code = profile_of(store)["columns"][0]
    assert len(code["values"]) == profiles.TOP_VALUES
    assert code["distinct_count"] > profiles.ALL_VALUES


def test_an_unchanged_table_keeps_its_parents_profile(repository, monkeypatch) -> None:
    store, working = repository
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    write(working, store, "CREATE TABLE lake.main.other AS SELECT 1 AS id")
    run("commit", working, "-m", "m", "--author", "t")
    write(working, store, "INSERT INTO lake.main.other VALUES (2)")

    built: list[str] = []
    original = profiles.build

    def spy(working, wanted, **kwargs):
        wanted = list(wanted)
        built.extend(t["name"] for t in wanted)
        return original(working, wanted, **kwargs)

    monkeypatch.setattr(profiles, "build", spy)
    run("commit", working, "-m", "again", "--author", "t")
    assert built == ["other"]
    assert profile_of(store, "towns")["rows"] == 1
    assert profile_of(store, "other")["rows"] == 2


def test_expiry_deletes_the_profiles(repository) -> None:
    store, working = repository
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    run("commit", working, "-m", "m", "--author", "t")
    first = repo.head(store).id
    write(working, store, "INSERT INTO lake.main.towns VALUES (2)")
    run("commit", working, "-m", "again", "--author", "t")
    repo.expire(store, first)
    assert store.list(f"{layout.version_dir(first)}profiles/") == []


def test_a_geometry_column_gets_its_extent_and_a_sample(repository) -> None:
    store, working = repository
    try:
        duckdb.connect().execute("INSTALL spatial; LOAD spatial")
    except duckdb.Error:
        pytest.skip("no spatial extension")
    write(
        working,
        store,
        "INSTALL spatial; LOAD spatial; CREATE TABLE lake.main.towns AS "
        "SELECT i AS id, ST_Point(139 + i, 35) AS geom FROM range(3) t(i)",
    )
    run("commit", working, "-m", "m", "--author", "t")
    got = profile_of(store)
    assert got["geometry"]["column"] == "geom"
    assert got["geometry"]["count"] == 3
    assert got["geometry"]["bbox"] == [139, 35, 141, 35]
    assert got["geometry"]["types"] == ["POINT"]
    assert len(got["geometry"]["sample"]["features"]) == 3
    assert [r.keys() for r in got["head"]][0] == {"id"}
