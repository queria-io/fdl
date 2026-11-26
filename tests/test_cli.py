"""clone, write, commit and read back through the command."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from fdl import catalog, layout, repo
from fdl.cli import app
from fdl.stores import LocalStore

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

runner = CliRunner()


@pytest.fixture(autouse=True)
def elsewhere(tmp_path: Path, monkeypatch) -> Path:
    """Commands run from a folder of their own, which they leave as it is."""
    path = tmp_path / "about"
    path.mkdir()
    monkeypatch.chdir(path)
    return path


#: A title for the dataset, in a table of its own.
TITLED = "CREATE OR REPLACE TABLE lake.main.about AS SELECT {!r} AS title"


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
    run("clone", remote, working)
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

    assert "Expired 1" in run("expire", remote, "--grace", "0m")
    assert "(expired)" in run("log", remote)


def test_a_second_clone_starts_from_the_branch(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    store = LocalStore(remote)
    run("init", remote)
    one, two = tmp_path / "one.sqlite", tmp_path / "two.sqlite"
    run("clone", remote, one)
    write(one, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(one, "-m", "from one", "--author", "a")

    run("clone", remote, two)
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
    run("clone", remote, working)
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
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT id::INTEGER AS id FROM (VALUES (1), (2), (2)) v(id)")
    commit(working, "-m", "first", "--author", "t")
    write(working, store, sql)
    assert "version 2" in commit(working, "-m", "changed", "--author", "t")


def test_a_new_description_makes_a_version(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    write(working, store, TITLED.format("Towns"))
    commit(working, "-m", "first", "--author", "t")
    write(working, store, TITLED.format("Towns"))
    assert "Nothing to commit" in run("commit", working, "-m", "same", "--author", "t")
    write(working, store, TITLED.format("Towns of the city"))
    assert "version 2" in commit(working, "-m", "retitled", "--author", "t")


def test_rows_it_cannot_read_are_taken_as_changed(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
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
    run("clone", remote, working)
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
    run("clone", remote, working)
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
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "leak", "--author", "tester")
    leak = repo.head(store)
    # The clean version's catalog must not keep the snapshot that held the data:
    # a catalog reaches every file it names, at any snapshot.
    write(
        working,
        store,
        "DROP TABLE lake.main.towns; CREATE TABLE lake.main.ok AS SELECT 1 AS id; "
        "CALL ducklake_expire_snapshots('lake', older_than => now())",
    )
    commit(working, "-m", "clean", "--author", "tester")

    assert "Nothing was changed" in run("withdraw", remote, leak.id)
    assert repo.is_expired(store, leak.id) is False
    assert "Withdrew 1 version(s) and deleted 1 file(s)" in run("withdraw", remote, leak.id, "--yes")
    assert repo.is_expired(store, leak.id)


def test_withdraw_applies_the_objects_another_dataset_withdrew(tmp_path: Path) -> None:
    import json

    remote, working = tmp_path / "remote", tmp_path / "work" / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "leak", "--author", "tester")
    leak = repo.head(store)
    names = [key.removeprefix("tables/") for key in store.list("tables/")]
    mark = tmp_path / "expired.json"
    mark.write_text(json.dumps({"reason": "withdrawn", "deleted": {"data": names, "assets": []}}))

    plan = json.loads(run("withdraw", remote, "--objects", mark, "--json"))
    assert plan["versions"] == [leak.id] and plan["blocked"] == ["refs/heads/main"]
    assert repo.is_expired(store, leak.id) is False


def test_withdraw_refuses_an_objects_file_that_names_none(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    run("init", remote)
    for name, text in [("broken.json", "{"), ("retention.json", '{"reason": "retention"}'), ("numbers.json", '{"data": [1]}')]:
        (tmp_path / name).write_text(text)
        result = runner.invoke(app, ["withdraw", str(remote), "--objects", str(tmp_path / name)])
        # A clean error, not a traceback
        assert result.exit_code != 0 and isinstance(result.exception, SystemExit), result.output


def test_status_diff_and_show(tmp_path: Path) -> None:
    import json

    remote, working = tmp_path / "remote", tmp_path / "work" / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id, 'a' AS name")
    assert "+ table main.towns" in run("status", working)
    commit(working, "-m", "first", "--author", "tester")
    assert "Nothing to commit" in run("status", working)

    write(
        working,
        store,
        "INSERT INTO lake.main.towns VALUES (2, 'b'); ALTER TABLE lake.main.towns RENAME COLUMN name TO town",
    )
    write(working, store, TITLED.format("Towns and their codes"))
    status = run("status", working)
    assert "~ rows main.towns: 1 -> 2" in status
    assert "~ column main.towns.name -> town  (breaking)" in status
    commit(working, "-m", "second", "--author", "tester")

    lines = run("diff", remote).splitlines()
    assert lines == [
        "+ table main.about",
        "~ rows main.towns: 1 -> 2",
        "~ column main.towns.name -> town  (breaking)",
        "~ data main.towns",
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
    run("clone", remote, working)
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
    con.execute(f"ATTACH 'ducklake:{store.root / layout.version_metadata(repo.head(store))}' AS v (READ_ONLY)")
    found = con.execute(sql).fetchall()
    con.close()
    return found


def test_revert_moves_the_branch_forward_to_what_a_version_held(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT * FROM (VALUES (1), (2)) v(id)")
    write(working, store, "CREATE TABLE lake.main.kept AS SELECT 1 AS id")
    write(working, store, "CREATE VIEW lake.main.ids AS SELECT id FROM lake.main.towns")
    commit(working, "-m", "good", "--author", "t")
    good = repo.head(store)

    write(working, store, TITLED.format("Wrong"))
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
    assert rows(
        store, "SELECT count(*) FROM duckdb_tables() WHERE database_name = 'v' AND table_name = 'about'"
    ) == [(0,)]
    # The version in between is still in the history.
    assert [v.message for v in repo.log(store)] == ["Revert to version 1", "bad", "good"]

    assert "Nothing to revert" in run("revert", good.id, working, "--author", "t")
    assert "Nothing to push" in run("push", working)
    assert repo.head(store).id == reverted.id


def test_restore_leaves_a_table_unchanged_since_as_it_is(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.a AS SELECT 1 AS id; CREATE TABLE lake.main.b AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "t")
    first = repo.head(store)
    write(working, store, "INSERT INTO lake.main.b VALUES (2)")
    commit(working, "-m", "second", "--author", "t")

    changed = catalog.restore(
        working, remote / layout.version_metadata(first), data_path=repo.data_url(store), dataset_data=repo.data_url(store)
    )
    assert changed == ["main.b"]


def test_restore_brings_back_a_column_that_allowed_nulls(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.a (id INTEGER); INSERT INTO lake.main.a VALUES (1)")
    commit(working, "-m", "first", "--author", "t")
    first = repo.head(store)
    write(working, store, "ALTER TABLE lake.main.a ALTER COLUMN id SET NOT NULL")
    commit(working, "-m", "second", "--author", "t")

    changed = catalog.restore(
        working, remote / layout.version_metadata(first), data_path=repo.data_url(store), dataset_data=repo.data_url(store)
    )
    assert changed == ["main.a"]


def test_restore_leaves_a_table_whose_files_the_working_metadata_pinned(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.a AS SELECT 1 AS id; CREATE TABLE lake.main.b AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "t")
    first = repo.head(store)
    write(working, store, "INSERT INTO lake.main.b VALUES (2)")
    commit(working, "-m", "second", "--author", "t")
    # As a clone of a dataset over HTTP makes it: published files by their URL, new ones in a local folder.
    local = tmp_path / "local"
    local.mkdir()
    frozen = tmp_path / "second.duckdb"
    frozen.write_bytes((remote / layout.version_metadata(repo.head(store))).read_bytes())
    catalog.pin_files(frozen, repo.data_url(store))
    pinned = tmp_path / "pinned.sqlite"
    catalog.convert(frozen, pinned, data_path=local.as_posix() + "/")

    changed = catalog.restore(
        pinned, remote / layout.version_metadata(first), data_path=local.as_posix() + "/", dataset_data=repo.data_url(store)
    )
    assert changed == ["main.b"]


def test_restore_keeps_what_the_columns_declared_and_drops_new_schemas(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
    write(
        working,
        store,
        "CREATE TABLE lake.main.a (id INTEGER NOT NULL, name VARCHAR); INSERT INTO lake.main.a VALUES (1, 'x'); "
        "COMMENT ON TABLE lake.main.a IS 'Towns'; COMMENT ON COLUMN lake.main.a.id IS 'Code'",
    )
    commit(working, "-m", "first", "--author", "t")
    first = repo.head(store)
    write(working, store, "INSERT INTO lake.main.a VALUES (2, 'y'); CREATE SCHEMA lake.extra; CREATE TABLE lake.extra.t AS SELECT 1 AS id")
    commit(working, "-m", "second", "--author", "t")

    changed = catalog.restore(
        working, remote / layout.version_metadata(first), data_path=repo.data_url(store), dataset_data=repo.data_url(store)
    )
    assert changed == ["extra", "extra.t", "main.a"]
    con = duckdb.connect()
    for statement in catalog.attach_sql(working, alias="lake", data_path=repo.data_url(store)):
        if statement:
            con.execute(statement)
    assert con.execute(
        "SELECT column_name, is_nullable, comment FROM duckdb_columns() "
        "WHERE database_name = 'lake' AND table_name = 'a' ORDER BY column_index"
    ).fetchall() == [("id", False, "Code"), ("name", True, None)]
    assert con.execute("SELECT comment FROM duckdb_tables() WHERE database_name = 'lake' AND table_name = 'a'").fetchone() == ("Towns",)
    assert con.execute("SELECT count(*) FROM duckdb_schemas() WHERE database_name = 'lake' AND schema_name = 'extra'").fetchone() == (0,)
    con.close()


def test_revert_refuses_a_version_outside_the_branch(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
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
    run("clone", remote, working)
    assert "Nothing to push" in run("push", working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    run("commit", working, "-m", "first try", "--author", "t")
    assert repo.head(store) is None

    # Committing again replaces the version not pushed yet.
    write(working, store, "INSERT INTO lake.main.towns VALUES (2)")
    run("commit", working, "-m", "second try", "--author", "t")
    assert len(list((tmp_path / "lake.sqlite.fdl" / "pending").iterdir())) == 1
    # Status counts from the commit, and names it.
    status = run("status", working)
    assert "Committed, not pushed" in status and "Nothing to commit" in status
    assert json.loads(run("status", working, "--json"))["pending"]
    assert "version 1" in run("push", working)
    assert [v.message for v in repo.log(store)] == ["second try"]
    assert not (tmp_path / "lake.sqlite.fdl" / "pending").exists()
    assert "Committed, not pushed" not in run("status", working)

    # A commit that finds nothing since the push drops the version not pushed yet.
    write(working, store, "INSERT INTO lake.main.towns VALUES (3)")
    run("commit", working, "-m", "third try", "--author", "t")
    write(working, store, "DELETE FROM lake.main.towns WHERE id = 3")
    assert "Nothing to commit" in run("commit", working, "-m", "fourth try", "--author", "t")
    assert not (tmp_path / "lake.sqlite.fdl" / "pending").exists()
    assert "Nothing to push" in run("push", working)


def test_gc_deletes_what_no_live_version_reaches(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "t")
    first_files = set(store.list(layout.DEFAULT_DATA_DIR))
    # The second catalog drops the first snapshot, so it no longer names the first file.
    # A catalog that kept it would reach it, and gc would keep it.
    write(
        working,
        store,
        "CREATE OR REPLACE TABLE lake.main.towns AS SELECT 2 AS id; "
        "CALL ducklake_expire_snapshots('lake', older_than => now())",
    )
    commit(working, "-m", "second", "--author", "t")
    # Written and never pushed.
    write(working, store, "CREATE OR REPLACE TABLE lake.main.towns AS SELECT 3 AS id")
    every = set(store.list(layout.DEFAULT_DATA_DIR))
    assert len(every) == 3

    # Nothing is old enough yet.
    assert "0 file(s) would be deleted" in run("gc", remote)
    run("expire", remote, "--grace", "0m")
    old = time.time() - 2 * 86400
    for path in (remote / layout.DEFAULT_DATA_DIR).rglob("*.parquet"):
        os.utime(path, (old, old))

    out = run("gc", remote, "--grace", "1d")
    assert "2 file(s) would be deleted" in out
    assert set(store.list(layout.DEFAULT_DATA_DIR)) == every
    assert "Deleted 2 file(s)" in run("gc", remote, "--grace", "1d", "--yes")
    left = set(store.list(layout.DEFAULT_DATA_DIR))
    assert len(left) == 1 and not left & first_files
    # The branch still reads.
    assert rows(store, "SELECT id FROM v.main.towns") == [(2,)]


def test_gc_keeps_what_a_version_pushed_since_the_plan_reaches(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    old = time.time() - 2 * 86400
    for path in (remote / layout.DEFAULT_DATA_DIR).rglob("*.parquet"):
        os.utime(path, (old, old))
    plan = repo.plan_collection(store, grace=__import__("datetime").timedelta(days=1))
    assert len(plan.deleted) == 1
    # Pushed between the plan and the delete: its file stays.
    commit(working, "-m", "first", "--author", "t")
    assert repo.collect(store, plan=plan) == []
    assert len(store.list(layout.DEFAULT_DATA_DIR)) == 1


def _move_data_dir(remote: Path, to: str) -> None:
    """Move the data files the way a host does: copy them, then switch fdl.json."""
    config = json.loads((remote / layout.CONFIG).read_text())
    shutil.copytree(remote / config["data_dir"], remote / to)
    (remote / layout.CONFIG).write_text(json.dumps({**config, "data_dir": to}))


def _cloned_before_the_move(tmp_path: Path) -> tuple[Path, Path, LocalStore]:
    remote, working = tmp_path / "remote", tmp_path / "work" / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote, "--data-dir", "old/")
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "tester")
    write(working, store, "INSERT INTO lake.main.towns VALUES (2)")
    return remote, working, store


def test_a_commit_cloned_before_data_dir_moved_is_refused(tmp_path: Path) -> None:
    remote, working, store = _cloned_before_the_move(tmp_path)
    _move_data_dir(remote, "data/")

    result = runner.invoke(app, ["commit", str(working), "-m", "second", "--author", "tester"])
    assert result.exit_code != 0
    assert "data_dir in fdl.json is now 'data/'" in result.output
    assert "Clone again" in result.output
    assert len(list(repo.log(store))) == 1


def test_a_push_committed_before_data_dir_moved_is_refused(tmp_path: Path) -> None:
    remote, working, store = _cloned_before_the_move(tmp_path)
    run("commit", working, "-m", "second", "--author", "tester")
    _move_data_dir(remote, "data/")

    result = runner.invoke(app, ["push", str(working)])
    assert result.exit_code != 0
    assert "Clone again" in result.output
    assert len(list(repo.log(store))) == 1
    # A clone after the move writes where versions read.
    again = tmp_path / "again" / "lake.sqlite"
    run("clone", remote, again)
    write(again, store, "INSERT INTO lake.main.towns VALUES (2)")
    commit(again, "-m", "second", "--author", "tester")
    assert len(list(repo.log(store))) == 2


def test_clone_names_the_working_metadata_after_the_dataset(tmp_path: Path, monkeypatch) -> None:
    remote = tmp_path / "towns"
    run("init", remote)
    monkeypatch.chdir(tmp_path)
    run("clone", f"{remote}/")
    assert (tmp_path / "towns.sqlite").exists()


def test_clone_names_a_folder_given_as_dot_by_where_it_is(tmp_path: Path, monkeypatch) -> None:
    from fdl.cli import _default_working

    monkeypatch.chdir(tmp_path)
    assert _default_working(".") == Path(f"{tmp_path.name}.sqlite")
    assert _default_working("https://data.example.invalid/datasets/alice/towns/") == Path("towns.sqlite")


def test_clone_writes_only_the_working_metadata(tmp_path: Path, elsewhere: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "work" / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    write(working, store, TITLED.format("Towns"))
    commit(working, "-m", "first", "--author", "tester")
    head = repo.head(store)
    assert head.tables is not None

    run("clone", remote, "again.sqlite")
    assert sorted(p.name for p in elsewhere.iterdir()) == ["again.sqlite", "again.sqlite.fdl"]


def test_the_author_comes_from_the_settings_when_not_given(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("FDL_AUTHOR_NAME", raising=False)
    monkeypatch.delenv("FDL_AUTHOR_EMAIL", raising=False)
    remote, working = tmp_path / "remote", tmp_path / "work" / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")

    result = runner.invoke(app, ["commit", str(working), "-m", "first"])
    assert result.exit_code == 1 and "fdl config author.name" in result.output

    run("config", "author.name", "Alice")
    run("config", "author.email", "alice@example.com")
    assert run("config", "author.name").strip() == "Alice"
    commit(working, "-m", "first")
    assert repo.head(store).author == {"name": "Alice", "email": "alice@example.com"}

    monkeypatch.setenv("FDL_AUTHOR_NAME", "Sync")
    write(working, store, "INSERT INTO lake.main.towns VALUES (2)")
    commit(working, "-m", "second")
    assert repo.head(store).author == {"name": "Sync"}

    write(working, store, "INSERT INTO lake.main.towns VALUES (3)")
    commit(working, "-m", "third", "--author", "bob")
    assert repo.head(store).author == {"name": "bob"}
    assert runner.invoke(app, ["config", "user.name", "x"]).exit_code == 1


def test_a_malformed_settings_file_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "fdl").mkdir()
    (tmp_path / "fdl" / "config.toml").write_text("[author", encoding="utf-8")
    result = runner.invoke(app, ["config", "author.name"])
    assert result.exit_code == 1 and "is not TOML" in result.output


def test_config_keeps_the_credential_helpers_in_the_same_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    settings = tmp_path / "fdl" / "config.toml"
    settings.parent.mkdir()
    settings.write_text('[credential."https://data.example.org/"]\nhelper = "queria credential"\n', encoding="utf-8")
    run("config", "author.name", 'Alice "A"')
    run("config", "author.email", "alice@example.com")
    run("config", "author.name", "Alice")
    assert tomllib.loads(settings.read_text(encoding="utf-8")) == {
        "author": {"name": "Alice", "email": "alice@example.com"},
        "credential": {"https://data.example.org/": {"helper": "queria credential"}},
    }


def test_a_version_keeps_only_its_last_snapshot(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.a AS SELECT 1 AS id; CREATE TABLE lake.main.b AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "t")
    write(working, store, "INSERT INTO lake.main.a VALUES (2); DROP TABLE lake.main.b")
    commit(working, "-m", "second", "--author", "t")
    second = repo.head(store)

    con = duckdb.connect(str(remote / layout.version_metadata(second)), read_only=True)
    assert con.execute("SELECT snapshot_id FROM ducklake_snapshot").fetchall() == [(second.ducklake.snapshot_id,)]
    assert con.execute("SELECT count(*) FROM ducklake_table WHERE end_snapshot IS NOT NULL").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM ducklake_files_scheduled_for_deletion").fetchone()[0] == 0
    con.close()
    # The diff was made before the earlier snapshots went.
    assert second.diff is not None and second.diff["tables"]["dropped"] == ["main.b"]
    assert catalog.storage_version(remote / layout.version_metadata(second)) == second.metadata.storage


def test_config_keeps_the_keys_before_the_first_table_at_the_top(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    settings = tmp_path / "fdl" / "config.toml"
    settings.parent.mkdir()
    settings.write_text('credential."https://data.example.org/".helper = "queria credential"\n', encoding="utf-8")
    run("config", "author.name", "Alice")
    assert tomllib.loads(settings.read_text(encoding="utf-8")) == {
        "author": {"name": "Alice"},
        "credential": {"https://data.example.org/": {"helper": "queria credential"}},
    }


def test_config_rewrites_the_author_table_where_it_is(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    settings = tmp_path / "fdl" / "config.toml"
    settings.parent.mkdir()
    settings.write_text('[ author ]\nname = "Bob"\n\n# queria\n[credential."https://x/"]\nhelper = "h"\n', encoding="utf-8")
    run("config", "author.email", "bob@example.com")
    assert settings.read_text(encoding="utf-8") == (
        '[author]\nname = "Bob"\nemail = "bob@example.com"\n\n# queria\n[credential."https://x/"]\nhelper = "h"\n'
    )


def test_an_author_that_is_not_a_table_is_not_read(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "fdl").mkdir()
    (tmp_path / "fdl" / "config.toml").write_text('author = "Alice"\n', encoding="utf-8")
    assert run("config", "author.name").strip() == ""


def test_a_working_metadata_cloned_by_an_earlier_fdl_says_so(tmp_path: Path) -> None:
    working = tmp_path / "lake.sqlite"
    working.write_bytes(b"")
    (tmp_path / "lake.sqlite.fdl.json").write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["status", str(working)])
    assert result.exit_code != 0 and "earlier fdl" in result.output


def test_a_clone_that_names_no_branch_builds_on_the_branch_head_names(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "work" / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "tester")
    run("branch", remote, "stable")
    # A host changes the default branch by writing HEAD.
    (remote / "HEAD").write_text("ref: refs/heads/stable\n")

    from fdl.cli import _read_state

    run("clone", remote, tmp_path / "second.sqlite")
    assert _read_state(tmp_path / "second.sqlite")["ref"] == "refs/heads/stable"
    assert runner.invoke(app, ["branch", str(remote), "stable", "-d"]).exit_code == 1


def test_a_clone_refuses_a_head_that_names_a_tag(tmp_path: Path) -> None:
    remote, working = tmp_path / "remote", tmp_path / "work" / "lake.sqlite"
    store = LocalStore(remote)
    run("init", remote)
    run("clone", remote, working)
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "tester")
    run("tag", remote, "first")
    # A push from this clone would move the tag, which is never moved.
    (remote / "HEAD").write_text("ref: refs/tags/first\n")

    result = runner.invoke(app, ["clone", str(remote), str(tmp_path / "second.sqlite")])
    assert result.exit_code != 0 and "not a branch" in result.output
