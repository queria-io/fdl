"""The operations on a repository, with no terminal and no service behind them.

Each takes a `Store` for the remote and paths for the local side, and returns
what it did. Printing, prompts and credentials belong to whoever calls these.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import shutil
import tempfile
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Mapping

from . import catalog, layout, objects, refs
from . import dataset as dataset_json
from .store import Store, is_conflict, is_missing
from .version import Catalog, DuckLake, File, Version, VersionError, new_id, parse, utc_now


class RepoError(RuntimeError):
    """The repository cannot do what was asked, and nothing was changed."""


class NotFastForward(RepoError):
    """The version is not built on what the ref holds now. Pull and build again."""


class NothingToCommit(RepoError):
    """The working catalog holds what its parent holds, so no version is made."""

    def __init__(self, parent: Version) -> None:
        super().__init__(f"nothing changed since {parent.id} (version {parent.seq})")
        self.parent = parent


class CannotCompare(RepoError):
    """Whether anything changed could not be told, so nothing was made either way."""


@dataclass(frozen=True)
class Config:
    fdl: str
    data_dir: str
    #: Never changes, so a copy of the repository can tell it holds the same one.
    #: None for repositories started before it was written.
    id: str | None = None


def init(store: Store, *, data_dir: str = layout.DEFAULT_DATA_DIR) -> Config:
    """Start a repository: `fdl.json` and HEAD, and no versions yet.

    An existing repository is left as it is and its settings returned, so
    running this twice is harmless.
    """
    if not data_dir.endswith("/"):
        raise RepoError(f"data_dir has to end with '/': {data_dir!r}")
    record = {"fdl": layout.FDL_VERSION, "id": str(uuid.uuid4()), "data_dir": data_dir}
    try:
        store.put(
            layout.CONFIG,
            (json.dumps(record, indent=2) + "\n").encode(),
            content_type="application/json",
            if_none_match="*",
        )
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if not is_conflict(exc):
            raise
    refs.write_head(store)
    return read_config(store)


def read_config(store: Store) -> Config:
    try:
        record = json.loads(store.get(layout.CONFIG))
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if is_missing(exc):
            raise RepoError("not an FDL repository (no fdl.json)") from exc
        raise
    major = str(record.get("fdl", "")).split(".")[0]
    if major != layout.FDL_VERSION.split(".")[0]:
        raise RepoError(f"written for fdl {record.get('fdl')}, this reads {layout.FDL_VERSION}")
    return Config(
        fdl=record["fdl"],
        data_dir=record.get("data_dir", layout.DEFAULT_DATA_DIR),
        id=record.get("id"),
    )


def read_version(store: Store, version_id: str) -> Version:
    try:
        data = store.get(layout.version_record(version_id))
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if is_missing(exc):
            raise RepoError(f"no version {version_id}") from exc
        raise
    return parse(data, expected_id=version_id)


def head(store: Store, name: str = layout.HEAD) -> Version | None:
    """The version a ref holds, or ``None`` before the first push."""
    _, version_id = refs.resolve(store, name)
    return read_version(store, version_id) if version_id else None


def readable(store: Store, name: str = layout.HEAD) -> Version:
    """The version a ref holds, checked the way a reader must (Reading, steps 1-3).

    Raises `RepoError` before the first version, for a record of a format this
    does not read, and for a version past retention or withdrawn.
    """
    try:
        version = head(store, name)
    except VersionError as exc:
        raise RepoError(str(exc)) from exc
    if version is None:
        raise RepoError(f"{name} holds no version yet")
    if is_expired(store, version.id):
        raise RepoError(f"{version.id} is past retention or withdrawn and has no catalog")
    return version


def commit(
    working: Path,
    out_dir: Path,
    *,
    parent: Version | None,
    data_path: str,
    author: dict,
    message: str,
    inputs: tuple[dict, ...] = (),
    annotations: dict | None = None,
    dataset: dict | None = None,
    tree: list[dict] | None = None,
    allow_empty: bool = True,
    working_data: str | None = None,
) -> Version:
    """Freeze the working catalog into a version under ``out_dir/<ID>/``.

    ``dataset`` is the writer's description (`dataset.json`), checked against
    the frozen catalog and completed from it. ``tree`` lists the version's
    other files (`tree.json`: path, sha256, bytes), such as its README.

    ``allow_empty=False`` refuses a version that would hold what ``parent``
    holds: the same tables, columns, rows and views, and the same
    `dataset.json`. It raises `NothingToCommit`, or `CannotCompare` when the
    rows could not be read to tell.

    ``data_path`` is where readers find the data files (the remote's root and
    data_dir), written into the frozen catalog. Every file reference must be
    relative to it, or the version would point readers at the writer's disk.
    Nothing is uploaded: that is `push`.

    ``working_data`` is where the working catalog wrote its new data files,
    when not where it records (see `checkout`): comparing reads them there.
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    frozen = Path(out_dir) / f".freezing-{new_id()}.duckdb"
    try:
        catalog.convert(working, frozen, data_path=data_path)
        catalog.set_data_path(frozen, data_path)
        # Files a working catalog read from the repository where they are.
        catalog.unpin_files(frozen, data_path)
        absolute = catalog.absolute_paths(frozen)
        if absolute:
            raise RepoError(
                f"{len(absolute)} file(s) are recorded with an absolute path "
                f"(first: {absolute[0].path}); rebuild so they sit under the data path"
            )
        # The description first: it reads the catalog alone, while comparing
        # the contents can read every row of a rebuilt table.
        if (
            not allow_empty
            and parent is not None
            and _same_description(frozen, dataset, parent)
            and _same_tree(tree, parent)
        ):
            try:
                unchanged = catalog.same_contents(
                    working, parent.ducklake.snapshot_id, data_path=working_data
                )
            except catalog.CatalogError as exc:
                raise CannotCompare(str(exc)) from exc
            if unchanged:
                raise NothingToCommit(parent)
        return record(
            frozen,
            out_dir,
            parent=parent,
            author=author,
            message=message,
            inputs=inputs,
            annotations=annotations,
            dataset=dataset,
            tree=tree,
        )
    finally:
        frozen.unlink(missing_ok=True)


