"""init, commit, push and log against a dataset in a local folder.

Real DuckLake catalogs, because what matters is that a reader can attach the
version that comes out the other side.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

import duckdb
import pytest

from fdl import catalog, declared, diff, layout, refs, repo
from fdl.stores import LocalStore
from fdl.version import is_id, new_id, parse

REAL_READ = refs.read

AUTHOR = {"name": "tester"}


@pytest.fixture
def remote(tmp_path: Path) -> LocalStore:
    store = LocalStore(tmp_path / "remote")
    repo.init(store)
    return store


def data_path(store: LocalStore) -> str:
    return f"{store.root}/{layout.DEFAULT_DATA_DIR}"


def build(working: Path, store: LocalStore, sql: str) -> None:
    """Write through DuckLake into the remote's data_dir, the way a build would."""
    con = duckdb.connect()
    for statement in catalog.attach_sql(working, alias="lake", data_path=data_path(store)):
        if statement:
            con.execute(statement)
    con.execute(sql)
    con.close()


def commit(tmp_path: Path, store, parent=None, message="m", **options):
    return repo.commit(
        tmp_path / "work.sqlite",
        tmp_path / "local",
        parent=parent,
        data_path=data_path(store),
        author=AUTHOR,
        message=message,
        **options,
    )


def read_rows(store: LocalStore, version_id: str) -> list[tuple]:
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(
        f"ATTACH 'ducklake:{store.root / layout.version_metadata(repo.read_version(store, version_id))}' "
        f"AS v (READ_ONLY)"
    )
    rows = con.execute("SELECT * FROM v.main.towns ORDER BY id").fetchall()
    con.close()
    return rows


def test_init_writes_the_config_and_head(remote) -> None:
    config = json.loads(remote.get(layout.CONFIG))
    assert config == {"fdl": "0.1", "data_dir": "tables/"}
    assert remote.get(layout.HEAD) == b"ref: refs/heads/main\n"
    assert repo.head(remote) is None
    # Twice is harmless.
    assert repo.init(remote) == repo.Config(fdl="0.1", data_dir="tables/")


def test_an_id_in_fdl_json_from_an_earlier_fdl_is_ignored(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "old")
    store.put(layout.CONFIG, b'{"fdl": "0.1", "id": "5f0c8a52-6d3e-4f8e-9b1a-2c7d4e9f0a13", "data_dir": "tables/"}')
    assert repo.read_config(store) == repo.Config(fdl="0.1", data_dir="tables/")


def test_commit_push_and_read_back(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    first = commit(tmp_path, remote, message="first")
    repo.push(remote, tmp_path / "local" / first.id, first)

    assert repo.head(remote) == first
    assert first.seq == 1 and first.parents == ()
    # Whatever DuckDB writes it, the catalog is in the format readers on
    # DuckDB 1.5 open.
    assert first.metadata.storage == "duckdb-v1.5.0"
    assert first.ducklake.format == "1.0"
    assert remote.get("refs/heads/main") == f"{first.id}\n".encode()
    assert read_rows(remote, first.id) == [(1,)]

    build(tmp_path / "work.sqlite", remote, "INSERT INTO lake.main.towns VALUES (2)")
    second = commit(tmp_path, remote, parent=first, message="second")
    repo.push(remote, tmp_path / "local" / second.id, second)

    assert second.parents == (first.id,) and second.seq == 2
    assert second.ducklake.snapshots[0] > first.ducklake.snapshot_id
    assert [v.message for v in repo.log(remote)] == ["second", "first"]
    # The first version still reads as it was.
    assert read_rows(remote, first.id) == [(1,)]
    assert read_rows(remote, second.id) == [(1,), (2,)]
    assert repo.is_ancestor(remote, first.id, second)


def test_a_push_not_built_on_the_branch_is_refused(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    first = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / first.id, first)
    second = commit(tmp_path, remote, parent=first)
    repo.push(remote, tmp_path / "local" / second.id, second)

    # Built on the first while the branch moved on to the second.
    stale = commit(tmp_path, remote, parent=first)
    with pytest.raises(repo.NotFastForward):
        repo.push(remote, tmp_path / "local" / stale.id, stale)
    assert repo.head(remote) == second


def test_a_ref_moved_by_someone_else_is_not_overwritten(remote) -> None:
    refs.write(remote, "refs/heads/main", new_id(), expected=None)
    seen = refs.read(remote, "refs/heads/main")
    refs.write(remote, "refs/heads/main", new_id(), expected=seen)
    with pytest.raises(refs.RefConflict):
        refs.write(remote, "refs/heads/main", new_id(), expected=seen)


def test_pushing_the_same_version_again_is_accepted(tmp_path: Path, remote) -> None:
    """A run whose answer was lost after the ref moved is run again."""
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    first = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / first.id, first)
    repo.push(remote, tmp_path / "local" / first.id, first)
    assert repo.head(remote) == first


