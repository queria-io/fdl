"""Profiles: a summary of each table, made from the data at push."""

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


def commit(working, *args) -> str:
    return run("commit", working, *args) + run("push", working)


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
    commit(working, "-m", "m", "--author", "t")
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
    commit(working, "-m", "m", "--author", "t")
    code = profile_of(store)["columns"][0]
    assert len(code["values"]) == profiles.TOP_VALUES
    assert code["distinct_count"] > profiles.ALL_VALUES


def test_a_column_at_the_limit_lists_every_value(repository) -> None:
    store, working = repository
    write(working, store, f"CREATE TABLE lake.main.towns AS SELECT 'v' || i AS code FROM range({profiles.ALL_VALUES}) t(i)")
    commit(working, "-m", "m", "--author", "t")
    code = profile_of(store)["columns"][0]
    assert len(code["values"]) == code["distinct_count"] == profiles.ALL_VALUES


@pytest.mark.parametrize(
    ("values", "distinct", "kept"),
    [
        (3, 3, True),
        (profiles.TOP_VALUES, profiles.ALL_VALUES + 1, True),
        # Written under a lower limit: a short list of a column now listed whole.
        (profiles.TOP_VALUES, profiles.ALL_VALUES - 1, False),
        # Written under a higher limit: a whole list longer than now allowed.
        (profiles.ALL_VALUES + 1, profiles.ALL_VALUES + 1, False),
    ],
)
def test_a_profile_written_under_another_limit_is_made_again(values: int, distinct: int, kept: bool) -> None:
    table = {"fields": [{"name": "code", "type": "VARCHAR"}]}
    column = {"name": "code", "type": "VARCHAR", "distinct_count": distinct}
    column["values"] = [{"value": f"v{i}", "count": 1} for i in range(values)]
    previous = json.dumps({"rows": distinct, "columns": [column]}).encode()
    assert profiles.reusable(previous, table) is kept


def test_an_unchanged_table_keeps_its_parents_profile(repository, monkeypatch) -> None:
    store, working = repository
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    write(working, store, "CREATE TABLE lake.main.other AS SELECT 1 AS id")
    commit(working, "-m", "m", "--author", "t")
    write(working, store, "INSERT INTO lake.main.other VALUES (2)")

    built: list[str] = []
    original = profiles.build

    def spy(working, wanted, **kwargs):
        wanted = list(wanted)
        built.extend(t["name"] for t in wanted)
        return original(working, wanted, **kwargs)

    monkeypatch.setattr(profiles, "build", spy)
    commit(working, "-m", "again", "--author", "t")
    assert built == ["other"]
    assert profile_of(store, "towns")["rows"] == 1
    assert profile_of(store, "other")["rows"] == 2


def test_a_parent_without_columns_in_its_files_keeps_its_profile(repository, monkeypatch) -> None:
    store, working = repository
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    write(working, store, "CREATE TABLE lake.main.other AS SELECT 1 AS id")
    commit(working, "-m", "m", "--author", "t")
    parent = repo.head(store)
    key = layout.version_dir(parent.id) + parent.files.path
    files = json.loads(store.get(key))
    for table in files["tables"].values():
        table.pop("columns", None)
    store.put(key, json.dumps(files).encode())
    write(working, store, "INSERT INTO lake.main.other VALUES (2)")

    built: list[str] = []
    original = profiles.build

    def spy(working, wanted, **kwargs):
        wanted = list(wanted)
        built.extend(t["name"] for t in wanted)
        return original(working, wanted, **kwargs)

    monkeypatch.setattr(profiles, "build", spy)
    commit(working, "-m", "again", "--author", "t")
    assert built == ["other"]
    assert profile_of(store, "towns")["rows"] == 1


def test_a_column_dropped_and_added_again_is_profiled_again(repository) -> None:
    store, working = repository
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT i AS id, i AS c FROM range(5) t(i)")
    write(working, store, "CREATE TABLE lake.main.other AS SELECT 1 AS id")
    commit(working, "-m", "m", "--author", "t")
    # The same name and type, and the same data files, but a new column with no values.
    write(working, store, "ALTER TABLE lake.main.towns DROP COLUMN c")
    write(working, store, "ALTER TABLE lake.main.towns ADD COLUMN c BIGINT")
    write(working, store, "INSERT INTO lake.main.other VALUES (2)")
    commit(working, "-m", "again", "--author", "t")
    c = {column["name"]: column for column in profile_of(store)["columns"]}["c"]
    assert c.get("max") is None


def test_expiry_deletes_the_profiles(repository) -> None:
    store, working = repository
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "m", "--author", "t")
    first = repo.head(store).id
    write(working, store, "INSERT INTO lake.main.towns VALUES (2)")
    commit(working, "-m", "again", "--author", "t")
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
    commit(working, "-m", "m", "--author", "t")
    got = profile_of(store)
    assert got["geometry"]["column"] == "geom"
    assert got["geometry"]["count"] == 3
    assert got["geometry"]["bbox"] == [139, 35, 141, 35]
    assert got["geometry"]["types"] == ["POINT"]
    assert len(got["geometry"]["sample"]["features"]) == 3
    assert [r.keys() for r in got["head"]][0] == {"id"}