def _same_description(frozen: Path, dataset: dict | None, parent: Version) -> bool:
    if dataset is None or parent.dataset is None:
        return dataset is None and parent.dataset is None
    written = dataset_json.to_json(dataset_json.describe(dataset, frozen))
    return hashlib.sha256(written).hexdigest() == parent.dataset.sha256


def _same_tree(tree: list[dict] | None, parent: Version) -> bool:
    if not tree or parent.tree is None:
        return not tree and parent.tree is None
    return hashlib.sha256(_tree_json(tree)).hexdigest() == parent.tree.sha256


def _tree_json(tree: list[dict]) -> bytes:
    objects.check_tree(tree)
    entries = sorted(tree, key=lambda item: item["path"])
    return (json.dumps({"entries": entries}, ensure_ascii=False, indent=2) + "\n").encode()


def checkout(
    store: Store,
    working: Path,
    *,
    ref: str = layout.HEAD,
    local_data: Path | None = None,
) -> Version | None:
    """Make a working catalog from the version ``ref`` holds.

    Before the first version there is nothing to copy, and an empty working
    catalog is created instead. Returns the version checked out, which a
    commit from this working catalog builds on.

    A store DuckDB cannot write to (one over HTTP) needs ``local_data``, a
    folder on this machine: new data files are written there and uploaded by
    `push`, while the files already in the repository are read where they are.
    """
    working = Path(working)
    config = read_config(store)
    remote_data = _data_url(store, config)
    if writes_data_files(store):
        data_path = remote_data
    elif local_data is None:
        raise RepoError("this store takes no data files from DuckDB; give a local folder for them")
    else:
        Path(local_data).mkdir(parents=True, exist_ok=True)
        data_path = Path(local_data).resolve().as_posix() + "/"
    version = head(store, ref)
    if version is None:
        catalog.create(working, alias="lake", data_path=data_path)
        return None
    if is_expired(store, version.id):
        raise RepoError(f"{version.id} is past retention and has no catalog")
    working.parent.mkdir(parents=True, exist_ok=True)
    frozen = working.with_name(f".{working.name}.{version.id}.duckdb")
    try:
        frozen.write_bytes(store.get(layout.version_catalog(version.id)))
        if data_path != remote_data:
            catalog.pin_files(frozen, remote_data)
        catalog.convert(frozen, working, data_path=data_path)
    finally:
        frozen.unlink(missing_ok=True)
    return version


