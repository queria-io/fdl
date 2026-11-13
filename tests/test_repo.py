"""init, commit, push and log against a repository in a local folder.

Real DuckLake catalogs, because what matters is that a reader can attach the
version that comes out the other side.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import duckdb
import pytest

from fdl import catalog, diff, layout, refs, repo
from fdl.stores import LocalStore
from fdl.version import is_id, new_id, parse

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


def commit(tmp_path: Path, store, parent=None, message="m"):
    return repo.commit(
        tmp_path / "work.sqlite",
        tmp_path / "local",
        parent=parent,
        data_path=data_path(store),
        author=AUTHOR,
        message=message,
    )


def read_rows(store: LocalStore, version_id: str) -> list[tuple]:
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(
        f"ATTACH 'ducklake:{store.root / layout.version_catalog(version_id)}' "
        f"AS v (READ_ONLY)"
    )
    rows = con.execute("SELECT * FROM v.main.towns ORDER BY id").fetchall()
    con.close()
    return rows


def test_init_writes_the_config_and_head(remote) -> None:
    config = json.loads(remote.get(layout.CONFIG))
    assert config == {"fdl": "0.1", "id": config["id"], "data_dir": "data/"}
    assert uuid.UUID(config["id"])
    assert remote.get(layout.HEAD) == b"ref: refs/heads/main\n"
    assert repo.head(remote) is None
    # Twice is harmless, and the id stays.
    assert repo.init(remote) == repo.Config(fdl="0.1", data_dir="data/", id=config["id"])


def test_a_repository_without_an_id_still_reads(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "old")
    store.put(layout.CONFIG, b'{"fdl": "0.1", "data_dir": "ducklake.duckdb.files/"}')
    assert repo.read_config(store).id is None


def test_commit_push_and_read_back(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    first = commit(tmp_path, remote, message="first")
    repo.push(remote, tmp_path / "local" / first.id, first)

    assert repo.head(remote) == first
    assert first.seq == 1 and first.parents == ()
    # Whatever DuckDB writes it, the catalog is in the format readers on
    # DuckDB 1.5 open.
    assert first.catalog.storage == "duckdb-v1.5.0"
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
    remote.delete([key for key in remote.list("data/")])

    with pytest.raises(repo.RepoError, match="not in the repository"):
        repo.push(remote, tmp_path / "local" / first.id, first)
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


def test_expire_marks_and_drops_the_catalog_but_keeps_the_record(tmp_path: Path, remote) -> None:
    first, second = pushed_chain(tmp_path, remote, 2)
    remote.put(layout.version_dir(first.id) + "catalogs/sqlite.db", b"x")
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


def test_a_version_a_ref_holds_is_not_expired(tmp_path: Path, remote) -> None:
    (first,) = pushed_chain(tmp_path, remote, 1)
    with pytest.raises(repo.RepoError, match="refs/heads/main"):
        repo.expire(remote, first.id)
    assert not repo.is_expired(remote, first.id)


def test_prune_keeps_the_newest_and_what_refs_hold(tmp_path: Path, remote) -> None:
    versions = pushed_chain(tmp_path, remote, 4)
    refs.write(remote, "refs/tags/v1", versions[0].id, expected=None)

    assert repo.prune(remote, versions[-1], keep=1) == [versions[2].id, versions[1].id]
    assert not repo.is_expired(remote, versions[0].id)
    # Stops at the first expired one, so nothing more to do.
    assert repo.prune(remote, versions[-1], keep=1) == []


def test_prune_keeps_what_was_made_since(tmp_path: Path, remote) -> None:
    versions = pushed_chain(tmp_path, remote, 3)
    assert repo.prune(remote, versions[-1], keep=1, since=versions[0].created_at) == []
    assert repo.prune(remote, versions[-1], keep=1, since="2999-01-01T00:00:00Z") == [
        versions[1].id,
        versions[0].id,
    ]



def files_index(store: LocalStore, version) -> dict:
    data = store.get(layout.version_dir(version.id) + version.files.path)
    assert len(data) == version.files.bytes
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

    assert parse(remote.get(layout.version_record(version.id))).files == version.files
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
    leaked = set(remote.list("data/"))
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
    kept = set(remote.list("data/")) - leaked
    assert kept

    plan = repo.withdraw(remote, [first.id, second.id])
    assert plan.tags == ("refs/tags/v2",)
    assert set(plan.deleted) == leaked and set(plan.still_read) == kept
    assert set(remote.list("data/")) == kept
    assert repo.tags(remote) == {}
    for version_id in (first.id, second.id):
        assert json.loads(remote.get(layout.version_expired(version_id)))["reason"] == "withdrawn"
        assert remote.head(layout.version_catalog(version_id)) is None
        assert remote.head(layout.version_record(version_id)) is not None
    assert repo.head(remote) == clean


def test_withdrawing_a_version_past_retention_deletes_unread_files_at_once(tmp_path: Path, remote) -> None:
    first = pushed(tmp_path, remote)
    leaked = set(remote.list("data/"))
    build(
        tmp_path / "work.sqlite",
        remote,
        "DROP TABLE lake.main.towns; CREATE TABLE lake.main.kept AS SELECT 1 AS id; CALL ducklake_expire_snapshots('lake', older_than => now())",
    )
    second = commit(tmp_path, remote, parent=first)
    repo.push(remote, tmp_path / "local" / second.id, second)
    kept = set(remote.list("data/")) - leaked
    repo.expire(remote, first.id)
    assert leaked <= set(remote.list("data/"))  # waiting for the grace period

    plan = repo.withdraw(remote, [first.id])
    assert set(plan.deleted) == leaked
    assert set(remote.list("data/")) == kept
    assert json.loads(remote.get(layout.version_expired(first.id)))["reason"] == "withdrawn"
    assert repo.head(remote) == second


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


def test_checkout_refuses_a_catalog_that_is_not_the_one_recorded(tmp_path: Path, remote) -> None:
    version_id = _pushed(tmp_path, remote)
    path = remote.root / layout.version_catalog(version_id)
    path.write_bytes(path.read_bytes() + b"\0")
    with pytest.raises(repo.RepoError, match="sha256"):
        repo.checkout(remote, tmp_path / "again.sqlite")


def test_checkout_refuses_a_catalog_naming_files_outside_the_data_folder(tmp_path: Path, remote) -> None:
    import hashlib

    version_id = _pushed(tmp_path, remote)
    path = remote.root / layout.version_catalog(version_id)
    con = duckdb.connect(str(path))
    con.execute("UPDATE ducklake_data_file SET path = '../../elsewhere/a.parquet'")
    con.close()
    # Recorded as if the writer had made it so: only the paths give it away.
    record = json.loads(remote.get(layout.version_record(version_id)))
    record["catalog"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    record["catalog"]["bytes"] = path.stat().st_size
    (remote.root / layout.version_record(version_id)).write_text(json.dumps(record))
    with pytest.raises(repo.RepoError, match="outside the data folder"):
        repo.checkout(remote, tmp_path / "again.sqlite")


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
    def described(title: str) -> dict:
        return {
            "name": "towns",
            "title": "Towns",
            "tables": [{"schema": "main", "name": "towns", "fields": [{"name": "id", "title": title}]}],
        }

    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    first = repo.commit(
        tmp_path / "work.sqlite", tmp_path / "local", parent=None, data_path=data_path(remote),
        author=AUTHOR, message="m", dataset=described("ID"),
    )
    repo.push(remote, tmp_path / "local" / first.id, first)
    assert first.diff["tables"] == {"added": ["main.towns"], "dropped": []}

    build(
        tmp_path / "work.sqlite",
        remote,
        "INSERT INTO lake.main.towns VALUES (2); CREATE TABLE lake.main.roads AS SELECT 1 AS id",
    )
    parent_dataset = json.loads(remote.get(layout.version_dir(first.id) + layout.DATASET))
    second = repo.commit(
        tmp_path / "work.sqlite", tmp_path / "local", parent=first, data_path=data_path(remote),
        author=AUTHOR, message="m", dataset={**described("Town ID"), "tables": described("Town ID")["tables"]
        + [{"schema": "main", "name": "roads"}]}, parent_dataset=parent_dataset,
    )
    recorded = json.loads((tmp_path / "local" / second.id / layout.VERSION_RECORD).read_text())
    assert recorded["diff"] == {
        "tables": {"added": ["main.roads"], "dropped": []},
        "rows": [{"table": "main.towns", "from": 1, "to": 2}],
        "columns": [],
        "data": ["main.towns"],
        "descriptions": ["main.towns.id"],
    }
    repo.push(remote, tmp_path / "local" / second.id, second)
    # fdl diff reads it from the record.
    assert diff.versions(remote, first, repo.read_version(remote, second.id)) == recorded["diff"]


def test_a_record_with_a_path_outside_its_folder_is_refused(tmp_path: Path, remote) -> None:
    version_id = _pushed(tmp_path, remote)
    record = json.loads(remote.get(layout.version_record(version_id)))
    for field, value in (("parents", ["../x"]), ("catalog", {**record["catalog"], "path": "../ducklake.duckdb"})):
        with pytest.raises(ValueError):
            parse(json.dumps({**record, field: value}).encode(), expected_id=version_id)


def test_a_ref_that_does_not_hold_a_version_id_is_refused(remote) -> None:
    remote.put("refs/heads/main", b"../../etc\n")
    with pytest.raises(refs.RefError, match="version ID"):
        refs.read(remote, "refs/heads/main")


def test_collection_leaves_files_that_are_not_shared_objects(tmp_path: Path, remote) -> None:
    import datetime as dt

    _pushed(tmp_path, remote)
    remote.put("data/README.txt", b"not a data file\n")
    remote.put("assets/notes.txt", b"not an asset\n")
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    plan = repo.plan_collection(remote, grace=dt.timedelta(0), now=later)
    assert "data/README.txt" not in plan.deleted and "assets/notes.txt" not in plan.deleted


def test_collection_keeps_files_only_an_older_snapshot_of_the_catalog_names(tmp_path: Path, remote) -> None:
    import datetime as dt

    # The working catalog keeps the table's first snapshot, so the catalog names
    # its first data file though the version reads only the second.
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    build(tmp_path / "work.sqlite", remote, "CREATE OR REPLACE TABLE lake.main.towns AS SELECT 2 AS id")
    version = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / version.id, version)
    index = json.loads(remote.get(layout.version_dir(version.id) + layout.FILES))
    read = {file["path"] for table in index["tables"].values() for file in table["files"]}
    assert set(index["named"]) > read

    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    plan = repo.plan_collection(remote, grace=dt.timedelta(0), now=later)
    assert not {"data/" + key for key in index["named"]} & set(plan.deleted)


def test_collection_reads_the_catalog_for_a_files_json_without_named(tmp_path: Path, remote) -> None:
    import datetime as dt

    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    build(tmp_path / "work.sqlite", remote, "CREATE OR REPLACE TABLE lake.main.towns AS SELECT 2 AS id")
    version = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / version.id, version)
    key = layout.version_dir(version.id) + layout.FILES
    index = json.loads(remote.get(key))
    # As written before `named`. The record's sha256 no longer matches, which collect does not check.
    remote.put(key, json.dumps({"tables": index["tables"]}).encode())

    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    plan = repo.plan_collection(remote, grace=dt.timedelta(0), now=later)
    assert not {"data/" + key for key in index["named"]} & set(plan.deleted)


def test_a_push_sent_again_rewrites_refs_json(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    version = commit(tmp_path, remote)
    repo.push(remote, tmp_path / "local" / version.id, version)
    remote.delete([layout.REFS_INDEX])
    repo.push(remote, tmp_path / "local" / version.id, version)
    assert version.id in remote.get(layout.REFS_INDEX).decode()