def test_profile_adds_the_profiles_a_pushed_version_lacks(repository) -> None:
    store, working = repository
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT i AS id FROM range(4) t(i)")
    write(working, store, "CREATE TABLE lake.main.other AS SELECT 1 AS id")
    commit(working, "-m", "m", "--author", "t")
    head = repo.head(store)
    kept = store.get(f"{layout.version_dir(head.id)}profiles/main/other.json")
    # A version made before its writer profiled: no profile for towns.
    (Path(store.url) / f"{layout.version_dir(head.id)}profiles/main/towns.json").unlink()

    output = run("profile", store.url)
    assert "put profiles/main/towns.json" in output
    assert "Profiled 1 table(s)" in output
    assert profile_of(store)["rows"] == 4
    assert store.get(f"{layout.version_dir(head.id)}profiles/main/other.json") == kept
    assert "Profiled 0 table(s)" in run("profile", store.url)


def test_push_no_profile_uploads_none(repository) -> None:
    store, working = repository
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    run("commit", working, "-m", "m", "--author", "t")
    run("push", working, "--no-profile")
    assert store.list(f"{layout.version_dir(repo.head(store).id)}profiles/") == []
    assert "Profiled 1 table(s)" in run("profile", store.url)
    assert profile_of(store)["rows"] == 1


def test_push_skips_profiles_when_the_working_catalog_changed_after_the_commit(repository) -> None:
    store, working = repository
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    run("commit", working, "-m", "m", "--author", "t")
    write(working, store, "INSERT INTO lake.main.towns VALUES (2)")
    result = runner.invoke(app, ["push", str(working)])
    assert result.exit_code == 0, result.output
    assert "changed after the commit" in result.output
    assert store.list(f"{layout.version_dir(repo.head(store).id)}profiles/") == []


def test_push_goes_without_profiles_when_the_working_catalog_is_gone(repository) -> None:
    store, working = repository
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    run("commit", working, "-m", "m", "--author", "t")
    working.unlink()
    result = runner.invoke(app, ["push", str(working)])
    assert result.exit_code == 0, result.output
    assert "were not profiled" in result.output
    assert store.list(f"{layout.version_dir(repo.head(store).id)}profiles/") == []


def test_push_profiles_the_tables_a_stopped_push_left_without(repository) -> None:
    store, working = repository
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    write(working, store, "CREATE TABLE lake.main.roads AS SELECT 1 AS id")
    run("commit", working, "-m", "m", "--author", "t")
    pending = next((working.parent / f"{working.name}.pending").iterdir())
    partial = pending / "profiles" / "main" / "towns.json"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b'{"rows": 1, "columns": []}')
    run("push", working)
    assert profile_of(store, "roads")["rows"] == 1


def test_a_view_is_profiled(repository) -> None:
    store, working = repository
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT i AS id FROM range(5) t(i)")
    write(working, store, "CREATE VIEW lake.main.big AS SELECT id FROM lake.main.towns WHERE id >= 2")
    commit(working, "-m", "m", "--author", "t")
    got = profile_of(store, "big")
    assert got["rows"] == 3
    assert got["columns"][0]["min"] == 2


def _spy_builds(monkeypatch) -> list[str]:
    built: list[str] = []
    original = profiles.build

    def spy(working, wanted, **kwargs):
        wanted = list(wanted)
        built.extend(t["name"] for t in wanted)
        return original(working, wanted, **kwargs)

    monkeypatch.setattr(profiles, "build", spy)
    return built


def test_a_view_keeps_its_profile_only_while_no_table_changes(repository, monkeypatch) -> None:
    store, working = repository
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    write(working, store, "CREATE VIEW lake.main.big AS SELECT id FROM lake.main.towns")
    commit(working, "-m", "m", "--author", "t")
    built = _spy_builds(monkeypatch)

    run("commit", working, "-m", "same", "--author", "t", "--allow-empty")
    run("push", working)
    assert built == []

    write(working, store, "INSERT INTO lake.main.towns VALUES (2)")
    commit(working, "-m", "again", "--author", "t")
    assert sorted(built) == ["big", "towns"]
    assert profile_of(store, "big")["rows"] == 2


def test_a_wide_table_is_measured_a_few_columns_at_a_time(repository, monkeypatch) -> None:
    store, working = repository
    monkeypatch.setattr(profiles, "COLUMNS_PER_QUERY", 2)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT i AS a, i + 1 AS b, i + 2 AS c FROM range(3) t(i)")
    commit(working, "-m", "m", "--author", "t")
    assert [(c["name"], c["min"], c["max"]) for c in profile_of(store)["columns"]] == [
        ("a", 0, 2),
        ("b", 1, 3),
        ("c", 2, 4),
    ]