def writes_data_files(store: Store) -> bool:
    """Whether DuckDB writes data files straight into the store."""
    return getattr(store, "writes_data_files", True)


def data_url(store: Store) -> str:
    """Where readers and writers find the data files: the root and data_dir."""
    return _data_url(store, read_config(store))


def _data_url(store: Store, config: Config) -> str:
    root = getattr(store, "url", None)
    if not root:
        raise RepoError("this store does not say where it is, so data files cannot be placed")
    return root.rstrip("/") + "/" + config.data_dir


def record(
    frozen: Path,
    out_dir: Path,
    *,
    parent: Version | None,
    author: dict,
    message: str,
    inputs: tuple[dict, ...] = (),
    annotations: dict | None = None,
    dataset: dict | None = None,
    tree: list[dict] | None = None,
) -> Version:
    """Make a version out of a catalog that is already frozen.

    For a writer that freezes the catalog itself (with checks of its own) and
    only needs the version around it. The catalog is copied, not moved.
    """
    if not author.get("name"):
        raise RepoError("a version needs an author name")
    version_id = new_id()
    target = Path(out_dir) / version_id
    target.mkdir(parents=True)
    copied = target / layout.CATALOG
    try:
        shutil.copyfile(frozen, copied)
        recorded = catalog.recorded_format(copied)
        if not recorded:
            raise RepoError(f"{frozen} records no DuckLake format")
        first, last = catalog.snapshot_range(
            copied, parent.ducklake.snapshot_id if parent else None
        )
        described = None
        if dataset is not None:
            written = target / layout.DATASET
            written.write_bytes(dataset_json.to_json(dataset_json.describe(dataset, copied)))
            described = File(
                path=layout.DATASET, bytes=written.stat().st_size, sha256=_sha256(written)
            )
        indexed = _write_files_index(target, copied, last)
        listed = None
        if tree:
            written = target / layout.TREE
            written.write_bytes(_tree_json(tree))
            listed = File(path=layout.TREE, bytes=written.stat().st_size, sha256=_sha256(written))
        version = Version(
            id=version_id,
            seq=parent.seq + 1 if parent else 1,
            parents=(parent.id,) if parent else (),
            created_at=utc_now(),
            author=dict(author),
            message=message,
            catalog=Catalog(
                path=layout.CATALOG,
                storage=catalog.storage_version(copied),
                bytes=copied.stat().st_size,
                sha256=_sha256(copied),
            ),
            ducklake=DuckLake(format=recorded, snapshot_id=last, snapshots=(first, last)),
            files=indexed,
            inputs=tuple(inputs),
            annotations=dict(annotations or {}),
            dataset=described,
            tree=listed,
        )
        (target / layout.VERSION_RECORD).write_bytes(version.to_json())
    except BaseException:
        # Whatever stopped it (a full disk included), a half-made version is a
        # copy of the catalog that nothing will ever read.
        shutil.rmtree(target, ignore_errors=True)
        raise
    return version


