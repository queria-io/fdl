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


def commit(working, *args) -> str:
    """Commit and push, as one step used to."""
    return run("commit", working, *args) + run("push", working)


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
    first = commit(working, "-m", "first", "--author", "tester")
    write(working, store, "INSERT INTO lake.main.towns VALUES (2)")
    commit(working, "-m", "second", "--author", "tester")

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
    commit(one, "-m", "from one", "--author", "a")

    run("checkout", remote, two)
    write(two, store, "INSERT INTO lake.main.towns VALUES (2)")
    commit(two, "-m", "from two", "--author", "b")

    # The first working catalog is now behind the branch.
    write(one, store, "INSERT INTO lake.main.towns VALUES (3)")
    run("commit", one, "-m", "stale", "--author", "a")
    result = runner.invoke(app, ["push", str(one)])
    assert result.exit_code == 1 and "Pull and build again" in result.output
    assert repo.head(store).message == "from two"
    assert remote.joinpath(layout.HEAD).read_text() == "ref: refs/heads/main\n"


def test_a_rebuild_with_the_same_rows_makes_no_version(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT * FROM (VALUES (1), (2), (2)) v(id)")
    commit(working, "-m", "first", "--author", "t")
    first = repo.head(store)

    assert "Nothing to commit" in commit(working, "-m", "again", "--author", "t")
    # Written again to new files, in another order: still the same rows.
    write(working, store, "CREATE OR REPLACE TABLE lake.main.towns AS SELECT * FROM (VALUES (2), (1), (2)) v(id)")
    assert "Nothing to commit" in commit(working, "-m", "rebuilt", "--author", "t")
    assert repo.head(store).id == first.id

    commit(working, "-m", "forced", "--author", "t", "--allow-empty")
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
    commit(working, "-m", "first", "--author", "t")
    write(working, store, sql)
    assert "version 2" in commit(working, "-m", "changed", "--author", "t")


def test_a_new_description_makes_a_version(tmp_path: Path, described: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "t")
    described.write_text('{"name": "towns", "title": "Towns of the city"}')
    assert "version 2" in commit(working, "-m", "retitled", "--author", "t")


def test_rows_it_cannot_read_are_taken_as_changed(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "t")
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
    commit(working, "-m", "first", "--author", "t")
    write(
        working,
        store,
        "CREATE OR REPLACE TABLE lake.main.towns AS SELECT * FROM (VALUES (1, []::INTEGER[]), (2, NULL::INTEGER[])) v(id, tags)",
    )
    assert "version 2" in commit(working, "-m", "swapped", "--author", "t")


def test_tags(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "work" / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "tester")
    version = repo.head(store)

    assert f"refs/tags/v1 -> {version.id}" in run("tag", remote, "v1")
    assert run("tag", remote, "v2", version.id).strip().endswith(version.id)
    assert run("tag", remote).splitlines() == [f"v1  {version.id}", f"v2  {version.id}"]
    run("tag", remote, "v1", "-d")
    assert repo.tags(store) == {"v2": version.id}


def test_withdraw_says_what_it_would_do_until_told_yes(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "work" / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "leak", "--author", "tester")
    leak = repo.head(store)
    write(working, store, "DROP TABLE lake.main.towns; CREATE TABLE lake.main.ok AS SELECT 1 AS id")
    commit(working, "-m", "clean", "--author", "tester")

    assert "Nothing was changed" in run("withdraw", remote, leak.id)
    assert repo.is_expired(store, leak.id) is False
    assert "Withdrew 1 version(s) and deleted 1 file(s)" in run("withdraw", remote, leak.id, "--yes")
    assert repo.is_expired(store, leak.id)


def test_status_diff_and_show(tmp_path: Path, described: Path) -> None:
    import json

    remote, working = tmp_path / "remote", tmp_path / "work" / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id, 'a' AS name")
    assert "+ table main.towns" in run("status", working)
    commit(working, "-m", "first", "--author", "tester")
    assert "Nothing to commit" in run("status", working)

    write(
        working,
        store,
        "INSERT INTO lake.main.towns VALUES (2, 'b'); ALTER TABLE lake.main.towns RENAME COLUMN name TO town",
    )
    described.write_text('{"name": "towns", "title": "Towns and their codes"}')
    status = run("status", working)
    assert "~ rows main.towns: 1 -> 2" in status
    assert "~ column main.towns.name -> town  (breaking)" in status
    commit(working, "-m", "second", "--author", "tester")

    lines = run("diff", remote).splitlines()
    assert lines == [
        "~ rows main.towns: 1 -> 2",
        "~ column main.towns.name -> town  (breaking)",
        "~ data main.towns",
        "~ description dataset",
    ]
    first = list(repo.log(store))[-1]
    assert run("diff", remote, "--against", first.id).splitlines() == lines
    as_json = json.loads(run("diff", remote, "--json"))
    assert as_json["columns"] == [
        {"table": "main.towns", "kind": "rename_column", "column": "town", "from": "name", "breaking": True}
    ]

    shown = run("show", remote)
    assert "version 2" in shown and "second" in shown and "main.towns  2 rows" in shown
    assert "+ table main.towns" in run("show", remote, first.id)


def test_url_prints_the_attach_statement(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "work" / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "tester")
    statement = run("url", remote, "--attach", "--alias", "towns").strip()
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(statement)
    assert con.execute("SELECT count(*) FROM towns.main.towns").fetchone() == (1,)


def rows(store: LocalStore, sql: str) -> list[tuple]:
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{store.root / layout.version_catalog(repo.head(store).id)}' AS v (READ_ONLY)")
    found = con.execute(sql).fetchall()
    con.close()
    return found


def test_revert_moves_the_branch_forward_to_what_a_version_held(tmp_path: Path, described: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT * FROM (VALUES (1), (2)) v(id)")
    write(working, store, "CREATE TABLE lake.main.kept AS SELECT 1 AS id")
    write(working, store, "CREATE VIEW lake.main.ids AS SELECT id FROM lake.main.towns")
    commit(working, "-m", "good", "--author", "t")
    good = repo.head(store)

    described.write_text('{"name": "towns", "title": "Wrong"}')
    write(working, store, "CREATE OR REPLACE TABLE lake.main.towns AS SELECT 9 AS id")
    write(working, store, "DROP VIEW lake.main.ids")
    write(working, store, "CREATE TABLE lake.main.extra AS SELECT 1 AS id")
    commit(working, "-m", "bad", "--author", "t")
    bad = repo.head(store)

    out = run("revert", good.id, working, "--author", "t")
    assert "holding what version 1 held" in out
    # Committed, not pushed: the branch has not moved yet.
    assert repo.head(store).id == bad.id
    assert "Committed, not pushed" in run("status", working)
    run("push", working)
    reverted = repo.head(store)
    assert reverted.seq == 3 and reverted.parents == (bad.id,)
    assert reverted.message == "Revert to version 1"
    assert rows(store, "SELECT id FROM v.main.ids ORDER BY id") == [(1,), (2,)]
    assert rows(store, "SELECT count(*) FROM v.main.kept") == [(1,)]
    assert rows(
        store, "SELECT count(*) FROM duckdb_tables() WHERE database_name = 'v' AND table_name = 'extra'"
    ) == [(0,)]
    assert reverted.dataset.sha256 == good.dataset.sha256
    # The version in between is still in the history.
    assert [v.message for v in repo.log(store)] == ["Revert to version 1", "bad", "good"]

    assert "Nothing to revert" in run("revert", good.id, working, "--author", "t")
    assert "Nothing to push" in run("push", working)
    assert repo.head(store).id == reverted.id


def test_revert_refuses_a_version_outside_the_branch(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "t")
    first = repo.head(store)
    run("tag", remote, "v1", first.id)

    result = runner.invoke(app, ["revert", "0" * 8, str(working), "--author", "t"])
    assert result.exit_code == 1
    assert "Nothing to revert" in run("revert", "refs/tags/v1", working, "--author", "t")


def test_a_commit_waits_until_it_is_pushed(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("checkout", remote, working)
    assert "Nothing to push" in run("push", working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    run("commit", working, "-m", "first try", "--author", "t")
    assert repo.head(store) is None

    # Committing again replaces the version not pushed yet.
    write(working, store, "INSERT INTO lake.main.towns VALUES (2)")
    run("commit", working, "-m", "second try", "--author", "t")
    assert len(list((tmp_path / "lake.sqlite.pending").iterdir())) == 1
    assert "version 1" in run("push", working)
    assert [v.message for v in repo.log(store)] == ["second try"]
    assert not (tmp_path / "lake.sqlite.pending").exists()
    assert "Committed, not pushed" not in run("status", working)
