"""init, commit, push and log against a repository in a local folder.

Real DuckLake catalogs, because what matters is that a reader can attach the
version that comes out the other side.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from fdl import catalog, layout, refs, repo
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
    assert json.loads(remote.get(layout.CONFIG)) == {"fdl": "0.1", "data_dir": "data/"}
    assert remote.get(layout.HEAD) == b"ref: refs/heads/main\n"
    assert repo.head(remote) is None
    # Twice is harmless.
    assert repo.init(remote).data_dir == "data/"


def test_commit_push_and_read_back(tmp_path: Path, remote) -> None:
    build(tmp_path / "work.sqlite", remote, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    first = commit(tmp_path, remote, message="first")
    repo.push(remote, tmp_path / "local" / first.id, first)

    assert repo.head(remote) == first
    assert first.seq == 1 and first.parents == ()
    assert first.catalog.storage.startswith("duckdb-v")
    assert not first.catalog.storage.endswith("+")
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
