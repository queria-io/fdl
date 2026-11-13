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

from . import catalog, layout, objects, profiles, refs
from . import dataset as dataset_json
from . import diff as diffs
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
    parent_dataset: dict | None = None,
    merged: Version | None = None,
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

    ``parent_dataset`` is the parent's `dataset.json`, which the version's
    ``diff`` compares descriptions with (see `record`).

    ``merged`` is the version a merge brought in: the second parent.
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
            parent_dataset=parent_dataset,
            merged=merged,
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
    version: Version | None = None,
    storage: str | None = None,
) -> Version | None:
    """Make a working catalog from the version ``ref`` holds, or from ``version`` when given.

    Before the first version there is nothing to copy, and an empty working
    catalog is created instead. Returns the version checked out, which a
    commit from this working catalog builds on.

    A store DuckDB cannot write to (one over HTTP) needs ``local_data``, a
    folder on this machine: new data files are written there and uploaded by
    `push`, while the files already in the repository are read where they are.

    ``storage`` is instead the repository's root in its object storage (an
    ``s3://`` URL, say): the working catalog reads and writes the data files
    there directly, with keys of the writer's own, and `push` has none to
    upload.
    """
    working = Path(working)
    config = read_config(store)
    remote_data = _data_url(store, config)
    if storage is not None:
        data_path = storage_data_path(storage, config)
    elif writes_data_files(store):
        data_path = remote_data
    elif local_data is None:
        raise RepoError("this store takes no data files from DuckDB; give a local folder for them")
    else:
        Path(local_data).mkdir(parents=True, exist_ok=True)
        data_path = Path(local_data).resolve().as_posix() + "/"
    version = version or head(store, ref)
    if version is None:
        catalog.create(working, alias="lake", data_path=data_path)
        return None
    if is_expired(store, version.id):
        raise RepoError(f"{version.id} is past retention and has no catalog")
    working.parent.mkdir(parents=True, exist_ok=True)
    frozen = working.with_name(f".{working.name}.{version.id}.duckdb")
    try:
        frozen.write_bytes(store.get(layout.version_catalog(version.id)))
        check_catalog(frozen, version)
        if data_path != remote_data and storage is None:
            catalog.pin_files(frozen, remote_data)
        catalog.convert(frozen, working, data_path=data_path)
    finally:
        frozen.unlink(missing_ok=True)
    return version


def check_catalog(frozen: Path, version: Version) -> None:
    """Refuse a catalog that is not the one ``version`` records, or that names
    a data file outside the repository's data folder.

    A reader that trusted such a catalog would read files the repository does
    not hold, with the reader's own credentials.
    """
    if _sha256(frozen) != version.catalog.sha256:
        raise RepoError(f"{version.id}: the catalog's sha256 is not the one version.json records")
    absolute = catalog.absolute_paths(frozen)
    if absolute:
        raise RepoError(f"{version.id}: the catalog names a data file by an absolute path: {absolute[0].path}")
    for key in catalog.data_files(frozen):
        if key.startswith("/") or "\\" in key or any(part in ("", ".", "..") for part in key.split("/")):
            raise RepoError(f"{version.id}: the catalog names a data file outside the data folder: {key}")


