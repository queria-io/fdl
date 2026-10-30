"""checkout, write, commit and read back through the command."""

from __future__ import annotations

from pathlib import Path

import duckdb
from typer.testing import CliRunner

from fdl import catalog, layout, repo
from fdl.cli import app
from fdl.stores import LocalStore

runner = CliRunner()


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
