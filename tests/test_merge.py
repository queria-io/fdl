"""fdl branch and fdl merge: the three-way merge of tables, rows, cells and declarations."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from fdl import catalog, merge, repo
from fdl.cli import app
from fdl.declared import Facts
from fdl.stores import LocalStore

runner = CliRunner()

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


def keyed(working: Path, table: str, *columns: str) -> None:
    """Declare the primary key of ``main.<table>``: ``id``, or none with ``()``."""
    run("declare", "primary-key", working, f"main.{table}", *(columns if columns != ((),) else ()))


def unkeyed(working: Path, table: str) -> None:
    run("declare", "primary-key", working, f"main.{table}")


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
    run("clone", remote, main)
    sql(main, store, "CREATE TABLE lake.main.towns AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) t(id, name)")
    keyed(main, "towns", "id")
    save(main, "first")
    run("branch", remote, "feature")
    run("clone", remote, feature, "--branch", "feature")
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
    assert "rows merged  main.towns (1 row(s) added, changed or deleted by theirs)" in out
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


def test_a_table_without_a_primary_key_is_not_merged_by_rows(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "INSERT INTO lake.main.towns VALUES (5, 'e')")
    unkeyed(feature, "towns")
    save(feature, "feature")
    sql(main, store, "INSERT INTO lake.main.towns VALUES (4, 'd')")
    unkeyed(main, "towns")
    save(main, "main")

    out = run("merge", "refs/heads/feature", main, "--author", "tester", code=1)
    assert "main.towns: both sides changed the table, and it has no primary key both declare" in out


def test_a_dropped_table_the_other_side_changed_is_a_conflict(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "DROP TABLE lake.main.towns; CREATE TABLE lake.main.other AS SELECT 1 AS id")
    save(feature, "feature")
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


def test_declarations_each_side_changed_are_merged(branches, tmp_path: Path) -> None:
    store, main, feature = branches
    other = (tmp_path / "remote").resolve().as_uri() + "/"
    sql(feature, store, "INSERT INTO lake.main.towns VALUES (5, 'e')")
    run("declare", "profile", feature, "main.towns", "false")
    save(feature, "feature")
    sql(main, store, "INSERT INTO lake.main.towns VALUES (4, 'd')")
    run("declare", "dependency", main, "other", other)
    save(main, "main")

    run("merge", "refs/heads/feature", main, "--author", "tester")
    run("push", main)
    index = tables_json(store)
    assert (index["tables"]["main.towns"]["primary_key"], index["tables"]["main.towns"]["profile"]) == (["id"], False)
    assert index["dependencies"] == {"other": {"url": other}}


def test_a_declaration_both_sides_changed_differently_is_a_conflict(branches) -> None:
    store, main, feature = branches
    run("declare", "primary-key", feature, "main.towns", "name")
    save(feature, "feature")
    run("declare", "primary-key", main, "main.towns", "id", "name")
    save(main, "main")

    out = run("merge", "refs/heads/feature", main, "--author", "tester", code=1)
    assert "declarations: main.towns: both sides changed the primary key differently" in out


def test_a_merge_whose_declarations_the_tables_refuse_is_a_conflict(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "ALTER TABLE lake.main.towns DROP COLUMN name")
    save(feature, "feature")
    run("declare", "primary-key", main, "main.towns", "id", "name")
    save(main, "main")
    before = repo.head(store, "refs/heads/main")

    out = run("merge", "refs/heads/feature", main, "--author", "tester", code=1)
    assert "declarations: main.towns: the primary key names columns it lacks: name" in out
    assert "Nothing to push" in run("push", main)
    assert repo.head(store, "refs/heads/main") == before
    assert towns(main, store) == [(1, "a"), (2, "b"), (3, "c")]


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


def test_primary_keys_must_agree() -> None:
    def declared(**keys):
        return Facts(primary_keys={f"s.{name}": key for name, key in keys.items()}), {f"s.{name}" for name in keys}

    base = declared(same=["id"], changed=["id"])
    ours = declared(same=["id"], changed=["code"], new=["id"], differ=["id"])
    theirs = declared(same=["id"], changed=["code"], new=["id"], differ=["code"])
    found = merge.primary_keys(base, ours, theirs)
    assert found == {("s", "same"): ["id"], ("s", "new"): ["id"]}


def test_a_merge_base_is_the_newest_common_ancestor(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "remote")
    repo.init(store)

    from fdl.version import DuckLake, Metadata, new_id, utc_now

    def put(*parents: repo.Version) -> repo.Version:
        made = repo.Version(
            id=new_id(),
            seq=parents[0].seq + 1 if parents else 1,
            parents=tuple(parent.id for parent in parents),
            created_at=utc_now(),
            author={"name": "tester"},
            message="",
            metadata=Metadata(path="metadata.db", storage="v1.5.0", bytes=0, sha256="0" * 64),
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


def test_conflicts_in_two_tables_merged_by_rows_are_both_listed(branches) -> None:
    store, main, feature = branches
    sql(main, store, "CREATE TABLE lake.main.s AS SELECT * FROM (VALUES (1, 'a'), (2, 'b')) t(id, name)")
    keyed(main, "s", "id")
    save(main, "s")
    run("merge", "refs/heads/main", feature, "--author", "tester")
    run("push", feature)
    for working, value in ((feature, "x"), (main, "y")):
        sql(working, store, f"UPDATE lake.main.towns SET name = '{value}' WHERE id = 2")
        sql(working, store, f"UPDATE lake.main.s SET name = '{value}' WHERE id = 2")
        save(working, value)

    out = run("merge", "refs/heads/feature", main, "--author", "tester", code=1)
    assert "main.s: both sides changed 1 row(s) differently: id=2" in out
    assert "main.towns: both sides changed 1 row(s) differently: id=2" in out


def test_rows_of_a_table_named_s_are_merged_by_key(branches) -> None:
    store, main, feature = branches
    sql(main, store, "CREATE TABLE lake.main.s AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) t(id, name)")
    keyed(main, "s", "id")
    save(main, "s")
    run("merge", "refs/heads/main", feature, "--author", "tester")
    run("push", feature)
    sql(feature, store, "UPDATE lake.main.s SET name = 'B' WHERE id = 2")
    save(feature, "feature")
    sql(main, store, "UPDATE lake.main.s SET name = 'C' WHERE id = 3")
    save(main, "main")

    run("merge", "refs/heads/feature", main, "--author", "tester")
    run("push", main)
    assert sql(main, store, "SELECT * FROM lake.main.s ORDER BY id") == [(1, "a"), (2, "B"), (3, "C")]


def test_a_revert_refuses_a_version_only_a_merge_brought_in(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "INSERT INTO lake.main.towns VALUES (5, 'e')")
    save(feature, "feature")
    theirs = repo.head(store, "refs/heads/feature")
    sql(main, store, "INSERT INTO lake.main.towns VALUES (4, 'd')")
    save(main, "main")
    run("merge", "refs/heads/feature", main, "--author", "tester")
    run("push", main)

    out = run("revert", theirs.id, main, "--author", "tester", code=1)
    assert "not in the first-parent history" in out


def comments(working: Path, store: LocalStore) -> dict:
    tables = sql(working, store, "SELECT table_name, comment FROM duckdb_tables() WHERE database_name = 'lake' AND table_name = 'towns'")
    columns = sql(working, store, "SELECT column_name, comment FROM duckdb_columns() WHERE database_name = 'lake' AND table_name = 'towns'")
    return {**dict(tables), **dict(columns)}


def test_comments_of_ours_are_kept_when_the_table_is_taken_from_theirs(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "UPDATE lake.main.towns SET name = 'B' WHERE id = 2; COMMENT ON COLUMN lake.main.towns.id IS 'Town ID'")
    save(feature, "feature")
    sql(main, store, "COMMENT ON TABLE lake.main.towns IS 'Towns'; COMMENT ON COLUMN lake.main.towns.name IS 'Town name'")
    sql(main, store, "CREATE TABLE lake.main.extra AS SELECT 42 AS x")
    save(main, "main")
    run("merge", "refs/heads/feature", main, "--author", "tester")
    assert towns(main, store) == [(1, "a"), (2, "B"), (3, "c")]
    assert comments(main, store) == {"towns": "Towns", "id": "Town ID", "name": "Town name"}


def test_comments_of_theirs_are_kept_when_rows_are_merged(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "UPDATE lake.main.towns SET name = 'B' WHERE id = 2; COMMENT ON COLUMN lake.main.towns.name IS 'Town name'")
    save(feature, "feature")
    sql(main, store, "UPDATE lake.main.towns SET name = 'C' WHERE id = 3; COMMENT ON TABLE lake.main.towns IS 'Towns'")
    save(main, "main")
    run("merge", "refs/heads/feature", main, "--author", "tester")
    assert towns(main, store) == [(1, "a"), (2, "B"), (3, "C")]
    assert comments(main, store) == {"towns": "Towns", "id": None, "name": "Town name"}


def test_a_comment_both_sides_changed_keeps_ours_with_a_warning(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "UPDATE lake.main.towns SET name = 'B' WHERE id = 2; COMMENT ON TABLE lake.main.towns IS 'Theirs'")
    save(feature, "feature")
    sql(main, store, "UPDATE lake.main.towns SET name = 'C' WHERE id = 3; COMMENT ON TABLE lake.main.towns IS 'Ours'")
    save(main, "main")
    output = run("merge", "refs/heads/feature", main, "--author", "tester")
    assert "comment of main.towns" in output
    assert comments(main, store)["towns"] == "Ours"


def test_a_comment_only_theirs_changed_comes_over_with_no_table_to_write(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "CREATE TABLE lake.main.extra AS SELECT 42 AS x; COMMENT ON TABLE lake.main.towns IS 'Towns'")
    save(feature, "feature")
    sql(main, store, "CREATE TABLE lake.main.extra AS SELECT 42 AS x")
    save(main, "main")
    run("merge", "refs/heads/feature", main, "--author", "tester")
    assert comments(main, store)["towns"] == "Towns"


def test_a_comment_ours_changed_on_a_view_theirs_made_a_table(branches) -> None:
    store, main, feature = branches
    sql(main, store, "CREATE VIEW lake.main.named AS SELECT name FROM lake.main.towns")
    save(main, "view")
    run("merge", "refs/heads/main", feature, "--author", "tester")
    run("push", feature)
    sql(feature, store, "DROP VIEW lake.main.named; CREATE TABLE lake.main.named AS SELECT 'a' AS name; COMMENT ON TABLE lake.main.named IS 'Theirs'")
    save(feature, "feature")
    sql(main, store, "COMMENT ON VIEW lake.main.named IS 'Ours'; INSERT INTO lake.main.towns VALUES (4, 'd')")
    save(main, "main")
    output = run("merge", "refs/heads/feature", main, "--author", "tester")
    assert "comment of main.named" in output


def test_cells_of_one_row_changed_on_each_side_are_merged(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "ALTER TABLE lake.main.towns ADD COLUMN note VARCHAR")
    save(feature, "note")
    run("merge", "refs/heads/feature", main, "--author", "tester")
    run("push", main)
    sql(feature, store, "UPDATE lake.main.towns SET note = 'n' WHERE id = 2")
    save(feature, "feature")
    sql(main, store, "UPDATE lake.main.towns SET name = 'y' WHERE id = 2")
    save(main, "main")

    run("merge", "refs/heads/feature", main, "--author", "tester")
    assert sql(main, store, "SELECT id, name, note FROM lake.main.towns WHERE id = 2") == [(2, "y", "n")]


def test_a_row_dropped_on_one_side_and_changed_on_the_other_is_a_conflict(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "DELETE FROM lake.main.towns WHERE id = 2")
    save(feature, "feature")
    sql(main, store, "UPDATE lake.main.towns SET name = 'y' WHERE id = 2")
    save(main, "main")

    out = run("merge", "refs/heads/feature", main, "--author", "tester", code=1)
    assert "main.towns: both sides changed 1 row(s) differently: id=2" in out


def tables_json(store: LocalStore) -> dict:
    version = repo.head(store)
    return json.loads(store.get(f"versions/{version.id}/{version.tables.path}"))


def test_columns_each_side_added_or_dropped_are_merged(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "ALTER TABLE lake.main.towns ADD COLUMN note VARCHAR; UPDATE lake.main.towns SET note = 'n' WHERE id = 1")
    sql(feature, store, "INSERT INTO lake.main.towns VALUES (5, 'e', 'new')")
    save(feature, "feature")
    sql(main, store, "ALTER TABLE lake.main.towns ADD COLUMN size INTEGER DEFAULT 0; UPDATE lake.main.towns SET name = 'B' WHERE id = 2")
    sql(main, store, "INSERT INTO lake.main.towns VALUES (4, 'd', 7)")
    save(main, "main")

    run("merge", "refs/heads/feature", main, "--author", "tester")
    run("push", main)
    assert sql(main, store, "SELECT id, name, size, note FROM lake.main.towns ORDER BY id") == [
        (1, "a", 0, "n"),
        (2, "B", 0, None),
        (3, "c", 0, None),
        (4, "d", 7, None),
        (5, "e", 0, "new"),
    ]


def test_a_column_dropped_on_one_side_and_changed_on_the_other_is_a_conflict(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "ALTER TABLE lake.main.towns DROP COLUMN name; INSERT INTO lake.main.towns VALUES (5)")
    save(feature, "feature")
    sql(main, store, "UPDATE lake.main.towns SET name = 'y' WHERE id = 2")
    save(main, "main")

    out = run("merge", "refs/heads/feature", main, "--author", "tester", code=1)
    assert "main.towns: both sides changed 1 row(s) differently: id=2" in out


def test_a_row_both_sides_added_differently_is_a_conflict(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "INSERT INTO lake.main.towns VALUES (4, 'x'), (5, 'same')")
    save(feature, "feature")
    sql(main, store, "INSERT INTO lake.main.towns VALUES (4, NULL), (5, 'same')")
    save(main, "main")

    out = run("merge", "refs/heads/feature", main, "--author", "tester", code=1)
    assert "main.towns: both sides changed 1 row(s) differently: id=4" in out


def test_a_column_renamed_on_one_side_takes_cells_changed_on_the_other(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "ALTER TABLE lake.main.towns RENAME COLUMN name TO title")
    save(feature, "feature")
    sql(main, store, "UPDATE lake.main.towns SET name = 'y' WHERE id = 2")
    save(main, "main")

    run("merge", "refs/heads/feature", main, "--author", "tester")
    assert sql(main, store, "SELECT id, title FROM lake.main.towns ORDER BY id") == [(1, "a"), (2, "y"), (3, "c")]


def test_cells_changed_on_one_side_are_cast_to_the_type_the_other_gave(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "ALTER TABLE lake.main.towns ALTER COLUMN id SET DATA TYPE BIGINT")
    sql(feature, store, "ALTER TABLE lake.main.towns ADD COLUMN size INTEGER; UPDATE lake.main.towns SET size = 1")
    save(feature, "size")
    run("merge", "refs/heads/feature", main, "--author", "tester")
    run("push", main)
    sql(feature, store, "ALTER TABLE lake.main.towns ALTER COLUMN size SET DATA TYPE BIGINT")
    save(feature, "feature")
    sql(main, store, "UPDATE lake.main.towns SET size = 2 WHERE id = 2")
    save(main, "main")

    run("merge", "refs/heads/feature", main, "--author", "tester")
    assert sql(main, store, "SELECT id, size, typeof(size) FROM lake.main.towns ORDER BY id") == [
        (1, 1, "BIGINT"),
        (2, 2, "BIGINT"),
        (3, 1, "BIGINT"),
    ]


def test_a_cell_that_does_not_cast_to_the_new_type_is_a_conflict(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "ALTER TABLE lake.main.towns ADD COLUMN size VARCHAR; UPDATE lake.main.towns SET size = '1'")
    save(feature, "size")
    run("merge", "refs/heads/feature", main, "--author", "tester")
    run("push", main)
    # Made again, so the column is found by name, in a type its values do not all cast to.
    sql(feature, store, "CREATE OR REPLACE TABLE lake.main.towns AS SELECT id, name, size::INTEGER AS size FROM lake.main.towns")
    save(feature, "feature")
    sql(main, store, "UPDATE lake.main.towns SET size = 'big' WHERE id = 2")
    save(main, "main")

    out = run("merge", "refs/heads/feature", main, "--author", "tester", code=1)
    assert "main.towns: both sides changed 1 row(s) differently: id=2" in out


def test_a_not_null_column_one_side_added_while_the_other_added_rows_is_a_conflict(branches) -> None:
    store, main, feature = branches
    sql(feature, store, "INSERT INTO lake.main.towns VALUES (4, 'd')")
    save(feature, "feature")
    sql(
        main,
        store,
        "CREATE OR REPLACE TABLE lake.main.towns (id INTEGER, name VARCHAR, size INTEGER NOT NULL); "
        "INSERT INTO lake.main.towns VALUES (1, 'a', 1), (2, 'b', 1), (3, 'c', 1)",
    )
    save(main, "main")

    out = run("merge", "refs/heads/feature", main, "--author", "tester", code=1)
    assert "main.towns: ours added the column size NOT NULL with no default, and theirs added rows" in out