def storage_data_path(storage: str, config: Config) -> str:
    """The data files' folder, given the repository's root in its storage."""
    root = storage if "://" in storage else Path(storage).resolve().as_posix()
    return root.rstrip("/") + "/" + config.data_dir


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
    parent_dataset: dict | None = None,
    merged: Version | None = None,
) -> Version:
    """Make a version out of a catalog that is already frozen.

    For a writer that freezes the catalog itself (with checks of its own) and
    only needs the version around it. The catalog is copied, not moved.

    The version's ``diff`` compares the catalog at the parent's snapshot with
    the frozen one. Its descriptions are compared only when both ``dataset``
    and ``parent_dataset`` (the parent's `dataset.json`) are given.

    ``merged`` is the second parent of a merge version; ``seq`` follows the first.
    """
    if merged is not None and parent is None:
        raise RepoError("a merge version needs the version of its own branch as first parent")
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
        completed = None
        if dataset is not None:
            written = target / layout.DATASET
            completed = dataset_json.describe(dataset, copied)
            written.write_bytes(dataset_json.to_json(completed))
            described = File(
                path=layout.DATASET, bytes=written.stat().st_size, sha256=_sha256(written)
            )
        indexed = _write_files_index(target, copied, last)
        difference = diffs.between(
            copied,
            parent.ducklake.snapshot_id if parent else None,
            last,
            old_dataset=parent_dataset,
            new_dataset=completed,
        )
        listed = None
        if tree:
            written = target / layout.TREE
            written.write_bytes(_tree_json(tree))
            listed = File(path=layout.TREE, bytes=written.stat().st_size, sha256=_sha256(written))
        version = Version(
            id=version_id,
            seq=parent.seq + 1 if parent else 1,
            parents=(parent.id, *([merged.id] if merged else [])) if parent else (),
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
            diff=difference,
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
    """Write `files.json`: each table's files at the frozen snapshot, and every file the catalog names."""
    written = target / layout.FILES
    index = {"tables": catalog.table_files(frozen, snapshot_id), "named": catalog.data_files(frozen)}
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
        # This push again, after a run whose answer was lost. That run may
        # have stopped before rewriting refs.json, and a host that does its
        # own work when a ref moves may have stopped after moving it.
        if move_ref:
            _write_ref_again(store, current_ref)
        update_refs_index(store)
        return
    _refuse_unless_fast_forward(store, current_ref, version)

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


def _write_ref_again(store: Store, ref: refs.Ref) -> None:
    """Write ``ref`` to the version it holds, so a host finishes the move.

    A ref that moved meanwhile was moved by a later push, which did the
    host's work for its own version: nothing is left to finish.
    """
    if not ref.etag:
        return
    try:
        refs.write(store, ref.name, ref.target, expected=ref)
    except refs.RefConflict:
        pass


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


def write_profiles(
    store: Store,
    local: Path,
    parent: Version | None,
    working: Path,
    *,
    data_path: str,
    setup: Iterable[str] = (),
) -> int:
    """Write a profile of each table of a committed version under ``local/profiles/``.

    A table whose data files and columns are the parent's keeps the parent's
    profile, so a commit reads only the tables that changed. Returns how many
    were made from the data.
    """
    local = Path(local)
    described_path = local / layout.DATASET
    if not described_path.is_file():
        return 0
    described = json.loads(described_path.read_text(encoding="utf-8"))
    files_now = json.loads((local / layout.FILES).read_text(encoding="utf-8")).get("tables", {})
    files_before: dict = {}
    if parent is not None and parent.files is not None:
        try:
            files_before = json.loads(store.get(layout.version_dir(parent.id) + parent.files.path)).get("tables", {})
        except Exception as exc:  # noqa: BLE001 - stores raise their own types
            if not is_missing(exc):
                raise
    kept: dict[str, bytes] = {}
    pending = []
    for table in profiles.tables(described):
        key = f"{table['schema']}.{table['name']}"
        relative = profiles.path(table["schema"], table["name"])
        previous = None
        before, now = files_before.get(key), files_now.get(key, {})
        if before is not None and "columns" not in before:
            # A parent written before files.json listed the columns: compare the
            # rest, and leave the names and types to profiles.reusable.
            now = {name: value for name, value in now.items() if name != "columns"}
        if parent is not None and before is not None and before == now:
            try:
                previous = store.get(layout.version_dir(parent.id) + relative)
            except Exception as exc:  # noqa: BLE001
                if not is_missing(exc):
                    raise
        if previous is not None and profiles.reusable(previous, table):
            kept[relative] = previous
        else:
            pending.append(table)
    made = profiles.build(working, pending, data_path=data_path, setup=setup)
    for relative, content in {**kept, **made}.items():
        target = local / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return len(made)


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
    profiled = local / layout.PROFILES_DIR
    if profiled.is_dir():
        for written in sorted(profiled.rglob("*.json")):
            relative = written.relative_to(local).as_posix()
            _put_once(store, layout.version_dir(version.id) + relative, written)
    _put_once(store, layout.version_record(version.id), local / layout.VERSION_RECORD)


def log(store: Store, name: str = layout.HEAD) -> Iterator[Version]:
    """Versions from the one ``name`` holds back to the first, by first parent."""
    current = head(store, name)
    while current is not None:
        yield current
        current = read_version(store, current.parents[0]) if current.parents else None


def is_ancestor(store: Store, ancestor_id: str, descendant: Version) -> bool:
    """Whether ``ancestor_id`` is ``descendant`` or behind it, through every parent.

    Breadth first, so a parent is found in one step. ``seq`` is no bound: a
    merge's ``seq`` follows its first parent, so it can be lower than that of
    the version it merged.
    """
    pending = [descendant]
    seen: set[str] = set()
    while pending:
        if any(current.id == ancestor_id for current in pending):
            return True
        seen.update(current.id for current in pending)
        parents = dict.fromkeys(parent for current in pending for parent in current.parents if parent not in seen)
        pending = [read_version(store, parent) for parent in parents]
    return False


def in_history(store: Store, ancestor_id: str, descendant: Version) -> bool:
    """Whether ``ancestor_id`` is ``descendant`` or behind it by first parent.

    The first-parent history is the one a working catalog's snapshots follow;
    a version reached only through a merge's second parent has snapshots of
    another catalog. ``seq`` falls by one at each step, so the walk stops
    once it is below the ancestor's.
    """
    target = read_version(store, ancestor_id)
    current: Version | None = descendant
    while current is not None and current.seq >= target.seq:
        if current.id == ancestor_id:
            return True
        current = read_version(store, current.parents[0]) if current.parents else None
    return False


def ancestors(store: Store, version: Version) -> dict[str, Version]:
    """``version`` and every version behind it, through every parent, by ID."""
    found: dict[str, Version] = {}
    pending = [version]
    while pending:
        current = pending.pop()
        if current.id in found:
            continue
        found[current.id] = current
        pending.extend(read_version(store, parent) for parent in current.parents if parent not in found)
    return found


def merge_bases(store: Store, ours: Version, theirs: Version) -> list[Version]:
    """The common ancestors of ``ours`` and ``theirs`` that no other common ancestor descends from.

    Every parent of a common ancestor is a common ancestor too, so those that
    are no common ancestor's parent are the ones asked for.
    """
    mine, other = ancestors(store, ours), ancestors(store, theirs)
    common = {version_id: mine[version_id] for version_id in mine.keys() & other.keys()}
    behind = {parent for version in common.values() for parent in version.parents}
    return sorted((version for version_id, version in common.items() if version_id not in behind), key=lambda v: v.id)


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
            *store.list(layout.version_dir(version_id) + layout.PROFILES_DIR),
        ]
    )