def _write_files_index(target: Path, frozen: Path, snapshot_id: int) -> File:
    """Write `files.json`: each table's files at the frozen snapshot, from the catalog."""
    written = target / layout.FILES
    index = {"tables": catalog.table_files(frozen, snapshot_id)}
    written.write_bytes((json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode())
    return File(path=layout.FILES, bytes=written.stat().st_size, sha256=_sha256(written))


def push(
    store: Store,
    local: Path,
    version: Version,
    *,
    ref: str = layout.DEFAULT_BRANCH,
    move_ref: bool = True,
    local_data: Path | None = None,
    assets: Iterable[str] = (),
    local_objects: Mapping[str, Path] | None = None,
) -> None:
    """Upload a committed version, then move ``ref`` to it.

    In the order the format asks for: the data files must already be there,
    then the catalog, then `version.json`, and the ref last. A push stopped
    half way leaves files no ref points at, which readers never see.

    ``move_ref=False`` uploads the version and stops: for a host that moves
    its refs itself (after an approval, say).

    ``local_data`` is the folder a working catalog wrote new data files to
    (see `checkout`). Those the repository lacks are uploaded first.

    ``assets`` are the sha256 the version's asset columns hold (see
    `objects.asset_values`). They and the files its `tree.json` lists must
    be in the repository; those it lacks are uploaded from
    ``local_objects`` (sha256 to a file with that content), and a push that
    still lacks one is refused.
    """
    config = read_config(store)
    current_ref = refs.read(store, layout.check_ref(ref))
    if current_ref is not None and current_ref.target == version.id:
        # This push again, after a run whose answer was lost.
        return
    _refuse_unless_fast_forward(current_ref, version)

    frozen = Path(local) / layout.CATALOG
    if local_data is not None:
        _upload_data_files(store, config, frozen, Path(local_data))
    missing = _missing_data_files(store, config, frozen)
    if missing:
        raise RepoError(
            f"{len(missing)} data file(s) the catalog names are not in the "
            f"repository (first: {missing[0]}). Rebuild from the working "
            f"catalog and commit again."
        )
    _upload_objects(store, _needed_objects(Path(local), version, assets), local_objects or {})
    upload(store, local, version)
    if not move_ref:
        return
    try:
        refs.write(store, ref, version.id, expected=current_ref)
    except refs.RefConflict as exc:
        # A write sent again after a lost answer is refused by its own first
        # attempt. That moved the ref to this version, which is what was asked.
        now = refs.read(store, ref)
        if now is None or now.target != version.id:
            raise NotFastForward(f"{ref} moved during the push. Pull and build again.") from exc
    update_refs_index(store)


def tag(store: Store, name: str, version_id: str) -> None:
    """Create ``refs/tags/<name>`` at a version. Tags are created, never moved."""
    ref = layout.check_ref(f"refs/tags/{name}")
    read_version(store, version_id)
    if is_expired(store, version_id):
        raise RepoError(f"{version_id} is out of retention, so it cannot be tagged")
    try:
        refs.write(store, ref, version_id, expected=None)
    except refs.RefConflict as exc:
        now = refs.read(store, ref)
        # A retry whose first answer was lost is not a second tag.
        if now is None or now.target != version_id:
            raise RepoError(f"{ref} is already there, at {now.target if now else '?'}") from exc
    update_refs_index(store)


def delete_tag(store: Store, name: str) -> None:
    """Delete ``refs/tags/<name>``. One already gone is not an error."""
    refs.delete(store, f"refs/tags/{name}")
    update_refs_index(store)


def tags(store: Store) -> dict[str, str]:
    """Each tag's name (after ``refs/tags/``) and the version it holds."""
    prefix = "refs/tags/"
    return {name[len(prefix) :]: target for name, target in refs.listed(store).items() if name.startswith(prefix)}


def update_refs_index(store: Store) -> None:
    """Rewrite `refs.json` after moving a ref.

    A host that checks pushes and moves refs itself keeps `refs.json` too, and
    may refuse a writer's (403). That is not an error: the ref has moved.
    """
    try:
        refs.write_index(store)
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if getattr(exc, "status", None) == 403:
            return
        warnings.warn(f"the ref moved, but refs.json was not rewritten: {exc}", stacklevel=2)


def upload(store: Store, local: Path, version: Version) -> None:
    """Put a committed version's files, catalog first and the record last.

    No checks and no ref: for a host that checks and moves its refs itself.
    """
    local = Path(local)
    _put_once(store, layout.version_catalog(version.id), local / layout.CATALOG)
    # Before the record, which is what makes a version there at all.
    if version.dataset is not None:
        _put_once(store, layout.version_dir(version.id) + version.dataset.path, local / version.dataset.path)
    if version.files is not None:
        _put_once(store, layout.version_dir(version.id) + version.files.path, local / version.files.path)
    if version.tree is not None:
        _put_once(store, layout.version_dir(version.id) + version.tree.path, local / version.tree.path)
    _put_once(store, layout.version_record(version.id), local / layout.VERSION_RECORD)


def log(store: Store, name: str = layout.HEAD) -> Iterator[Version]:
    """Versions from the one ``name`` holds back to the first, by first parent."""
    current = head(store, name)
    while current is not None:
        yield current
        current = read_version(store, current.parents[0]) if current.parents else None


def is_ancestor(store: Store, ancestor_id: str, descendant: Version) -> bool:
    """Whether ``ancestor_id`` is ``descendant`` or behind it.

    Stops once ``seq`` falls below the ancestor's, so a check costs the
    distance between the two rather than the whole history.
    """
    target = read_version(store, ancestor_id)
    pending = [descendant]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current.id == ancestor_id:
            return True
        if current.id in seen or current.seq <= target.seq:
            continue
        seen.add(current.id)
        pending.extend(read_version(store, parent) for parent in current.parents)
    return False


def is_expired(store: Store, version_id: str) -> bool:
    return store.head(layout.version_expired(version_id)) is not None


def expire(store: Store, version_id: str, *, reason: str = "retention") -> None:
    """Take a version out of retention: the mark first, then its catalogs.

    `version.json` stays, so the history still says the version was there and
    what it was. Data files are not touched: other versions share them, and
    which ones may go is for a pass over the whole repository to decide.
    Running it again finishes a run that stopped between the two steps.
    """
    read_version(store, version_id)
    held = _held(store)
    if version_id in held:
        raise RepoError(f"{held[version_id]} points at {version_id}, so it is kept")
    record = {"expired_at": utc_now(), "reason": reason}
    try:
        store.put(
            layout.version_expired(version_id),
            (json.dumps(record) + "\n").encode(),
            content_type="application/json",
            if_none_match="*",
        )
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if not is_conflict(exc):
            raise
        # A version already out of retention that is withdrawn says so, for
        # mirrors to delete what the origin deleted.
        mark = layout.version_expired(version_id)
        if reason == "withdrawn" and json.loads(store.get(mark)).get("reason") != reason:
            store.put(mark, (json.dumps(record) + "\n").encode(), content_type="application/json")
    store.delete(
        [
            layout.version_catalog(version_id),
            # The index names files the grace period may delete.
            layout.version_dir(version_id) + layout.FILES,
            layout.version_dir(version_id) + layout.TREE,
            *store.list(layout.version_dir(version_id) + layout.CATALOGS_DIR),
        ]
    )


@dataclass(frozen=True)
class Withdrawal:
    """What withdrawing versions does, worked out before anything is changed."""

    versions: tuple[str, ...]
    #: Tags that point at the versions, deleted first.
    tags: tuple[str, ...]
    #: Data files the versions' catalogs name and no version kept reads.
    deleted: tuple[str, ...]
    #: Data files the versions name that a kept version still reads. Kept: if
    #: they hold the data, the version that reads them has to be withdrawn too.
    still_read: tuple[str, ...]


def plan_withdrawal(store: Store, version_ids: Iterable[str]) -> Withdrawal:
    """Work out a withdrawal (see `withdraw`) without changing anything.

    Refused while a branch or a request still points at one of the versions:
    the first step, a new version without the data, is the writer's.
    """
    config = read_config(store)
    affected = tuple(dict.fromkeys(version_ids))
    for version_id in affected:
        read_version(store, version_id)
    tags: list[str] = []
    for name in store.list("refs/"):
        try:
            ref = refs.read(store, name)
        except (refs.RefError, layout.LayoutError):
            continue
        if ref is None or ref.target not in affected:
            continue
        if not name.startswith("refs/tags/"):
            raise RepoError(
                f"{name} points at {ref.target}. Move it to a version without the data first"
            )
        tags.append(name)

    candidates: set[str] = set()
    kept: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="fdl-") as tmp:
        for version_id in _version_ids(store):
            if version_id in affected:
                local = _fetch_catalog(store, version_id, Path(tmp))
                if local is not None:
                    # Every file the catalog has ever named: earlier snapshots
                    # in it may hold the data too.
                    candidates.update(catalog.data_files(local))
                    local.unlink()
                continue
            if is_expired(store, version_id):
                continue
            version = read_version(store, version_id)
            # files.json lists what the version reads, without its catalog.
            indexed = _indexed_files(store, version)
            if indexed is not None:
                kept.update(indexed)
                continue
            local = _fetch_catalog(store, version_id, Path(tmp))
            if local is not None:
                kept.update(_read_files(local, version))
                local.unlink()
    return Withdrawal(
        versions=affected,
        tags=tuple(tags),
        deleted=tuple(config.data_dir + key for key in sorted(candidates - kept)),
        still_read=tuple(config.data_dir + key for key in sorted(candidates & kept)),
    )


