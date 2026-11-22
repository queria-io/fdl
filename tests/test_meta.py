"""fdl meta: the rows of _fdl, read and written by name and key."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from fdl import catalog, layout, repo, tags
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


def files_json(store: LocalStore) -> dict:
    version = repo.head(store)
    return json.loads(store.get(layout.version_dir(version.id) + version.files.path))


@pytest.fixture
def lake(tmp_path: Path) -> tuple[Path, LocalStore]:
    remote, working = tmp_path / "remote", tmp_path / "work" / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
    write(
        working,
        store,
        "CREATE TABLE lake.main.towns AS SELECT 1 AS code, 2020 AS year, 'a' AS name;"
        "CREATE VIEW lake.main.names AS SELECT name FROM lake.main.towns",
    )
    return working, store


def test_set_get_list_and_unset(lake) -> None:
    working, store = lake
    run("meta", "set", working, ".", "title", "Towns")
    run("meta", "set", working, "main.towns", "description", "Towns\n\nOne row a year.")
    run("meta", "set", working, "main.towns", "primary_key", "code", "year")
    run("meta", "set", working, "main.towns.code", "title", "Area code")
    run("meta", "set", working, "main.towns", "licenses", '[{"id": "CC-BY-4.0", "spdx": "CC-BY-4.0"}]')
    run("meta", "set", working, "main.names", "example-theme", '{"color": "red"}')

    assert run("meta", "get", working, ".", "title") == "Towns\n"
    assert run("meta", "get", working, "main.towns", "primary_key") == '["code", "year"]\n'
    assert run("meta", "list", working, "main.towns.code") == "main.towns.code.title=Area code\n"
    listed = run("meta", "list", working).splitlines()
    assert listed[0] == "title=Towns"
    assert 'main.towns.description="Towns\\n\\nOne row a year."' in listed
    assert 'main.names.example-theme={"color": "red"}' in listed
    assert json.loads(run("meta", "list", working, "main.towns", "--json"))[0]["key"] == "description"

    run("commit", working, "-m", "described", "--author", "tester")
    run("push", working)
    assert files_json(store)["tables"]["main.towns"]["primary_key"] == ["code", "year"]

    run("meta", "unset", working, "main.towns.code", "title")
    assert runner.invoke(app, ["meta", "get", str(working), "main.towns.code", "title"]).exit_code == 1
    assert runner.invoke(app, ["meta", "unset", str(working), "main.towns.code", "title"]).exit_code == 1
    # The row held nothing else, so it is gone.
    assert "main.towns.code" not in run("meta", "list", working)


@pytest.mark.parametrize(
    "args, said",
    [
        (["main.gone", "title", "x"], "main.gone, which is not there"),
        (["main.names", "primary_key", "name"], "gives the view main.names a primary_key"),
        (["main.towns", "primary_key", "nope"], "primary_key names columns it lacks"),
        (["main.towns.code", "asset", "true"], "has to be VARCHAR"),
        (["main.towns", "colour", "red"], "'colour' is not a key of main.towns"),
        (["main.towns", "profile", "maybe"], "profile"),
        (["main.towns", "licenses", "not json"], "as JSON"),
        (["a.b.c.d", "title", "x"], "a.b.c.d, which is not there"),
    ],
)
def test_what_the_checks_refuse_is_not_written(lake, args, said) -> None:
    working, _ = lake
    result = runner.invoke(app, ["meta", "set", str(working), *args])
    assert result.exit_code == 1
    assert said in result.output
    assert run("meta", "list", working) == ""


def _tag(working: Path, name: str, key: str, value) -> None:
    con = sqlite3.connect(working)
    snapshot = con.execute("SELECT max(snapshot_id) FROM ducklake_snapshot").fetchone()[0]
    (table_id,) = con.execute(
        "SELECT table_id FROM ducklake_table WHERE table_name = ? AND end_snapshot IS NULL", [name]
    ).fetchone()
    con.execute("INSERT INTO ducklake_tag VALUES (?, ?, NULL, ?, ?)", [table_id, snapshot, key, json.dumps(value)])
    con.commit()
    con.close()


def test_what_an_earlier_version_said_can_be_changed(lake, tmp_path: Path) -> None:
    working, store = lake
    _tag(working, "towns", tags.PRIMARY_KEY, ["code"])
    run("commit", working, "-m", "tagged", "--author", "tester")
    run("push", working)
    again = tmp_path / "again.sqlite"
    run("clone", tmp_path / "remote", again)

    # Reading shows what the tags said, and leaves the working metadata as it was.
    assert run("meta", "get", again, "main.towns", "primary_key") == '["code"]\n'
    assert "+ table" not in run("status", again)

    run("meta", "unset", again, "main.towns", "primary_key")
    write(again, store, "INSERT INTO lake.main.towns VALUES (2, 2021, 'b')")
    run("commit", again, "-m", "no key", "--author", "tester")
    run("push", again)
    assert "primary_key" not in files_json(store)["tables"]["main.towns"]