@dataclass(frozen=True)
class Withdrawal:
    """What withdrawing versions does, worked out before anything is changed."""

    versions: tuple[str, ...]
    #: Tags that point at the versions, deleted first.
    tags: tuple[str, ...]
    #: Shared objects (data files and assets) the versions reach and no kept
    #: version reaches, as store keys.
    deleted: tuple[str, ...]
    #: Shared objects the versions reach that a kept version still reaches. Kept:
    #: if they hold the data, the version that reaches them has to be withdrawn too.
    still_read: tuple[str, ...]


def plan_withdrawal(store: Store, version_ids: Iterable[str], *, setup: Iterable[str] = ()) -> Withdrawal:
    """Work out a withdrawal (see `withdraw`) without changing anything.

    Refused while a branch or a request still points at one of the versions:
    the first step, a new version without the data, is the writer's.

    Shared objects are data files and assets. ``setup`` runs before a
    catalog is attached to read its asset columns: a ``CREATE SECRET`` for
    data files in private storage, say.
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

    setup = list(setup)
    # Store keys: data files under the data directory, assets under assets/.
    candidates: set[str] = set()
    kept: set[str] = set()
    catalogless = False
    with tempfile.TemporaryDirectory(prefix="fdl-") as tmp:
        for version_id in _version_ids(store):
            if version_id in affected:
                version = read_version(store, version_id)
                local = _fetch_catalog(store, version_id, Path(tmp))
                if local is not None:
                    # Every file the catalog has ever named: earlier snapshots
                    # in it may hold the data too.
                    candidates.update(config.data_dir + key for key in catalog.data_files(local))
                    try:
                        candidates.update(_reached_assets(store, version, local, setup))
                    except objects.ObjectError:
                        # A run stopped half way may have deleted the data
                        # files that held its asset columns: then, as for a
                        # version without a catalog, every asset no live
                        # version reaches goes.
                        catalogless = True
                    local.unlink()
                else:
                    catalogless = True
                continue
            if is_expired(store, version_id):
                continue
            kept.update(_reached_by(store, config, read_version(store, version_id), Path(tmp), setup))
    if catalogless:
        # A version already past retention no longer says which objects it
        # reached. Those no live version reaches are waiting for the grace
        # period anyway, so all of them go now.
        candidates.update(
            key
            for key in (*store.list(config.data_dir), *store.list(layout.ASSETS_DIR))
            if layout.is_shared_object(key, config.data_dir)
        )
    return Withdrawal(
        versions=affected,
        tags=tuple(tags),
        deleted=tuple(sorted(candidates - kept)),
        still_read=tuple(sorted(candidates & kept)),
    )


def _reached_by(store: Store, config: Config, version: Version, tmp: Path, setup: list[str]) -> set[str]:
    """The shared objects a live version reaches, as store keys.

    A version reaches every data file its catalog names, at any snapshot the
    catalog keeps, not only those it reads at its own snapshot.
    """
    # files.json names them without the catalog, when it holds `named`.
    named = _named_files(store, version)
    local = None
    if named is None or _asset_columns(store, version):
        local = _fetch_catalog(store, version.id, tmp)
    reached: set[str] = set()
    if named is not None:
        reached.update(config.data_dir + key for key in named)
    elif local is not None:
        reached.update(config.data_dir + key for key in catalog.data_files(local))
    else:
        raise RepoError(f"{version.id} is live but has neither files.json with `named` nor a catalog to say what it reaches")
    reached.update(_reached_assets(store, version, local, setup))
    if local is not None:
        local.unlink()
    return reached


@dataclass(frozen=True)
class Collection:
    """What `collect` deletes: shared objects no live version reaches, placed before ``before``."""

    deleted: tuple[str, ...]
    before: dt.datetime
    #: The live versions whose objects were kept.
    versions: tuple[str, ...] = ()


def plan_collection(
    store: Store,
    *,
    grace: dt.timedelta = dt.timedelta(days=7),
    setup: Iterable[str] = (),
    now: dt.datetime | None = None,
) -> Collection:
    """Work out which shared objects may be deleted (see `collect`), changing nothing.

    The objects are listed before the versions are read, so a version pushed
    while this runs keeps what it reaches.
    """
    if not hasattr(store, "placed"):
        raise RepoError("this store does not say when objects were written; collect at the storage itself")
    config = read_config(store)
    before = (now or dt.datetime.now(dt.timezone.utc)) - grace
    placed = {**store.placed(config.data_dir), **store.placed(layout.ASSETS_DIR)}
    old = {key for key, when in placed.items() if when < before and layout.is_shared_object(key, config.data_dir)}
    live = [version_id for version_id in _version_ids(store) if not is_expired(store, version_id)]
    reached = _reached_by_all(store, config, live, list(setup))
    return Collection(deleted=tuple(sorted(old - reached)), before=before, versions=tuple(live))


def _reached_by_all(store: Store, config: Config, version_ids: Iterable[str], setup: list[str]) -> set[str]:
    reached: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="fdl-") as tmp:
        for version_id in version_ids:
            reached.update(_reached_by(store, config, read_version(store, version_id), Path(tmp), setup))
    return reached


def collect(store: Store, *, plan: Collection, setup: Iterable[str] = ()) -> list[str]:
    """Delete the shared objects no live version reaches, once past the grace period.

    Data files left by working catalogs that were never pushed, and those only
    versions past retention reached, go this way. Versions written since the
    plan are read first, and what they reach is kept. Returns the keys deleted.
    """
    config = read_config(store)
    since = [
        version_id
        for version_id in _version_ids(store)
        if version_id not in plan.versions and not is_expired(store, version_id)
    ]
    kept = _reached_by_all(store, config, since, list(setup))
    deleted = [key for key in plan.deleted if key not in kept]
    store.delete(deleted)
    return deleted


def _asset_columns(store: Store, version: Version) -> list[tuple[str, str, str]]:
    """The asset columns the version's `dataset.json` marks."""
    if version.dataset is None:
        return []
    try:
        described = json.loads(store.get(layout.version_dir(version.id) + version.dataset.path))
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if is_missing(exc):
            return []
        raise
    return objects.asset_columns(described)