def withdraw(store: Store, version_ids: Iterable[str], *, plan: Withdrawal | None = None) -> Withdrawal:
    """Remove versions that hold data that must not be published.

    Unlike retention it takes live versions and deletes their data files at
    once. In the spec's order, except that the data files go before the
    catalogs, so a run that stops half way can be run again while the
    catalogs still say which files they are. `version.json` stays.
    """
    plan = plan or plan_withdrawal(store, version_ids)
    for name in plan.tags:
        refs.delete(store, name)
    if plan.tags:
        update_refs_index(store)
    store.delete(list(plan.deleted))
    for version_id in plan.versions:
        expire(store, version_id, reason="withdrawn")
    return plan


def _version_ids(store: Store) -> list[str]:
    suffix = "/" + layout.VERSION_RECORD
    return [key[len("versions/") : -len(suffix)] for key in store.list("versions/") if key.endswith(suffix)]


def _fetch_catalog(store: Store, version_id: str, tmp: Path) -> Path | None:
    """The version's catalog in ``tmp``, or ``None`` when it is gone."""
    local = tmp / f"{version_id}.duckdb"
    try:
        local.write_bytes(store.get(layout.version_catalog(version_id)))
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if is_missing(exc):
            return None
        raise
    return local


def _indexed_files(store: Store, version: Version) -> set[str] | None:
    """What the version's `files.json` says it reads, or ``None`` without one."""
    if version.files is None:
        return None
    try:
        index = json.loads(store.get(layout.version_dir(version.id) + version.files.path))
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if is_missing(exc):
            return None
        raise
    return _paths_of(index["tables"].values())