def test_a_push_sent_again_writes_the_ref_again(tmp_path: Path, remote, monkeypatch) -> None:
    """A host that does its work when the ref moves may have stopped after
    moving it. The second push gives it the write again."""
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    version = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / version.id, version)
    seen = refs.read(remote, "refs/heads/main")
    written: list[tuple[str, str | None]] = []
    put = remote.put
    monkeypatch.setattr(
        remote,
        "put",
        lambda key, *a, **kw: written.append((key, kw.get("if_match"))) or put(key, *a, **kw),
    )

    repo.push(remote, tmp_path / "local" / version.id, version)

    # Written on condition of the ETag read, so a ref moved since is left.
    assert ("refs/heads/main", seen.etag) in written
    assert repo.head(remote) == version


def test_a_push_sent_again_leaves_a_ref_moved_since(tmp_path: Path, remote, monkeypatch) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    version = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / version.id, version)
    later = new_id()
    seen = refs.read(remote, "refs/heads/main")
    # Another push moves main between this one's read and its write.
    monkeypatch.setattr(
        refs, "read", lambda store, name: seen if name == "refs/heads/main" else REAL_READ(store, name)
    )
    refs.write(remote, "refs/heads/main", later, expected=seen)

    repo.push(remote, tmp_path / "local" / version.id, version)

    assert REAL_READ(remote, "refs/heads/main").target == later


def test_push_without_moving_the_ref_leaves_readers_where_they_were(
    tmp_path: Path, remote
) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    first = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / first.id, first, move_ref=False)

    assert repo.head(remote) is None
    assert repo.read_version(remote, first.id) == first


