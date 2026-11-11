"""fdl branch and fdl merge: the three-way merge of tables, rows and descriptions."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from fdl import catalog, merge, repo
from fdl.cli import app
from fdl.stores import LocalStore

runner = CliRunner()

DESCRIBED = {
    "name": "towns",
    "title": "Towns",
    "tables": [{"schema": "main", "name": "towns", "primary_key": ["id"]}],
}


@pytest.fixture(autouse=True)
def described(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(DESCRIBED))
    monkeypatch.chdir(tmp_path)
    return path


def run(*args, code: int = 0) -> str:
    result = runner.invoke(app, [str(a) for a in args])
    assert result.exit_code == code, result.output
    return result.output


def sql(working: Path, store: LocalStore, statement: str) -> list[tuple]:
    con = duckdb.connect()
    for line in catalog.attach_sql(working, alias="lake", data_path=repo.data_url(store)):
        if line:
            con.execute(line)
    result = con.execute(statement)
    rows = result.fetchall() if result.description else []
    con.close()
    return rows


def save(working: Path, message: str) -> None:
    run("commit", working, "-m", message, "--author", "tester")
    run("push", working)


@pytest.fixture
def branches(tmp_path: Path):
    """main and feature at a first version holding three towns, each with a working catalog."""
    remote = tmp_path / "remote"
    store = LocalStore(remote)
    run("init", remote)
    main, feature = tmp_path / "main.sqlite", tmp_path / "feature.sqlite"
    run("checkout", remote, main)
    sql(main, store, "CREATE TABLE lake.main.towns AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) t(id, name)")
    save(main, "first")
    run("branch", remote, "feature")
    run("checkout", remote, feature, "--ref", "refs/heads/feature")
    return store, main, feature


def towns(working: Path, store: LocalStore) -> list[tuple]:
    return sql(working, store, "SELECT * FROM lake.main.towns ORDER BY id")


def test_rows_each_side_changed_are_merged_by_primary_key(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "UPDATE lake.main.towns SET name = 'B' WHERE id = 2")
    sql(feature, store, "CREATE TABLE lake.main.extra AS SELECT 42 AS x")
    save(feature, "feature")
    sql(main, store, "UPDATE lake.main.towns SET name = 'C' WHERE id = 3; INSERT INTO lake.main.towns VALUES (4, 'd')")
    save(main, "main")
    ours, theirs = repo.head(store, "refs/heads/main"), repo.head(store, "refs/heads/feature")

    out = run("merge", "refs/heads/feature", main, "--author", "tester")
    assert "rows merged  main.towns (1 row(s) from theirs)" in out
    assert "from theirs  main.extra" in out
    run("push", main)

    merged = repo.head(store, "refs/heads/main")
    assert merged.parents == (ours.id, theirs.id)
    assert merged.seq == ours.seq + 1
    assert merged.message == "Merge refs/heads/feature"
    assert towns(main, store) == [(1, "a"), (2, "B"), (3, "C"), (4, "d")]
    assert sql(main, store, "SELECT * FROM lake.main.extra") == [(42,)]


def test_a_row_both_sides_changed_differently_is_a_conflict(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "UPDATE lake.main.towns SET name = 'x' WHERE id = 2")
    save(feature, "feature")
    sql(main, store, "UPDATE lake.main.towns SET name = 'y' WHERE id = 2")
    save(main, "main")
    before = repo.head(store, "refs/heads/main")

    out = run("merge", "refs/heads/feature", main, "--author", "tester", code=1)
    assert "main.towns: both sides changed 1 row(s) differently: id=2" in out
    assert "Nothing to push" in run("push", main)
    assert repo.head(store, "refs/heads/main") == before
    assert towns(main, store) == [(1, "a"), (2, "y"), (3, "c")]


def test_the_same_change_on_both_sides_is_no_conflict(branches) -> None:
    store, main, feature = branches
    for working in (feature, main):
        sql(working, store, "DELETE FROM lake.main.towns WHERE id = 1")
        save(working, "drop 1")

    run("merge", "refs/heads/feature", main, "--author", "tester")
    run("push", main)
    assert towns(main, store) == [(2, "b"), (3, "c")]


def test_a_table_without_a_primary_key_is_not_merged_by_rows(branches, described: Path) -> None:
    store, main, feature = branches
    described.write_text(json.dumps({"name": "towns", "title": "Towns"}))
    sql(feature, store, "INSERT INTO lake.main.towns VALUES (5, 'e')")
    save(feature, "feature")
    sql(main, store, "INSERT INTO lake.main.towns VALUES (4, 'd')")
    save(main, "main")

    out = run("merge", "refs/heads/feature", main, "--author", "tester", code=1)
    assert "main.towns: both sides changed the table, and it has no primary key both declare" in out


def test_a_dropped_table_the_other_side_changed_is_a_conflict(branches, described: Path) -> None:
    store, main, feature = branches
    described.write_text(json.dumps({"name": "towns", "title": "Towns"}))
    sql(feature, store, "DROP TABLE lake.main.towns; CREATE TABLE lake.main.other AS SELECT 1 AS id")
    save(feature, "feature")
    described.write_text(json.dumps(DESCRIBED))
    sql(main, store, "INSERT INTO lake.main.towns VALUES (4, 'd')")
    save(main, "main")

    out = run("merge", "refs/heads/feature", main, "--author", "tester", code=1)
    assert "main.towns: one side dropped the table and the other changed it" in out


def test_a_view_one_side_added_comes_over(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "CREATE VIEW lake.main.named AS SELECT name FROM main.towns")
    save(feature, "feature")
    sql(main, store, "INSERT INTO lake.main.towns VALUES (4, 'd')")
    save(main, "main")

    assert "from theirs  main.named" in run("merge", "refs/heads/feature", main, "--author", "tester")
    run("push", main)
    assert sql(main, store, "SELECT count(*) FROM lake.main.named") == [(4,)]


def test_descriptions_are_merged_key_by_key(branches, described: Path) -> None:
    store, main, feature = branches
    described.write_text(json.dumps({**DESCRIBED, "description": "Towns of a city"}))
    sql(feature, store, "INSERT INTO lake.main.towns VALUES (5, 'e')")
    save(feature, "feature")
    described.write_text(json.dumps({**DESCRIBED, "title": "All towns"}))
    sql(main, store, "INSERT INTO lake.main.towns VALUES (4, 'd')")
    save(main, "main")

    run("merge", "refs/heads/feature", main, "--author", "tester")
    run("push", main)
    merged = repo.head(store, "refs/heads/main")
    written = json.loads(store.get(f"versions/{merged.id}/dataset.json"))
    assert (written["title"], written["description"]) == ("All towns", "Towns of a city")
    assert written["tables"][0]["rows"] == 5


def test_a_branch_behind_the_other_moves_forward_without_a_version(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "INSERT INTO lake.main.towns VALUES (4, 'd')")
    save(feature, "feature")
    theirs = repo.head(store, "refs/heads/feature")

    assert "Fast-forward" in run("merge", "refs/heads/feature", main, "--author", "tester")
    assert "Fast-forwarded, not pushed" in run("status", main)
    assert repo.head(store, "refs/heads/main").id != theirs.id
    run("push", main)
    assert repo.head(store, "refs/heads/main").id == theirs.id
    assert towns(main, store)[-1] == (4, "d")


def test_a_branch_already_holding_the_other_merges_nothing(branches) -> None:
    store, main, _ = branches
    sql(main, store, "INSERT INTO lake.main.towns VALUES (4, 'd')")
    save(main, "main")
    assert "Already up to date" in run("merge", "refs/heads/feature", main, "--author", "tester")


def test_a_merge_needs_the_changes_committed(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "INSERT INTO lake.main.towns VALUES (4, 'd')")
    save(feature, "feature")
    sql(main, store, "INSERT INTO lake.main.towns VALUES (5, 'e')")
    assert "changes not committed" in run("merge", "refs/heads/feature", main, "--author", "tester", code=1)


def test_a_merge_waits_for_a_commit_not_pushed(branches) -> None:
    store, main, _ = branches
    sql(main, store, "INSERT INTO lake.main.towns VALUES (4, 'd')")
    run("commit", main, "-m", "main", "--author", "tester")
    assert "committed and not pushed" in run("merge", "refs/heads/feature", main, "--author", "tester", code=1)


def test_branches_are_listed_created_and_deleted(branches, tmp_path: Path) -> None:
    store, _, _ = branches
    remote = tmp_path / "remote"
    assert "feature  " in run("branch", remote)
    run("branch", remote, "feature")  # again, at the same version: harmless
    run("branch", remote, "feature", "--delete")
    assert "feature" not in run("branch", remote)
    assert "HEAD names refs/heads/main" in run("branch", remote, "main", "--delete", code=1)


# -- the pieces ------------------------------------------------------------------------


def test_values_one_side_changed_take_that_side() -> None:
    base = {"title": "a", "tables": [{"schema": "s", "name": "t", "description": "x"}]}
    ours = {"title": "b", "tables": [{"schema": "s", "name": "t", "description": "x"}]}
    theirs = {"title": "a", "tables": [{"schema": "s", "name": "t", "description": "y"}, {"schema": "s", "name": "u"}]}
    merged, conflicts = merge.values(base, ours, theirs)
    assert conflicts == []
    assert merged == {"title": "b", "tables": [{"schema": "s", "name": "t", "description": "y"}, {"schema": "s", "name": "u"}]}


def test_values_both_sides_changed_differently_conflict() -> None:
    base = {"keywords": ["a"], "tables": [{"schema": "s", "name": "t", "title": "x"}]}
    ours = {"keywords": ["b"], "tables": [{"schema": "s", "name": "t", "title": "y"}]}
    theirs = {"keywords": ["c"], "tables": []}
    _, conflicts = merge.values(base, ours, theirs)
    assert conflicts == ["keywords", "tables[s.t]"]


def test_primary_keys_must_agree() -> None:
    def described(*keys):
        return {"tables": [{"schema": "s", "name": name, "primary_key": key} for name, key in keys]}

    base = described(("same", ["id"]), ("changed", ["id"]))
    ours = described(("same", ["id"]), ("changed", ["code"]), ("new", ["id"]), ("differ", ["id"]))
    theirs = described(("same", ["id"]), ("changed", ["code"]), ("new", ["id"]), ("differ", ["code"]))
    assert merge.primary_keys(base, ours, theirs) == {("s", "same"): ["id"], ("s", "new"): ["id"]}


def test_a_merge_base_is_the_newest_common_ancestor(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "remote")
    repo.init(store)

    from fdl.version import Catalog, DuckLake, new_id, utc_now

    def put(*parents: repo.Version) -> repo.Version:
        made = repo.Version(
            id=new_id(),
            seq=parents[0].seq + 1 if parents else 1,
            parents=tuple(parent.id for parent in parents),
            created_at=utc_now(),
            author={"name": "tester"},
            message="",
            catalog=Catalog(path="ducklake.duckdb", storage="v1.5.0", bytes=0, sha256="0" * 64),
            ducklake=DuckLake(format="1.0", snapshot_id=1, snapshots=(1, 1)),
        )
        store.put(f"versions/{made.id}/version.json", made.to_json())
        return made

    root = put()
    a1, b1 = put(root), put(root)
    a2 = put(a1, b1)  # b1 merged into a
    b2 = put(b1)
    assert [v.id for v in repo.merge_bases(store, a2, b2)] == [b1.id]
    assert repo.is_ancestor(store, b1.id, a2)
    # A merge's seq follows its first parent, so it is no bound on the walk.
    long = put(put(put(b1)))
    late = put(a1, long)
    assert late.seq < long.seq
    assert repo.is_ancestor(store, long.id, late)
    # Two criss-cross merges leave two bases.
    x, y = put(a1, b1), put(b1, a1)
    assert {v.id for v in repo.merge_bases(store, x, y)} == {a1.id, b1.id}