def _read_files(local: Path, version: Version) -> set[str]:
    """The data and delete files a reader of the version reads, at its snapshot."""
    return _paths_of(catalog.table_files(local, version.ducklake.snapshot_id).values())


def _paths_of(tables: Iterable[dict]) -> set[str]:
    files = [file for table in tables for file in table["files"]]
    return {file["path"] for file in files} | {file["deletes"]["path"] for file in files if "deletes" in file}


def prune(
    store: Store,
    newest: Version,
    *,
    keep: int = 1,
    since: str | None = None,
    reason: str = "retention",
) -> list[str]:
    """Expire the versions behind ``newest`` that fall out of retention.

    Kept are the ``keep`` newest on its first-parent line (``newest`` among
    them), every one created at or after ``since`` (an ISO time, as
    ``created_at`` is written), and any a ref points at. The walk stops at
    the first version already expired: an earlier prune took everything past
    it. Returns the IDs expired, newest first.
    """
    if keep < 1:
        raise ValueError(f"keep must be >= 1, got {keep}")
    held = _held(store)
    expired: list[str] = []
    current: Version | None = newest
    for position in itertools.count():
        if current is None or (position > 0 and is_expired(store, current.id)):
            break
        recent = since is not None and _instant(current.created_at) >= _instant(since)
        if position >= keep and not recent and current.id not in held:
            expire(store, current.id, reason=reason)
            expired.append(current.id)
        current = read_version(store, current.parents[0]) if current.parents else None
    return expired