def _reached_assets(store: Store, version: Version, local: Path | None, setup: list[str]) -> set[str]:
    """The asset keys the version reaches: its `tree.json` and its asset columns."""
    reached: set[str] = set()
    if version.tree is not None:
        try:
            listed = json.loads(store.get(layout.version_dir(version.id) + version.tree.path))
        except Exception as exc:  # noqa: BLE001 - stores raise their own types
            if not is_missing(exc):
                raise
        else:
            reached.update(objects.asset_key(item["sha256"]) for item in listed["entries"])
    columns = _asset_columns(store, version)
    if columns and local is not None:
        values = objects.asset_values(local, columns, data_path=data_url(store), setup=setup)
        reached.update(objects.asset_key(sha256) for sha256 in values)
    return reached


def withdraw(store: Store, version_ids: Iterable[str], *, plan: Withdrawal | None = None) -> Withdrawal:
    """Remove versions that hold data that must not be published.

    Unlike retention it takes live versions and deletes their data files and
    assets at once. In the spec's order: the shared objects go before the
    catalogs, so a run that stops half way can be run again while the
    catalogs still say which objects they are. `version.json` stays.
    """
    plan = plan or plan_withdrawal(store, version_ids)
    for name in plan.tags:
        refs.delete(store, name)
    if plan.tags:
        update_refs_index(store)
    # Assets first: the data files are what says which assets the versions'
    # rows reach, so a run that stops half way can still work them out.
    assets = [key for key in plan.deleted if key.startswith(layout.ASSETS_DIR)]
    store.delete(assets)
    store.delete([key for key in plan.deleted if not key.startswith(layout.ASSETS_DIR)])
    for version_id in plan.versions:
        expire(store, version_id, reason="withdrawn")
    return plan