def test_push_refuses_a_catalog_whose_data_files_are_gone(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    first = commit(tmp_path, remote)
    remote.delete([key for key in remote.list("tables/")])

    with pytest.raises(repo.RepoError, match="not in the dataset"):
        repo.push(remote, tmp_path / "local" / first.id, first)
    assert remote.head(layout.version_record(first.id)) is None


def test_push_refuses_a_tables_json_that_does_not_match(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    first = commit(tmp_path, remote)
    written = tmp_path / "local" / first.id / layout.TABLES
    index = json.loads(written.read_text())
    index["tables"]["main.towns"]["files"] = []
    written.write_text(json.dumps(index))
    with pytest.raises(repo.RepoError, match="tables.json is not the one version.json names"):
        repo.push(remote, tmp_path / "local" / first.id, first)
    content = written.read_bytes()
    tables = repo.File(path=layout.TABLES, bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
    with pytest.raises(repo.RepoError, match="tables.json and the metadata name different files"):
        repo.push(remote, tmp_path / "local" / first.id, dataclasses.replace(first, tables=tables))
    assert remote.head(layout.version_record(first.id)) is None


def test_version_ids_are_v7_and_ordered() -> None:
    earlier, later = new_id(1_000), new_id(2_000)
    assert is_id(earlier) and is_id(later)
    assert earlier < later


def test_a_record_under_another_id_is_refused(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    first = commit(tmp_path, remote)
    with pytest.raises(ValueError, match="sits under"):
        parse(first.to_json(), expected_id=new_id())


@pytest.mark.parametrize(
    "name", ["main", "refs/heads/Main", "refs/remotes/x", "refs/heads/-x", "refs/heads/"]
)
def test_ref_names_outside_the_format_are_refused(name: str) -> None:
    with pytest.raises(layout.LayoutError):
        layout.check_ref(name)


def test_a_ref_the_store_will_not_head_is_not_taken_as_absent(remote) -> None:
    """A HEAD refused (403) comes back as None from some stores; the branch
    is not unborn for that."""

    class Forbidden(Exception):
        status = 403

    class Guarded:
        def head(self, key):
            return None

        def get(self, key):
            raise Forbidden(key)

    with pytest.raises(Forbidden):
        refs.read(Guarded(), "refs/heads/main")
    assert refs.read(remote, "refs/heads/main") is None


def pushed_chain(tmp_path: Path, store, count: int) -> list:
    build(
        tmp_path / "work.sqlite",
        store,
        "CREATE TABLE lake.main.towns AS SELECT 1 AS id",
    )
    versions = []
    for _ in range(count):
        version = commit(tmp_path, store, parent=versions[-1] if versions else None)
        repo.push(store, tmp_path / "local" / version.id, version)
        versions.append(version)
    return versions


def test_expire_marks_and_drops_the_metadata_but_keeps_the_record(tmp_path: Path, remote) -> None:
    first, second = pushed_chain(tmp_path, remote, 2)
    repo.expire(remote, first.id)

    assert json.loads(remote.get(layout.version_expired(first.id)))["reason"] == "retention"
    assert remote.list(layout.version_dir(first.id)) == [
        layout.version_expired(first.id),
        layout.version_record(first.id),
    ]
    assert repo.is_expired(remote, first.id) and not repo.is_expired(remote, second.id)
    assert [v.id for v in repo.log(remote)] == [second.id, first.id]
    # Again finishes a run that stopped half way.
    repo.expire(remote, first.id)


def test_expire_records_who_took_the_version_out(tmp_path: Path, remote) -> None:
    first, _ = pushed_chain(tmp_path, remote, 2)
    repo.expire(remote, first.id, by="nightly retention", message="Keep the newest 7 versions")
    record = json.loads(remote.get(layout.version_expired(first.id)))
    assert record["by"] == "nightly retention"
    assert record["message"] == "Keep the newest 7 versions"


def test_a_version_a_ref_holds_is_not_expired(tmp_path: Path, remote) -> None:
    (first,) = pushed_chain(tmp_path, remote, 1)
    with pytest.raises(repo.RepoError, match="refs/heads/main"):
        repo.expire(remote, first.id)
    assert not repo.is_expired(remote, first.id)


def test_prune_keeps_the_newest_and_what_refs_hold(tmp_path: Path, remote) -> None:
    versions = pushed_chain(tmp_path, remote, 4)
    refs.write(remote, "refs/tags/v1", versions[0].id, expected=None)

    assert repo.prune(remote, keep=1, grace=dt.timedelta(0)) == [versions[2].id, versions[1].id]
    assert not repo.is_expired(remote, versions[0].id)
    assert repo.prune(remote, keep=1, grace=dt.timedelta(0)) == []


def test_prune_keeps_what_was_made_since_and_within_the_grace_period(tmp_path: Path, remote) -> None:
    versions = pushed_chain(tmp_path, remote, 3)
    assert repo.prune(remote, keep=1) == []
    assert repo.prune(remote, keep=1, since=versions[0].created_at, grace=dt.timedelta(0)) == []
    assert repo.prune(remote, keep=1, since="2999-01-01T00:00:00Z", grace=dt.timedelta(0)) == [
        versions[1].id,
        versions[0].id,
    ]



def files_index(store: LocalStore, version) -> dict:
    data = store.get(layout.version_dir(version.id) + version.tables.path)
    assert len(data) == version.tables.bytes
    return json.loads(data)["tables"]


def test_a_version_indexes_each_tables_files(tmp_path: Path, remote) -> None:
    build(
        tmp_path / "work.sqlite",
        remote,
        "CREATE TABLE lake.main.towns AS SELECT range AS id FROM range(3);"
        "DELETE FROM lake.main.towns WHERE id = 0",
    )
    version = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / version.id, version)

    assert parse(remote.get(layout.version_record(version.id))).tables == version.tables
    (data_file,) = files_index(remote, version)["main.towns"]["files"]
    assert data_file["rows"] == 3
    assert (remote.root / layout.DEFAULT_DATA_DIR / data_file["path"]).stat().st_size == (
        data_file["bytes"]
    )
    assert data_file["deletes"]["rows"] == 1
    assert (remote.root / layout.DEFAULT_DATA_DIR / data_file["deletes"]["path"]).exists()


def test_the_index_leaves_out_dropped_tables_and_marks_renames(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.gone AS SELECT 1 AS id")
    build(tmp_path / "work.sqlite", remote, "DROP TABLE lake.main.gone")
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.moved AS SELECT 1 AS id")
    build(tmp_path / "work.sqlite", remote, "ALTER TABLE lake.main.moved RENAME COLUMN id TO code")
    version = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / version.id, version)

    tables = files_index(remote, version)
    assert sorted(tables) == ["main.moved", "main.towns"]
    assert tables["main.moved"]["renamed_columns"] is True
    assert "renamed_columns" not in tables["main.towns"]


def test_the_index_lists_each_tables_columns(tmp_path: Path, remote) -> None:
    build(
        tmp_path / "work.sqlite",
        remote,
        "CREATE TABLE lake.main.towns AS SELECT 1 AS id, {'p': 'x'} AS s, [1] AS li;"
        "ALTER TABLE lake.main.towns ADD COLUMN n INTEGER DEFAULT 5;"
        "ALTER TABLE lake.main.towns ALTER id SET NOT NULL;"
        "ALTER TABLE lake.main.towns RENAME COLUMN id TO code",
    )
    version = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / version.id, version)

    assert files_index(remote, version)["main.towns"]["columns"] == [
        {"id": 1, "name": "code", "type": "int32", "nullable": False},
        {
            "id": 2,
            "name": "s",
            "type": "struct",
            "nullable": True,
            "children": [{"id": 3, "name": "p", "type": "varchar", "nullable": True}],
        },
        {
            "id": 4,
            "name": "li",
            "type": "list",
            "nullable": True,
            "children": [{"id": 5, "name": "element", "type": "int32", "nullable": True}],
        },
        {"id": 6, "name": "n", "type": "int32", "nullable": True, "default": "5"},
    ]


def pushed(tmp_path: Path, store, parent=None, sql="CREATE TABLE lake.main.towns AS SELECT 1 AS id"):
    build(tmp_path / "work.sqlite", store, sql)
    version = commit(tmp_path, store, parent=parent)
    repo.push(store, tmp_path / "local" / version.id, version)
    return version


def test_push_and_tags_keep_refs_json(tmp_path: Path, remote) -> None:
    first = pushed(tmp_path, remote)
    assert json.loads(remote.get(layout.REFS_INDEX)) == {
        "head": "refs/heads/main",
        "refs": {"refs/heads/main": first.id},
    }

    repo.tag(remote, "2025-final", first.id)
    second = pushed(tmp_path, remote, parent=first, sql="INSERT INTO lake.main.towns VALUES (2)")
    assert json.loads(remote.get(layout.REFS_INDEX))["refs"] == {
        "refs/heads/main": second.id,
        "refs/tags/2025-final": first.id,
    }
    assert repo.tags(remote) == {"2025-final": first.id}
    assert repo.head(remote, "refs/tags/2025-final") == first

    repo.delete_tag(remote, "2025-final")
    assert repo.tags(remote) == {}
    assert json.loads(remote.get(layout.REFS_INDEX))["refs"] == {"refs/heads/main": second.id}


def test_a_tag_is_never_moved(tmp_path: Path, remote) -> None:
    first = pushed(tmp_path, remote)
    second = pushed(tmp_path, remote, parent=first, sql="INSERT INTO lake.main.towns VALUES (2)")
    repo.tag(remote, "v1", first.id)
    repo.tag(remote, "v1", first.id)  # a retry is the same tag
    with pytest.raises(repo.RepoError, match="already there"):
        repo.tag(remote, "v1", second.id)


def test_a_tag_needs_a_version_in_retention(tmp_path: Path, remote) -> None:
    first = pushed(tmp_path, remote)
    pushed(tmp_path, remote, parent=first, sql="INSERT INTO lake.main.towns VALUES (2)")
    repo.expire(remote, first.id)
    with pytest.raises(repo.RepoError, match="out of retention"):
        repo.tag(remote, "old", first.id)
    with pytest.raises(layout.LayoutError):
        repo.tag(remote, "Not ASCII ü", first.id)


def test_a_host_that_keeps_refs_json_itself_does_not_fail_the_push(tmp_path: Path, remote) -> None:
    class Refused(Exception):
        status = 403

    class Host(LocalStore):
        def put(self, key, data, **kwargs):
            if key == layout.REFS_INDEX:
                raise Refused()
            return super().put(key, data, **kwargs)

    host = Host(remote.root)
    version = pushed(tmp_path, host)
    assert repo.head(host) == version
    assert host.head(layout.REFS_INDEX) is None


def test_withdraw_removes_the_data_from_live_versions(tmp_path: Path, remote) -> None:
    first = pushed(tmp_path, remote)
    leaked = set(remote.list("tables/"))
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.kept AS SELECT 1 AS id")
    second = commit(tmp_path, remote, parent=first)
    repo.push(remote, tmp_path / "local" / second.id, second)
    repo.tag(remote, "v2", second.id)

    with pytest.raises(repo.RepoError, match="refs/heads/main"):
        repo.plan_withdrawal(remote, [first.id, second.id])

    # A catalog reaches every file it names: the clean one keeps no snapshot with the data.
    build(tmp_path / "work.sqlite", remote, "DROP TABLE lake.main.towns; CALL ducklake_expire_snapshots('lake', older_than => now())")
    clean = commit(tmp_path, remote, parent=second)
    repo.push(remote, tmp_path / "local" / clean.id, clean)
    kept = set(remote.list("tables/")) - leaked
    assert kept

    plan = repo.withdraw(remote, [first.id, second.id])
    assert plan.tags == ("refs/tags/v2",)
    assert set(plan.deleted) == leaked and set(plan.still_read) == kept
    assert set(remote.list("tables/")) == kept
    assert repo.tags(remote) == {}
    named = {"data": sorted(key.removeprefix("tables/") for key in leaked), "assets": []}
    for version_id in (first.id, second.id):
        mark = json.loads(remote.get(layout.version_expired(version_id)))
        assert mark["reason"] == "withdrawn" and mark["deleted"] == named
        assert remote.head(layout.version_metadata(repo.read_version(remote, version_id))) is None
        assert remote.head(layout.version_record(version_id)) is not None
    assert repo.head(remote) == clean


def test_withdrawing_a_version_past_retention_deletes_unread_files_at_once(tmp_path: Path, remote) -> None:
    first = pushed(tmp_path, remote)
    leaked = set(remote.list("tables/"))
    build(
        tmp_path / "work.sqlite",
        remote,
        "DROP TABLE lake.main.towns; CREATE TABLE lake.main.kept AS SELECT 1 AS id; CALL ducklake_expire_snapshots('lake', older_than => now())",
    )
    second = commit(tmp_path, remote, parent=first)
    repo.push(remote, tmp_path / "local" / second.id, second)
    kept = set(remote.list("tables/")) - leaked
    repo.expire(remote, first.id)
    assert leaked <= set(remote.list("tables/"))  # waiting for the grace period

    plan = repo.withdraw(remote, [first.id])
    assert set(plan.deleted) == leaked
    assert set(remote.list("tables/")) == kept
    assert json.loads(remote.get(layout.version_expired(first.id)))["reason"] == "withdrawn"
    assert repo.head(remote) == second


def test_withdrawing_again_keeps_the_names_the_first_run_deleted(tmp_path: Path, remote) -> None:
    first = pushed(tmp_path, remote)
    leaked = sorted(key.removeprefix("tables/") for key in remote.list("tables/"))
    pushed(tmp_path, remote, parent=first, sql="DROP TABLE lake.main.towns; CREATE TABLE lake.main.kept AS SELECT 1 AS id; CALL ducklake_expire_snapshots('lake', older_than => now())")
    repo.withdraw(remote, [first.id])
    repo.expire(remote, first.id, reason="withdrawn", deleted={"data": [], "assets": []})
    assert json.loads(remote.get(layout.version_expired(first.id)))["deleted"]["data"] == leaked


def test_a_copy_applies_a_withdrawal_by_the_objects_it_names(tmp_path: Path, remote) -> None:
    first = pushed(tmp_path, remote)
    leaked = sorted(key.removeprefix("tables/") for key in remote.list("tables/"))
    # A version made on top in the copy still reads the withdrawn file
    second = pushed(tmp_path, remote, parent=first, sql="INSERT INTO lake.main.towns VALUES (2)")
    stray = "assets/" + "a" * 64
    remote.put(stray, b"copied along")
    named = {"data": leaked, "assets": ["a" * 64]}

    plan = repo.plan_withdrawal(remote, [], objects=named, refuse_refs=False)
    assert set(plan.versions) == {first.id, second.id}
    assert plan.blocked == ("refs/heads/main",)
    with pytest.raises(repo.RepoError, match="refs/heads/main"):
        repo.withdraw(remote, plan.versions, plan=plan)
    with pytest.raises(repo.RepoError, match=r"\.\."):
        repo.plan_withdrawal(remote, [], objects={"data": ["../x/ducklake-a.parquet"]})

    clean = pushed(
        tmp_path,
        remote,
        parent=second,
        sql="DROP TABLE lake.main.towns; CREATE TABLE lake.main.kept AS SELECT 1 AS id; CALL ducklake_expire_snapshots('lake', older_than => now())",
    )
    plan = repo.withdraw(remote, [], plan=repo.plan_withdrawal(remote, [], objects=named))
    assert set(plan.versions) == {first.id, second.id}
    assert remote.head(stray) is None
    assert not {"tables/" + name for name in leaked} & set(remote.list("tables/"))
    mark = json.loads(remote.get(layout.version_expired(second.id)))
    assert mark["deleted"]["assets"] == ["a" * 64] and set(leaked) <= set(mark["deleted"]["data"])
    assert repo.head(remote) == clean


def test_a_file_a_kept_version_still_reads_is_not_deleted(tmp_path: Path, remote) -> None:
    first = pushed(tmp_path, remote)
    second = pushed(tmp_path, remote, parent=first, sql="INSERT INTO lake.main.towns VALUES (2)")
    plan = repo.plan_withdrawal(remote, [first.id])
    assert plan.deleted == () and len(plan.still_read) == 1
    assert repo.head(remote) == second


def _pushed(tmp_path: Path, remote) -> str:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    version = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / version.id, version)
    return version.id


def test_a_table_without_column_statistics_gets_them_in_the_version(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT * FROM (VALUES (1, 'a'), (2, NULL)) t(id, name)")
    frozen = tmp_path / "frozen.duckdb"
    catalog.convert(tmp_path / "work.sqlite", frozen, data_path=data_path(remote))
    # What DuckDB 2.0 leaves after a delete empties a data file: DuckDB 1.5 fails to list the tables.
    con = duckdb.connect(str(frozen))
    con.execute("DELETE FROM ducklake_table_column_stats")
    con.close()
    version = repo.record(frozen, tmp_path / "local", parent=None, author=AUTHOR, message="m", described=declared.Described(
        facts=declared.Facts(), views={}, names={"main.towns"}))
    con = duckdb.connect(str(tmp_path / "local" / version.id / layout.METADATA), read_only=True)
    rows = con.execute(
        "SELECT column_id, contains_null, min_value, max_value FROM ducklake_table_column_stats ORDER BY column_id"
    ).fetchall()
    con.close()
    assert rows == [(1, False, None, None), (2, True, None, None)]


def test_a_table_whose_column_statistics_cannot_be_made_is_not_made_a_version(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    frozen = tmp_path / "frozen.duckdb"
    catalog.convert(tmp_path / "work.sqlite", frozen, data_path=data_path(remote))
    con = duckdb.connect(str(frozen))
    con.execute("DELETE FROM ducklake_table_column_stats")
    con.execute("DELETE FROM ducklake_file_column_stats")
    con.close()
    with pytest.raises(repo.RepoError, match="no column statistics: main.towns"):
        repo.record(frozen, tmp_path / "local", parent=None, author=AUTHOR, message="m", described=declared.Described(
            facts=declared.Facts(), views={}, names={"main.towns"}))
    assert not any((tmp_path / "local").iterdir())


def test_a_metadata_duckdb_cannot_attach_is_not_made_a_version(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    frozen = tmp_path / "frozen.duckdb"
    catalog.convert(tmp_path / "work.sqlite", frozen, data_path=data_path(remote))
    # A table without its columns: DuckLake fails to load the catalog.
    con = duckdb.connect(str(frozen))
    con.execute("DELETE FROM ducklake_column")
    con.close()
    with pytest.raises(repo.RepoError, match="cannot open"):
        repo.record(frozen, tmp_path / "local", parent=None, author=AUTHOR, message="m", described=declared.Described(
            facts=declared.Facts(), views={}, names={"main.towns"}))
    assert not any((tmp_path / "local").iterdir())


def test_clone_refuses_a_catalog_that_is_not_the_one_recorded(tmp_path: Path, remote) -> None:
    version_id = _pushed(tmp_path, remote)
    path = remote.root / layout.version_metadata(repo.read_version(remote, version_id))
    path.write_bytes(path.read_bytes() + b"\0")
    with pytest.raises(repo.RepoError, match="sha256"):
        repo.clone(remote, tmp_path / "again.sqlite")


def test_clone_refuses_a_catalog_naming_files_outside_the_data_folder(tmp_path: Path, remote) -> None:
    import hashlib

    version_id = _pushed(tmp_path, remote)
    path = remote.root / layout.version_metadata(repo.read_version(remote, version_id))
    con = duckdb.connect(str(path))
    con.execute("UPDATE ducklake_data_file SET path = '../../elsewhere/a.parquet'")
    con.close()
    # Recorded as if the writer had made it so: only the paths give it away.
    record = json.loads(remote.get(layout.version_record(version_id)))
    record["metadata"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    record["metadata"]["bytes"] = path.stat().st_size
    (remote.root / layout.version_record(version_id)).write_text(json.dumps(record))
    with pytest.raises(repo.RepoError, match="outside the data folder"):
        repo.clone(remote, tmp_path / "again.sqlite")


def test_a_version_records_its_column_changes(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1::INTEGER AS id, 'a' AS name, 2 AS pop, 3 AS area")
    first = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / first.id, first)
    build(
        tmp_path / "work.sqlite",
        remote,
        "ALTER TABLE lake.main.towns ADD COLUMN note VARCHAR;"
        "ALTER TABLE lake.main.towns RENAME COLUMN name TO town;"
        "ALTER TABLE lake.main.towns DROP COLUMN area;"
        "ALTER TABLE lake.main.towns ALTER id SET TYPE BIGINT;",
    )
    second = commit(tmp_path, remote, parent=first)
    assert first.changes == ()
    assert sorted(second.changes, key=lambda c: c["kind"]) == [
        {"table": "main.towns", "kind": "add_column", "column": "note", "breaking": False},
        {"table": "main.towns", "kind": "change_type", "column": "id", "from_type": "int32", "to_type": "int64", "breaking": False},
        {"table": "main.towns", "kind": "drop_column", "column": "area", "breaking": True},
        {"table": "main.towns", "kind": "rename_column", "column": "town", "from": "name", "breaking": True},
    ]


def test_a_rebuilt_table_is_compared_by_column_name(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id, 2 AS pop")
    first = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / first.id, first)
    build(tmp_path / "work.sqlite", remote, "CREATE OR REPLACE TABLE lake.main.towns AS SELECT 1 AS id, 'x' AS pop")
    second = commit(tmp_path, remote, parent=first)
    assert second.changes == (
        {"table": "main.towns", "kind": "change_type", "column": "pop", "from_type": "int32", "to_type": "varchar", "breaking": True},
    )


def test_a_version_records_what_changed_since_its_parent(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    first = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / first.id, first)
    assert first.diff["tables"] == {"added": ["main.towns"], "dropped": []}

    build(
        tmp_path / "work.sqlite",
        remote,
        "INSERT INTO lake.main.towns VALUES (2); CREATE TABLE lake.main.roads AS SELECT 1 AS id;"
        "CREATE TABLE lake.main.about AS SELECT 'Towns' AS title",
    )
    second = commit(tmp_path, remote, parent=first, facts=declared.Facts(primary_keys={"main.towns": ["id"]}))
    recorded = json.loads((tmp_path / "local" / second.id / layout.VERSION_RECORD).read_text())
    assert recorded["diff"] == {
        "tables": {"added": ["main.about", "main.roads"], "dropped": []},
        "rows": [{"table": "main.towns", "from": 1, "to": 2}],
        "columns": [],
        "data": ["main.towns"],
        "declarations": ["main.towns"],
        "dependencies": [],
    }
    repo.push(remote, tmp_path / "local" / second.id, second)
    # fdl diff reads it from the record.
    assert diff.versions(remote, first, repo.read_version(remote, second.id)) == recorded["diff"]
    # And compares the files of two versions otherwise.
    del recorded["diff"]
    bare = parse(json.dumps(recorded).encode())
    assert diff.versions(remote, first, bare) == recorded_diff(remote, first, bare)


def recorded_diff(remote, first, bare) -> dict:
    found = diff.versions(remote, first, bare)
    assert found["tables"] == {"added": ["main.about", "main.roads"], "dropped": []}
    assert found["declarations"] == ["main.towns"]
    return found


def test_a_record_with_a_path_outside_its_folder_is_refused(tmp_path: Path, remote) -> None:
    version_id = _pushed(tmp_path, remote)
    record = json.loads(remote.get(layout.version_record(version_id)))
    for field, value in (("parents", ["../x"]), ("metadata", {**record["metadata"], "path": "../metadata.db"})):
        with pytest.raises(ValueError):
            parse(json.dumps({**record, field: value}).encode(), expected_id=version_id)


def test_a_record_with_annotations_from_an_earlier_fdl_still_reads(tmp_path: Path, remote) -> None:
    version_id = _pushed(tmp_path, remote)
    record = json.loads(remote.get(layout.version_record(version_id)))
    assert "annotations" not in record
    legacy = parse(json.dumps({**record, "annotations": {"host": {"made_from": "x"}}}).encode(), expected_id=version_id)
    assert legacy == parse(json.dumps(record).encode(), expected_id=version_id)
    assert "annotations" not in json.loads(legacy.to_json())


def test_a_ref_that_does_not_hold_a_version_id_is_refused(remote) -> None:
    remote.put("refs/heads/main", b"../../etc\n")
    with pytest.raises(refs.RefError, match="version ID"):
        refs.read(remote, "refs/heads/main")


def test_collection_leaves_files_that_are_not_shared_objects(tmp_path: Path, remote) -> None:
    import datetime as dt

    _pushed(tmp_path, remote)
    remote.put("tables/README.txt", b"not a data file\n")
    remote.put("assets/notes.txt", b"not an asset\n")
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    plan = repo.plan_collection(remote, grace=dt.timedelta(0), now=later)
    assert "tables/README.txt" not in plan.deleted and "assets/notes.txt" not in plan.deleted


def test_a_version_names_only_the_files_of_its_last_snapshot(tmp_path: Path, remote) -> None:
    import datetime as dt

    # The working catalog keeps the table's first snapshot, but the version
    # keeps only its last, so the first data file is reached by no version.
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    first = {key for key in remote.list("tables/") if key.endswith(".parquet")}
    build(tmp_path / "work.sqlite", remote, "CREATE OR REPLACE TABLE lake.main.towns AS SELECT 2 AS id")
    version = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / version.id, version)
    index = json.loads(remote.get(layout.version_dir(version.id) + layout.TABLES))
    read = {file["path"] for table in index["tables"].values() for file in table["files"]}
    assert "named" not in index

    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    plan = repo.plan_collection(remote, grace=dt.timedelta(0), now=later)
    assert first and first <= set(plan.deleted)
    assert not {"tables/" + key for key in read} & set(plan.deleted)


def test_a_push_sent_again_rewrites_refs_json(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    version = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / version.id, version)
    remote.delete([layout.REFS_INDEX])
    repo.push(remote, tmp_path / "local" / version.id, version)
    assert version.id in remote.get(layout.REFS_INDEX).decode()


def test_a_version_writes_its_declarations_and_views(tmp_path: Path, remote) -> None:
    # Another dataset, which a view reads under the name `census`.
    census = LocalStore(tmp_path / "census")
    repo.init(census)
    build(tmp_path / "census.sqlite", census, "CREATE TABLE lake.main.towns AS SELECT 'a' AS town")
    made = repo.commit(
        tmp_path / "census.sqlite", tmp_path / "census-local", parent=None, data_path=data_path(census),
        author=AUTHOR, message="m",
    )
    repo.push(census, tmp_path / "census-local" / made.id, made)

    build(
        tmp_path / "work.sqlite",
        remote,
        f"ATTACH 'ducklake:{census.root / layout.version_metadata(made)}' AS census (DATA_PATH '{data_path(census)}', READ_ONLY);"
        "ATTACH ':memory:' AS gone; CREATE TABLE gone.main.x AS SELECT 1 AS y;"
        "CREATE TABLE lake.main.reports AS SELECT 1 AS id, 2 AS year, NULL::VARCHAR AS pdf;"
        "CREATE VIEW lake.main.latest AS SELECT id, year FROM lake.main.reports;"
        "CREATE VIEW lake.main.towns AS SELECT town FROM census.main.towns;"
        "CREATE VIEW lake.main.lost AS SELECT y FROM gone.main.x;",
    )
    facts = declared.Facts(
        primary_keys={"main.reports": ["id"]},
        assets={"main.reports": ["pdf"]},
        unprofiled=frozenset({"main.latest"}),
        dependencies={"census": {"url": census.root.as_uri() + "/", "version": made.id}},
    )
    first = commit(tmp_path, remote, facts=facts)
    index = json.loads((tmp_path / "local" / first.id / layout.TABLES).read_text())
    assert first.tables.path == layout.TABLES
    reports = index["tables"]["main.reports"]
    assert (reports["primary_key"], reports["assets"]) == (["id"], ["pdf"])
    latest = index["views"]["main.latest"]
    assert latest["columns"] == [{"name": "id", "type": "INTEGER"}, {"name": "year", "type": "INTEGER"}]
    assert latest["profile"] is False and "main.reports" in latest["sql"]
    # The placeholder DuckLake writes for the metadata's own name is left out.
    assert "DUCKLAKE_CATALOG" not in json.dumps(index)
    assert index["views"]["main.towns"]["columns"] == [{"name": "town", "type": "VARCHAR"}]
    # A view that reads what no dependency holds is listed without its columns.
    assert "columns" not in index["views"]["main.lost"] and "gone" in index["views"]["main.lost"]["sql"]
    assert index["dependencies"] == facts.dependencies
    repo.push(remote, tmp_path / "local" / first.id, first)
    assert repo.declarations(remote, first) == facts


@pytest.mark.parametrize(
    ("facts", "refused"),
    [
        (declared.Facts(primary_keys={"main.reports": ["nope"]}), "the primary key names columns it lacks: nope"),
        (declared.Facts(primary_keys={"main.reports": ["id", "id"]}), "names a column twice"),
        (declared.Facts(primary_keys={"main.v": ["id"]}), "main.v is a view, which has no primary key"),
        (declared.Facts(assets={"main.reports": ["year"]}), "has to be VARCHAR"),
        (declared.Facts(assets={"main.v": ["id"]}), "main.v is a view, which has no asset columns"),
        (declared.Facts(dependencies={"c": {"url": "relative/"}}), "url has to be an absolute URL ending in /"),
        (declared.Facts(dependencies={"Census": {"url": "https://example.org/c/"}}), "a name is lower-case letters"),
        (declared.Facts(dependencies={"temp": {"url": "https://example.org/c/"}}), "not memory, system or temp"),
        (declared.Facts(dependencies={"c": {"url": "https://example.org/c/", "version": "v1"}}), "is not a version ID"),
    ],
)
def test_declarations_the_tables_refuse_are_not_committed(tmp_path: Path, remote, facts, refused: str) -> None:
    build(
        tmp_path / "work.sqlite",
        remote,
        "CREATE TABLE lake.main.reports AS SELECT 1 AS id, 2 AS year;"
        "CREATE VIEW lake.main.v AS SELECT id FROM lake.main.reports;",
    )
    with pytest.raises(declared.DeclarationError, match=re.escape(refused)):
        commit(tmp_path, remote, facts=facts)
    assert not (tmp_path / "local").exists() or not any((tmp_path / "local").iterdir())


def test_a_change_of_dependencies_is_in_the_diff(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    old = declared.Facts(dependencies={"a": {"url": "https://example.org/a/"}, "b": {"url": "https://example.org/b/"}})
    first = commit(tmp_path, remote, facts=old)
    repo.push(remote, tmp_path / "local" / first.id, first)
    new = declared.Facts(dependencies={"a": {"url": "https://example.org/a/", "version": new_id()}, "c": {"url": "https://example.org/c/"}})
    second = commit(tmp_path, remote, parent=first, allow_empty=False, facts=new, inherited=old)
    repo.push(remote, tmp_path / "local" / second.id, second)
    assert second.diff["dependencies"] == ["a", "b", "c"]
    assert diff.versions(remote, first, second)["dependencies"] == ["a", "b", "c"]
    assert "~ dependency b" in diff.lines(second.diff)


@pytest.mark.parametrize(
    ("pinned", "kept"),
    [({"census"}, {"main.towns", "main.mine"}), (set(), {"main.mine"})],
)
def test_a_view_reading_another_dataset_keeps_its_profile_only_when_pinned(pinned, kept) -> None:
    views = {"main.towns": "SELECT * FROM census.main.towns", "main.mine": "SELECT * FROM main.t"}
    found = repo._unchanged_views(views, dict(views), {"main.t"}, set(), dependencies={"census"}, pinned=pinned)
    assert found == kept


def test_declarations_follow_a_renamed_column_and_leave_with_a_dropped_table(tmp_path: Path, remote) -> None:
    working = tmp_path / "work.sqlite"
    build(working, remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id; CREATE TABLE lake.main.roads AS SELECT 1 AS id")
    facts = declared.Facts(primary_keys={"main.towns": ["id"], "main.roads": ["id"]})
    first = commit(tmp_path, remote, facts=facts)
    repo.push(remote, tmp_path / "local" / first.id, first)
    build(working, remote, "ALTER TABLE lake.main.towns RENAME COLUMN id TO code; DROP TABLE lake.main.roads")
    second = commit(tmp_path, remote, parent=first, facts=facts, inherited=facts)
    repo.push(remote, tmp_path / "local" / second.id, second)
    assert repo.declarations(remote, second) == declared.Facts(primary_keys={"main.towns": ["code"]})


def test_a_change_of_declarations_alone_makes_a_version(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    first = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / first.id, first)
    with pytest.raises(repo.NothingToCommit):
        commit(tmp_path, remote, parent=first, allow_empty=False, inherited=declared.Facts())
    keyed = declared.Facts(primary_keys={"main.towns": ["id"]})
    second = commit(tmp_path, remote, parent=first, allow_empty=False, facts=keyed, inherited=declared.Facts())
    assert second.diff["declarations"] == ["main.towns"]