def _held(store: Store) -> dict[str, str]:
    """The versions some ref points at, each with one ref that does."""
    held: dict[str, str] = {}
    for name in store.list("refs/"):
        try:
            ref = refs.read(store, name)
        except (refs.RefError, layout.LayoutError):
            continue
        if ref is not None:
            held.setdefault(ref.target, name)
    return held


def _instant(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _refuse_unless_fast_forward(current: refs.Ref | None, version: Version) -> None:
    if current is None:
        if version.parents:
            raise NotFastForward(
                f"{version.id} is built on {version.parents[0]}, but the branch "
                f"has no version yet"
            )
        return
    if current.target not in version.parents:
        raise NotFastForward(
            f"{current.name} is at {current.target}, which {version.id} is not "
            f"built on. Pull and build again."
        )


def _missing_data_files(store: Store, config: Config, frozen: Path) -> list[str]:
    present = set(store.list(config.data_dir))
    return [
        key
        for key in (config.data_dir + name for name in catalog.data_files(frozen))
        if key not in present
    ]


def _upload_data_files(store: Store, config: Config, frozen: Path, local_data: Path) -> None:
    files = [
        (key, local_data / key[len(config.data_dir) :])
        for key in _missing_data_files(store, config, frozen)
    ]
    files = [(key, path) for key, path in files if path.is_file()]
    # A host that hands out upload URLs takes them straight to storage.
    put_files = getattr(store, "put_files", None)
    if put_files is not None:
        put_files(files, content_type="application/octet-stream")
        return
    for key, path in files:
        try:
            store.put_file(key, path, content_type="application/octet-stream", if_none_match="*")
        except Exception as exc:  # noqa: BLE001 - stores raise their own types
            # Data files are never rewritten: one already there is this one.
            if not is_conflict(exc):
                raise


def _needed_objects(local: Path, version: Version, assets: Iterable[str]) -> dict[str, str]:
    """The assets a version reaches: store key to media type, where it is known."""
    needed: dict[str, str] = {}
    if version.tree is not None:
        listed = json.loads((local / version.tree.path).read_text(encoding="utf-8"))
        for item in listed["entries"]:
            needed[objects.asset_key(item["sha256"])] = objects.media_type(item["path"])
    for sha256 in assets:
        needed.setdefault(objects.asset_key(sha256), "")
    return needed


def _upload_objects(store: Store, needed: dict[str, str], local: Mapping[str, Path]) -> None:
    """Put the assets the repository lacks, or refuse the push."""
    if not needed:
        return
    present = set(store.list(layout.ASSETS_DIR))
    lacking = [key for key in needed if key not in present]
    unknown = [key for key in lacking if key.rsplit("/", 1)[1] not in local]
    if unknown:
        raise RepoError(
            f"{len(unknown)} file(s) the version points at are neither in the repository "
            f"nor given to upload (first: {unknown[0]})"
        )
    by_type: dict[str, list[tuple[str, Path]]] = {}
    for key in lacking:
        path = Path(local[key.rsplit("/", 1)[1]])
        if objects.sha256_of(path) != key.rsplit("/", 1)[1]:
            raise RepoError(f"{path} changed since its sha256 was taken")
        by_type.setdefault(needed[key] or objects.media_type(path.name), []).append((key, path))
    put_files = getattr(store, "put_files", None)
    for content_type, files in by_type.items():
        if put_files is not None:
            put_files(files, content_type=content_type)
            continue
        for key, path in files:
            try:
                store.put_file(key, path, content_type=content_type, if_none_match="*")
            except Exception as exc:  # noqa: BLE001 - stores raise their own types
                # Named by their content: one already there is this one.
                if not is_conflict(exc):
                    raise


def _put_once(store: Store, key: str, path: Path) -> None:
    """Write a file of a version. Versions do not change, so an object already
    there is only accepted when it is this very file (a retried push)."""
    content_type = "application/json" if key.endswith(".json") else "application/octet-stream"
    try:
        store.put_file(key, path, content_type=content_type, if_none_match="*")
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if not is_conflict(exc):
            raise
        if store.get(key) != path.read_bytes():
            raise RepoError(f"{key} is already there with other content") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
