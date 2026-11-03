"""checkout, write, commit and read back through the command."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from fdl import catalog, layout, repo
from fdl.cli import app
from fdl.stores import LocalStore

runner = CliRunner()


@pytest.fixture(autouse=True)
def described(tmp_path: Path, monkeypatch) -> Path:
    """Every commit reads `dataset.json` from where it runs."""
    path = tmp_path / "dataset.json"
    path.write_text('{"name": "towns", "title": "Towns"}')
    monkeypatch.chdir(tmp_path)
    return path


def run(*args: str) -> str:
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


def test_the_round_trip(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "work" / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    first = run("commit", working, "-m", "first", "--author", "tester")
    write(working, store, "INSERT INTO lake.main.towns VALUES (2)")
    run("commit", working, "-m", "second", "--author", "tester")

    assert "version 1" in first
    assert [v.message for v in repo.log(store)] == ["second", "first"]
    url = run("url", remote).strip()
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{url}' AS v (READ_ONLY)")
    assert con.execute("SELECT count(*) FROM v.main.towns").fetchone() == (2,)

    assert "Expired 1" in run("expire", remote)
    assert "(expired)" in run("log", remote)


def test_a_second_checkout_starts_from_the_branch(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    store = LocalStore(remote)
    run("init", remote)
    one, two = tmp_path / "one.sqlite", tmp_path / "two.sqlite"
    run("checkout", remote, one)
    write(one, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    run("commit", one, "-m", "from one", "--author", "a")

    run("checkout", remote, two)
    write(two, store, "INSERT INTO lake.main.towns VALUES (2)")
    run("commit", two, "-m", "from two", "--author", "b")

    # The first working catalog is now behind the branch.
    write(one, store, "INSERT INTO lake.main.towns VALUES (3)")
    result = runner.invoke(app, ["commit", str(one), "-m", "stale", "--author", "a"])
    assert result.exit_code == 1 and "Pull and build again" in result.output
    assert repo.head(store).message == "from two"
    assert remote.joinpath(layout.HEAD).read_text() == "ref: refs/heads/main\n"


def test_a_rebuild_with_the_same_rows_makes_no_version(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT * FROM (VALUES (1), (2), (2)) v(id)")
    run("commit", working, "-m", "first", "--author", "t")
    first = repo.head(store)

    assert "Nothing to commit" in run("commit", working, "-m", "again", "--author", "t")
    # Written again to new files, in another order: still the same rows.
    write(working, store, "CREATE OR REPLACE TABLE lake.main.towns AS SELECT * FROM (VALUES (2), (1), (2)) v(id)")
    assert "Nothing to commit" in run("commit", working, "-m", "rebuilt", "--author", "t")
    assert repo.head(store).id == first.id

    run("commit", working, "-m", "forced", "--author", "t", "--allow-empty")
    assert repo.head(store).message == "forced"


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE OR REPLACE TABLE lake.main.towns AS SELECT * FROM (VALUES (1), (2)) v(id)",
        "CREATE OR REPLACE TABLE lake.main.towns AS SELECT id::BIGINT AS id FROM (VALUES (1), (2), (2)) v(id)",
        "CREATE TABLE lake.main.more AS SELECT 1 AS id",
        "CREATE VIEW lake.main.ids AS SELECT id FROM lake.main.towns",
    ],
)
def test_a_change_makes_a_version(tmp_path: Path, sql: str) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT id::INTEGER AS id FROM (VALUES (1), (2), (2)) v(id)")
    run("commit", working, "-m", "first", "--author", "t")
    write(working, store, sql)
    assert "version 2" in run("commit", working, "-m", "changed", "--author", "t")


def test_a_new_description_makes_a_version(tmp_path: Path, described: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    run("commit", working, "-m", "first", "--author", "t")
    described.write_text('{"name": "towns", "title": "Towns of the city"}')
    assert "version 2" in run("commit", working, "-m", "retitled", "--author", "t")


def test_rows_it_cannot_read_are_taken_as_changed(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    run("commit", working, "-m", "first", "--author", "t")
    write(working, store, "CREATE OR REPLACE TABLE lake.main.towns AS SELECT 1 AS id")
    for parquet in (remote / layout.DEFAULT_DATA_DIR).rglob("*.parquet"):
        parquet.unlink()
        break

    result = runner.invoke(app, ["commit", str(working), "-m", "again", "--author", "t"])
    assert "could not tell whether anything changed" in result.output


def test_values_that_hash_alike_still_count_as_changed(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    # DuckDB's hash() gives NULL and [] the same hash, so swapping them
    # between rows leaves a sum of row hashes (and the row and null counts) as
    # they were.
    write(
        working,
        store,
        "CREATE TABLE lake.main.towns AS SELECT * FROM (VALUES (1, NULL::INTEGER[]), (2, []::INTEGER[])) v(id, tags)",
    )
    run("commit", working, "-m", "first", "--author", "t")
    write(
        working,
        store,
        "CREATE OR REPLACE TABLE lake.main.towns AS SELECT * FROM (VALUES (1, []::INTEGER[]), (2, NULL::INTEGER[])) v(id, tags)",
    )
    assert "version 2" in run("commit", working, "-m", "swapped", "--author", "t")


def test_tags(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "work" / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    run("commit", working, "-m", "first", "--author", "tester")
    version = repo.head(store)

    assert f"refs/tags/v1 -> {version.id}" in run("tag", remote, "v1")
    assert run("tag", remote, "v2", version.id).strip().endswith(version.id)
    assert run("tag", remote).splitlines() == [f"v1  {version.id}", f"v2  {version.id}"]
    run("tag", remote, "v1", "-d")
    assert repo.tags(store) == {"v2": version.id}