def _version_ids(store: Store) -> list[str]:
    suffix = "/" + layout.VERSION_RECORD
    ids = [key[len("versions/") : -len(suffix)] for key in store.list("versions/") if key.endswith(suffix)]
    return [version_id for version_id in ids if layout.is_version_id(version_id)]


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


def _named_files(store: Store, version: Version) -> set[str] | None:
    """Every data file the version's catalog names, from `named` in its `files.json`.

    ``None`` without a `files.json`, or with one written before `named`: its
    `tables` hold only the files at the version's snapshot.
    """
    if version.files is None:
        return None
    try:
        index = json.loads(store.get(layout.version_dir(version.id) + version.files.path))
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if is_missing(exc):
            return None
        raise
    named = index.get("named")
    return None if named is None else set(named)


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


def _refuse_unless_fast_forward(store: Store, current: refs.Ref | None, version: Version) -> None:
    if current is None:
        if version.parents:
            raise NotFastForward(
                f"{version.id} is built on {version.parents[0]}, but the branch "
                f"has no version yet"
            )
        return
    if not is_ancestor(store, current.target, version):
        raise NotFastForward(
            f"{current.name} is at {current.target}, which {version.id} is not "
            f"built on. Pull and build again."
        )


def forward(store: Store, ref: str, version: Version) -> None:
    """Move a branch to a version already in the repository that descends from where it is.

    For a merge whose base is the branch's own version: no new version is
    made, the branch moves to the other one.
    """
    current = refs.read(store, layout.check_ref(ref))
    if current is not None and current.target == version.id:
        update_refs_index(store)
        return
    _refuse_unless_fast_forward(store, current, version)
    read_version(store, version.id)
    try:
        refs.write(store, ref, version.id, expected=current)
    except refs.RefConflict as exc:
        now = refs.read(store, ref)
        if now is None or now.target != version.id:
            raise NotFastForward(f"{ref} moved meanwhile. Pull and merge again.") from exc
    update_refs_index(store)


def branch(store: Store, name: str, version_id: str) -> None:
    """Create ``refs/heads/<name>`` at a version. A branch that is there is not moved."""
    ref = layout.check_ref(f"refs/heads/{name}")
    read_version(store, version_id)
    if is_expired(store, version_id):
        raise RepoError(f"{version_id} is out of retention, so a branch cannot start there")
    try:
        refs.write(store, ref, version_id, expected=None)
    except refs.RefConflict as exc:
        now = refs.read(store, ref)
        if now is None or now.target != version_id:
            raise RepoError(f"{ref} is already there, at {now.target if now else '?'}") from exc
    update_refs_index(store)


def delete_branch(store: Store, name: str) -> None:
    """Delete ``refs/heads/<name>``. The branch HEAD names is kept."""
    ref = layout.check_ref(f"refs/heads/{name}")
    if refs.resolve(store, layout.HEAD)[0] == ref:
        raise RepoError(f"HEAD names {ref}, so it is kept")
    refs.delete(store, ref)
    update_refs_index(store)


def branches(store: Store) -> dict[str, str]:
    """Each branch's name (after ``refs/heads/``) and the version it holds."""
    prefix = "refs/heads/"
    return {name[len(prefix) :]: target for name, target in refs.listed(store).items() if name.startswith(prefix)}


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
