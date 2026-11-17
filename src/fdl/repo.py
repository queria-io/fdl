"""The operations on a repository, with no terminal and no service behind them.

Each takes a `Store` for the remote and paths for the local side, and returns
what it did. Printing, prompts and credentials belong to whoever calls these.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import tempfile
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping

from . import catalog, layout, objects, profiles, refs
from . import tags as fdl_tags
from . import dataset as dataset_json
from . import diff as diffs
from .store import Store, is_conflict, is_missing
from .version import DuckLake, File, Metadata, Version, VersionError, new_id, parse, utc_now


class RepoError(RuntimeError):
    """The repository cannot do what was asked, and nothing was changed."""


class NotFastForward(RepoError):
    """The version is not built on what the ref holds now. Pull and build again."""


class NothingToCommit(RepoError):
    """The working metadata holds what its parent holds, so no version is made."""

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
    if not layout.is_data_dir(data_dir):
        raise RepoError(f"data_dir is not a folder under the root: {data_dir!r}")
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
    data_dir = record.get("data_dir", layout.DEFAULT_DATA_DIR)
    if not layout.is_data_dir(data_dir):
        raise RepoError(f"fdl.json: data_dir is not a folder under the root: {data_dir!r}")
    return Config(fdl=record["fdl"], data_dir=data_dir, id=record.get("id"))


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
        raise RepoError(f"{version.id} is past retention or withdrawn and has no metadata")
    return version


def commit(
    working: Path,
    out_dir: Path,
    *,
    parent: Version | None,
    data_path: str,
    author: dict,
    message: str,
    annotations: dict | None = None,
    dataset: dict | None = None,
    tree: list[dict] | None = None,
    allow_empty: bool = True,
    working_data: str | None = None,
    parent_dataset: dict | None = None,
    merged: Version | None = None,
) -> Version:
    """Freeze the working metadata into a version under ``out_dir/<ID>/``.

    ``dataset`` is the writer's description (`dataset.json`), checked against
    the frozen metadata and completed from it. ``tree`` lists the version's
    other files (`tree.json`: path, sha256, bytes), such as its README.

    ``allow_empty=False`` refuses a version that would hold what ``parent``
    holds: the same tables, columns, rows and views, and the same
    `dataset.json`. It raises `NothingToCommit`, or `CannotCompare` when the
    rows could not be read to tell.

    ``data_path`` is where readers find the data files (the remote's root and
    data_dir), written into the frozen catalog. Every file reference must be
    relative to it, or the version would point readers at the writer's disk.
    Nothing is uploaded: that is `push`.

    ``working_data`` is where the working metadata wrote its new data files,
    when not where it records (see `clone`): comparing reads them there.

    ``parent_dataset`` is the parent's `dataset.json`, which the version's
    ``diff`` compares with (see `record`).

    ``merged`` is the version a merge brought in: the second parent.
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    frozen = Path(out_dir) / f".freezing-{new_id()}.duckdb"
    try:
        catalog.convert(working, frozen, data_path=data_path)
        catalog.set_data_path(frozen, data_path)
        # Files a working metadata file read from the repository where they are.
        catalog.unpin_files(frozen, data_path)
        absolute = catalog.absolute_paths(frozen)
        if absolute:
            raise RepoError(
                f"{len(absolute)} file(s) are recorded with an absolute path "
                f"(first: {absolute[0].path}); rebuild so they sit under the data path"
            )
        # The description first: it reads the metadata alone, while comparing
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


def clone(
    store: Store,
    working: Path,
    *,
    ref: str = layout.HEAD,
    local_data: Path | None = None,
    version: Version | None = None,
    storage: str | None = None,
) -> Version | None:
    """Make a working metadata file from the version ``ref`` holds, or from ``version`` when given.

    Before the first version there is nothing to copy, and an empty working
    metadata is created instead. Returns the version cloned, which a
    commit from this working metadata builds on.

    A store DuckDB cannot write to (one over HTTP) needs ``local_data``, a
    folder on this machine: new data files are written there and uploaded by
    `push`, while the files already in the repository are read where they are.

    ``storage`` is instead the repository's root in its object storage (an
    ``s3://`` URL, say): the working metadata reads and writes the data files
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
        raise RepoError(f"{version.id} is past retention and has no metadata")
    working.parent.mkdir(parents=True, exist_ok=True)
    frozen = working.with_name(f".{working.name}.{version.id}.duckdb")
    try:
        frozen.write_bytes(store.get(layout.version_metadata(version)))
        check_catalog(frozen, version)
        if data_path != remote_data and storage is None:
            catalog.pin_files(frozen, remote_data)
        catalog.convert(frozen, working, data_path=data_path)
    finally:
        frozen.unlink(missing_ok=True)
    return version


def check_catalog(frozen: Path, version: Version) -> None:
    """Refuse a metadata file that is not the one ``version`` records, or that names
    a data file outside the repository's data folder.

    A reader that trusted such a metadata file would read files the repository does
    not hold, with the reader's own credentials.
    """
    if _sha256(frozen) != version.metadata.sha256:
        raise RepoError(f"{version.id}: the metadata's sha256 is not the one version.json records")
    absolute = catalog.absolute_paths(frozen)
    if absolute:
        raise RepoError(f"{version.id}: the metadata names a data file by an absolute path: {absolute[0].path}")
    for key in catalog.data_files(frozen):
        if key.startswith("/") or "\\" in key or any(part in ("", ".", "..") for part in key.split("/")):
            raise RepoError(f"{version.id}: the metadata names a data file outside the data folder: {key}")


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
    annotations: dict | None = None,
    dataset: dict | None = None,
    tree: list[dict] | None = None,
    parent_dataset: dict | None = None,
    merged: Version | None = None,
) -> Version:
    """Make a version out of a metadata file that is already frozen.

    For a writer that freezes the metadata itself (with checks of its own) and
    only needs the version around it. The metadata is copied, not moved, and
    the copy keeps only its last snapshot (see `catalog.keep_last_snapshot`).

    The version's ``diff`` compares the metadata at the parent's snapshot with
    the frozen one, and says whether what the writer wrote in `dataset.json`
    changed when both it and ``parent_dataset`` (the parent's) are given.

    The primary keys, assets and catalogs ``dataset`` declares are written into
    the metadata as `fdl.` tags (see `tags.write`).

    ``merged`` is the second parent of a merge version; ``seq`` follows the first.
    """
    if merged is not None and parent is None:
        raise RepoError("a merge version needs the version of its own branch as first parent")
    if not author.get("name"):
        raise RepoError("a version needs an author name")
    version_id = new_id()
    target = Path(out_dir) / version_id
    target.mkdir(parents=True)
    copied = target / layout.METADATA
    try:
        shutil.copyfile(frozen, copied)
        recorded = catalog.recorded_format(copied)
        if not recorded:
            raise RepoError(f"{frozen} records no DuckLake format")
        after = parent.ducklake.snapshot_id if parent else None
        described = None
        completed = None
        if dataset is not None:
            written = target / layout.DATASET
            completed = dataset_json.describe(dataset, copied)
            written.write_bytes(dataset_json.to_json(completed))
            described = File(
                path=layout.DATASET, bytes=written.stat().st_size, sha256=_sha256(written)
            )
            # The values FDL reads, kept in the metadata as `fdl.` tags beside `dataset.json`.
            # Before the range: tags that change alone get a snapshot of their own.
            fdl_tags.write(copied, fdl_tags.from_dataset(completed), after=after)
        first, last = catalog.snapshot_range(copied, after)
        difference = diffs.between(copied, after, last)
        if difference is not None:
            difference = diffs.with_dataset(difference, parent_dataset, completed)
        # After the diff, which reads the parent's snapshot: the version keeps only its own.
        catalog.keep_last_snapshot(copied)
        # Attaching it through DuckLake must not have moved it to another format.
        if catalog.recorded_format(copied) != recorded:
            raise RepoError(f"keeping the last snapshot changed the DuckLake format of {frozen}")
        indexed = _write_files_index(target, copied, last)
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
            metadata=Metadata(
                path=layout.METADATA,
                storage=catalog.storage_version(copied),
                bytes=copied.stat().st_size,
                sha256=_sha256(copied),
            ),
            ducklake=DuckLake(format=recorded, snapshot_id=last, snapshots=(first, last)),
            files=indexed,
            diff=difference,
            annotations=dict(annotations or {}),
            dataset=described,
            tree=listed,
        )
        (target / layout.VERSION_RECORD).write_bytes(version.to_json())
    except BaseException:
        # Whatever stopped it (a full disk included), a half-made version is a
        # copy of the metadata that nothing will ever read.
        shutil.rmtree(target, ignore_errors=True)
        raise
    return version


def _write_files_index(target: Path, frozen: Path, snapshot_id: int) -> File:
    """Write `files.json`: each table's files at the frozen snapshot, and every file the metadata names."""
    written = target / layout.FILES
    tagged = fdl_tags.read(frozen, snapshot_id)
    index = {
        "tables": {
            name: {**table, **_index_tags(tagged.get(name, {}))}
            for name, table in catalog.table_files(frozen, snapshot_id).items()
        },
        "named": catalog.data_files(frozen),
    }
    written.write_bytes((json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode())
    return File(path=layout.FILES, bytes=written.stat().st_size, sha256=_sha256(written))


def _index_tags(values: dict) -> dict:
    """A table's `fdl.primary_key` and `fdl.assets`, as `files.json` keys them."""
    return {
        key: values[tag]
        for key, tag in (("primary_key", fdl_tags.PRIMARY_KEY), ("assets", fdl_tags.ASSETS))
        if tag in values
    }


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
    then the metadata, then `version.json`, and the ref last. A push stopped
    half way leaves files no ref points at, which readers never see.

    ``move_ref=False`` uploads the version and stops: for a host that moves
    its refs itself (after an approval, say).

    ``local_data`` is the folder a working metadata file wrote new data files to
    (see `clone`). Those the repository lacks are uploaded first.

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

    frozen = Path(local) / version.metadata.path
    if local_data is not None:
        _upload_data_files(store, config, frozen, Path(local_data))
    missing = _missing_data_files(store, config, frozen)
    if missing:
        raise RepoError(
            f"{len(missing)} data file(s) the metadata names are not in the "
            f"repository (first: {missing[0]}). Rebuild from the working "
            f"metadata and commit again."
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
        # The first attempt moved it, but its answer was lost: the host may
        # have stopped after the move.
        _write_ref_again(store, now)
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
    profile, so profiling reads only the tables that changed. A view keeps it
    when no view's query and no table's data files changed. Returns
    how many were made from the data.
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
    views_now = _view_sql(described)
    views_unchanged = False
    if views_now and parent is not None and parent.dataset is not None and files_before == files_now:
        # A view reads tables and other views, so it keeps its profile only when
        # no table's data files and no view's query changed.
        try:
            held = json.loads(store.get(layout.version_dir(parent.id) + parent.dataset.path))
            views_unchanged = _view_sql(held) == views_now
        except Exception as exc:  # noqa: BLE001 - stores raise their own types
            if not is_missing(exc):
                raise
    kept: dict[str, bytes] = {}
    pending = []
    for table in profiles.tables(described):
        key = f"{table['schema']}.{table['name']}"
        relative = profiles.path(table["schema"], table["name"])
        previous = None
        if table.get("kind") == "view":
            unchanged = views_unchanged
        else:
            before, now = files_before.get(key), files_now.get(key, {})
            if before is not None and "columns" not in before:
                # A parent written before files.json listed the columns: compare the
                # rest, and leave the names and types to profiles.reusable.
                now = {name: value for name, value in now.items() if name != "columns"}
            unchanged = before is not None and before == now
        if parent is not None and unchanged:
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


def _view_sql(described: dict) -> dict[str, str | None]:
    """Each view of a `dataset.json` (``schema.name``) and its query."""
    return {
        f"{t['schema']}.{t['name']}": t.get("sql") for t in described.get("tables", []) if t.get("kind") == "view"
    }


def missing_profiles(store: Store, version: Version) -> list[dict]:
    """The tables of ``version`` that get a profile and have none in the repository."""
    if version.dataset is None:
        return []
    folder = layout.version_dir(version.id)
    described = json.loads(store.get(folder + version.dataset.path))
    there = set(store.list(folder + layout.PROFILES_DIR))
    return [
        table
        for table in profiles.tables(described)
        if folder + profiles.path(table["schema"], table["name"]) not in there
    ]


def add_profiles(store: Store, version: Version, *, setup: Iterable[str] = ()) -> tuple[list[str], list[str]]:
    """Profile the tables of a pushed version that have no profile, and put those profiles.

    For a version made before its writer profiled tables, or whose profiling
    failed. A profile already there is left as it is. Returns the paths put,
    under the version, and the tables (``schema.name``) that could not be read.
    Refused for a version past retention, which has no metadata to clone.
    """
    wanted = missing_profiles(store, version)
    if not wanted:
        return [], []
    with tempfile.TemporaryDirectory(prefix=".fdl-profile-") as scratch:
        working = Path(scratch) / "work.sqlite"
        clone(store, working, version=version, local_data=Path(scratch) / "data")
        made = profiles.build(working, wanted, data_path=data_url(store), setup=setup)
        for relative, content in made.items():
            written = Path(scratch) / relative
            written.parent.mkdir(parents=True, exist_ok=True)
            written.write_bytes(content)
            _put_once(store, layout.version_dir(version.id) + relative, written)
    unread = [
        f"{table['schema']}.{table['name']}"
        for table in wanted
        if profiles.path(table["schema"], table["name"]) not in made
    ]
    return sorted(made), unread


def upload(store: Store, local: Path, version: Version) -> None:
    """Put a committed version's files, metadata first and the record last.

    No checks and no ref: for a host that checks and moves its refs itself.
    """
    local = Path(local)
    _put_once(store, layout.version_metadata(version), local / version.metadata.path)
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

    The first-parent history is the one a working metadata file's snapshots follow;
    a version reached only through a merge's second parent has snapshots of
    another metadata file. ``seq`` falls by one at each step, so the walk stops
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
    """The common ancestors of ``ours`` and ``theirs`` that no other common ancestor descends from."""
    mine = ancestors(store, ours)
    return sorted((mine[version_id] for version_id in _bases(mine, ancestors(store, theirs))), key=lambda v: v.id)


def _bases(mine: Mapping[str, Version], other: Mapping[str, Version]) -> set[str]:
    """The IDs of the merge bases of two versions, from everything behind each (`ancestors`).

    Every parent of a common ancestor is a common ancestor too, so those that
    are no common ancestor's parent are the ones asked for.
    """
    common = mine.keys() & other.keys()
    behind = {parent for version_id in common for parent in mine[version_id].parents}
    return common - behind


def is_expired(store: Store, version_id: str) -> bool:
    return store.head(layout.version_expired(version_id)) is not None


def expire(
    store: Store,
    version_id: str,
    *,
    reason: str = "retention",
    by: str | None = None,
    message: str | None = None,
    deleted: dict[str, list[str]] | None = None,
) -> None:
    """Take a version out of retention: the mark first, then its catalogs.

    `version.json` stays, so the history still says the version was there and
    what it was. Data files are not touched: other versions share them, and
    which ones may go is for a pass over the whole repository to decide.
    Running it again finishes a run that stopped between the two steps.
    """
    version = read_version(store, version_id)
    held = _held(store)
    if version_id in held:
        raise RepoError(f"{held[version_id]} points at {version_id}, so it is kept")
    record: dict = {
        "expired_at": utc_now(),
        "reason": reason,
        **({"by": by} if by else {}),
        **({"message": message} if message else {}),
    }
    if deleted is not None:
        record["deleted"] = deleted
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
        if reason == "withdrawn":
            before = json.loads(store.get(mark))
            if before.get("reason") == reason:
                # Run again after stopping half way: the objects the first run
                # deleted may no longer be listed, so its names are kept.
                record = {**before, "deleted": _union_deleted(before.get("deleted"), deleted)}
            store.put(mark, (json.dumps(record) + "\n").encode(), content_type="application/json")
    store.delete(
        [
            layout.version_metadata(version),
            # The index names files the grace period may delete.
            layout.version_dir(version_id) + layout.FILES,
            layout.version_dir(version_id) + layout.TREE,
            *store.list(layout.version_dir(version_id) + layout.METADATA_DIR),
            *store.list(layout.version_dir(version_id) + layout.LEGACY_METADATA_DIR),
            *store.list(layout.version_dir(version_id) + layout.PROFILES_DIR),
        ]
    )


#: What `deleted` of `expired.json` names: data files, assets and the files of trees.
DELETED_KINDS = ("data", "assets", "objects")
#: Where each kind of hashed object is stored.
_HASHED_DIRS = {"assets": layout.ASSETS_DIR, "objects": layout.OBJECTS_DIR}


def _union_deleted(*lists: object) -> dict[str, list[str]]:
    union: dict[str, set[str]] = {kind: set() for kind in DELETED_KINDS}
    for value in lists:
        if isinstance(value, dict):
            for kind, names in union.items():
                names.update(name for name in value.get(kind, []) if isinstance(name, str))
    return {kind: sorted(names) for kind, names in union.items()}


def deleted_names(keys: Iterable[str], data_dir: str) -> dict[str, list[str]]:
    """Shared objects as `expired.json` names them: data files from `data_dir`, assets and tree files by sha256."""
    named: dict[str, list[str]] = {kind: [] for kind in DELETED_KINDS}
    for key in keys:
        kind = next((kind for kind, prefix in _HASHED_DIRS.items() if key.startswith(prefix)), None)
        if kind is not None:
            named[kind].append(key[len(_HASHED_DIRS[kind]) :])
        elif key.startswith(data_dir):
            named["data"].append(key[len(data_dir) :])
    return {kind: sorted(names) for kind, names in named.items()}


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
    #: Refs other than tags that point at the versions. `withdraw` refuses while
    #: there are any: move them to a version without the data first.
    blocked: tuple[str, ...] = ()


def plan_withdrawal(
    store: Store,
    version_ids: Iterable[str],
    *,
    setup: Iterable[str] = (),
    objects: dict[str, list[str]] | None = None,
    refuse_refs: bool = True,
) -> Withdrawal:
    """Work out a withdrawal (see `withdraw`) without changing anything.

    Refused while a branch or a request still points at one of the versions:
    the first step, a new version without the data, is the writer's. With
    ``refuse_refs=False`` those refs are listed in `blocked` instead, for a
    host that moves them itself.

    ``objects`` names shared objects another repository withdrew, as `deleted`
    of its `expired.json` does: a copy of it, such as a mirror,
    applies that withdrawal. Every live version that reaches one of them is
    affected too, and those objects go even where no version reaches them.

    Shared objects are data files and assets. ``setup`` runs before a
    metadata is attached to read its asset columns: a ``CREATE SECRET`` for
    data files in private storage, say.
    """
    config = read_config(store)
    setup = list(setup)
    named = _named_objects(objects or {}, config.data_dir)
    # What each live version reaches, read once for finding the affected versions and the kept objects.
    reached: dict[str, set[str]] = {}
    affected = tuple(dict.fromkeys([*version_ids, *_reaching(store, config, named, setup, reached)]))
    for version_id in affected:
        read_version(store, version_id)
    tags: list[str] = []
    blocked: list[str] = []
    for name in store.list("refs/"):
        try:
            ref = refs.read(store, name)
        except (refs.RefError, layout.LayoutError):
            continue
        if ref is None or ref.target not in affected:
            continue
        if not name.startswith("refs/tags/"):
            if refuse_refs:
                raise RepoError(
                    f"{name} points at {ref.target}. Move it to a version without the data first"
                )
            blocked.append(name)
            continue
        tags.append(name)

    # Store keys: data files under the data directory, assets under assets/,
    # tree files under objects/.
    # Named objects still there go whether or not a version reaches them.
    candidates: set[str] = {key for key in named if store.head(key) is not None}
    kept: set[str] = set()
    catalogless = False
    with tempfile.TemporaryDirectory(prefix="fdl-") as tmp:
        for version_id in _version_ids(store):
            if version_id in affected:
                version = read_version(store, version_id)
                local = _fetch_catalog(store, version, Path(tmp))
                if local is not None:
                    # Every file the metadata has ever named: earlier snapshots
                    # in it may hold the data too.
                    candidates.update(config.data_dir + key for key in catalog.data_files(local))
                    try:
                        candidates.update(_reached_assets(store, version, local, setup))
                    except objects.ObjectError:
                        # A run stopped half way may have deleted the data
                        # files that held its asset columns: then, as for a
                        # version without a metadata file, every asset no live
                        # version reaches goes.
                        catalogless = True
                    local.unlink()
                else:
                    catalogless = True
                continue
            if version_id in reached:
                kept.update(reached[version_id])
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
            for key in _shared_keys(store, config.data_dir)
            if layout.is_shared_object(key, config.data_dir)
        )
    return Withdrawal(
        versions=affected,
        tags=tuple(tags),
        deleted=tuple(sorted(candidates - kept)),
        still_read=tuple(sorted(candidates & kept)),
        blocked=tuple(blocked),
    )


def _named_objects(objects: dict[str, list[str]], data_dir: str) -> set[str]:
    """Store keys of the objects `deleted` names, leaving out any not in the form of a shared object."""
    prefixes = {"data": data_dir, **_HASHED_DIRS}
    keys = {prefixes[kind] + name for kind in DELETED_KINDS for name in objects.get(kind, [])}
    if any(".." in key.split("/") for key in keys):
        raise RepoError("an object name may not have a .. segment")
    return {key for key in keys if layout.is_shared_object(key, data_dir)}


def _shared_keys(store: Store, data_dir: str) -> list[str]:
    """Every key under the folders that hold shared objects."""
    return [key for prefix in (data_dir, *_HASHED_DIRS.values()) for key in store.list(prefix)]


def _reaching(
    store: Store, config: Config, keys: set[str], setup: list[str], reached: dict[str, set[str]]
) -> list[str]:
    """The live versions that reach any of ``keys``. What each live version reaches goes into ``reached``."""
    if not keys:
        return []
    found: list[str] = []
    with tempfile.TemporaryDirectory(prefix="fdl-") as tmp:
        for version_id in _version_ids(store):
            if is_expired(store, version_id):
                continue
            reached[version_id] = _reached_by(store, config, read_version(store, version_id), Path(tmp), setup)
            if reached[version_id] & keys:
                found.append(version_id)
    return found


def _reached_by(store: Store, config: Config, version: Version, tmp: Path, setup: list[str]) -> set[str]:
    """The shared objects a live version reaches, as store keys.

    A version reaches every data file its metadata names, at any snapshot the
    metadata keeps, not only those it reads at its own snapshot.
    """
    # files.json names them without the metadata, when it holds `named`.
    named = _named_files(store, version)
    local = None
    if named is None or _asset_columns(store, version):
        local = _fetch_catalog(store, version, tmp)
    reached: set[str] = set()
    if named is not None:
        reached.update(config.data_dir + key for key in named)
    elif local is not None:
        reached.update(config.data_dir + key for key in catalog.data_files(local))
    else:
        raise RepoError(f"{version.id} is live but has neither files.json with `named` nor a metadata file to say what it reaches")
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
    placed = {
        key: when
        for prefix in (config.data_dir, *_HASHED_DIRS.values())
        for key, when in store.placed(prefix).items()
    }
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

    Data files left by working metadata files that were never pushed, and those only
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
    """The keys of the files the version reaches: its `tree.json` and its asset columns."""
    reached: set[str] = set()
    if version.tree is not None:
        try:
            listed = json.loads(store.get(layout.version_dir(version.id) + version.tree.path))
        except Exception as exc:  # noqa: BLE001 - stores raise their own types
            if not is_missing(exc):
                raise
        else:
            reached.update(objects.tree_key(item["sha256"]) for item in listed["entries"])
    columns = _asset_columns(store, version)
    if columns and local is not None:
        values = objects.asset_values(local, columns, data_path=data_url(store), setup=setup)
        reached.update(objects.asset_key(sha256) for sha256 in values)
    return reached


def withdraw(
    store: Store,
    version_ids: Iterable[str],
    *,
    plan: Withdrawal | None = None,
    by: str | None = None,
    message: str | None = None,
) -> Withdrawal:
    """Remove versions that hold data that must not be published.

    Unlike retention it takes live versions and deletes their data files and
    assets at once. In the spec's order: the shared objects go before the
    metadata files, so a run that stops half way can be run again while the
    metadata files still say which objects they are. `version.json` stays.
    Each `expired.json` names the deleted objects, so that mirrors delete the
    same ones and hosts refuse to place them again.
    """
    plan = plan or plan_withdrawal(store, version_ids)
    if plan.blocked:
        raise RepoError(f"{plan.blocked[0]} points at a version to withdraw. Move it to a version without the data first")
    for name in plan.tags:
        refs.delete(store, name)
    if plan.tags:
        update_refs_index(store)
    # Assets and tree files first: the data files are what says which assets
    # the versions' rows reach, so a run that stops half way can still work them out.
    hashed = [key for key in plan.deleted if key.startswith(tuple(_HASHED_DIRS.values()))]
    store.delete(hashed)
    store.delete([key for key in plan.deleted if key not in set(hashed)])
    deleted = deleted_names(plan.deleted, read_config(store).data_dir)
    for version_id in plan.versions:
        expire(store, version_id, reason="withdrawn", by=by, message=message, deleted=deleted)
    return plan


def _version_ids(store: Store) -> list[str]:
    return _version_ids_in(store.list("versions/"))


def _version_ids_in(keys: Iterable[str]) -> list[str]:
    suffix = "/" + layout.VERSION_RECORD
    ids = [key[len("versions/") : -len(suffix)] for key in keys if key.endswith(suffix)]
    return [version_id for version_id in ids if layout.is_version_id(version_id)]


def _fetch_catalog(store: Store, version: Version, tmp: Path) -> Path | None:
    """The version's metadata in ``tmp``, or ``None`` when it is gone."""
    local = tmp / f"{version.id}.duckdb"
    try:
        local.write_bytes(store.get(layout.version_metadata(version)))
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if is_missing(exc):
            return None
        raise
    return local


def _named_files(store: Store, version: Version) -> set[str] | None:
    """Every data file the version's metadata names, from `named` in its `files.json`.

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


def plan_retention(
    store: Store,
    *,
    keep: int = 1,
    since: str | None = None,
    grace: dt.timedelta = dt.timedelta(days=7),
    now: dt.datetime | None = None,
) -> list[str]:
    """The live versions to take out of retention (Data files, "Choosing the versions to take out").

    Kept are the versions a ref points at, the merge bases of each ref with
    main, the ``keep`` newest on each branch's first-parent line, those made at
    or after ``since`` (an ISO time, as ``created_at`` is written), and those
    made within ``grace``. Nothing is taken out before main has a version.
    Returns the IDs to take out, newest first.
    """
    if keep < 1:
        raise ValueError(f"keep must be >= 1, got {keep}")
    targets = _refs(store)
    main = targets.get(layout.DEFAULT_BRANCH)
    if main is None:
        return []
    known: dict[str, Version] = {}

    def read(version_id: str) -> Version:
        if version_id not in known:
            known[version_id] = read_version(store, version_id)
        return known[version_id]

    kept = set(targets.values())
    behind_main = _ancestors(read, main)
    for name, target in targets.items():
        if target != main:
            kept.update(_bases(behind_main, _ancestors(read, target)))
        if name.startswith(layout.BRANCH_PREFIX):
            kept.update(_first_parents(read, target, keep))
    cutoff = _utc(now or dt.datetime.now(dt.timezone.utc)) - grace
    after = _instant(since) if since is not None else None
    taken = []
    for version_id in _live_version_ids(store):
        if version_id in kept:
            continue
        made = _instant(read(version_id).created_at)
        if made >= cutoff or (after is not None and made >= after):
            continue
        taken.append(version_id)
    return sorted(taken, reverse=True)


def _ancestors(read: Callable[[str], Version], version_id: str) -> dict[str, Version]:
    """`ancestors`, reading each version through ``read``."""
    found: dict[str, Version] = {}
    pending = [version_id]
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found[current] = read(current)
        pending.extend(parent for parent in found[current].parents if parent not in found)
    return found


def _live_version_ids(store: Store) -> list[str]:
    """The versions not past retention, from one listing of `versions/`."""
    keys = set(store.list("versions/"))
    return [version_id for version_id in _version_ids_in(keys) if layout.version_expired(version_id) not in keys]


def prune(
    store: Store,
    *,
    keep: int = 1,
    since: str | None = None,
    grace: dt.timedelta = dt.timedelta(days=7),
    reason: str = "retention",
    by: str | None = None,
    message: str | None = None,
) -> list[str]:
    """Take out of retention the versions `plan_retention` chooses. Returns their IDs, newest first.

    The refs are read again after choosing, so a version a ref came to point
    at meanwhile is kept; `expire` checks each once more.
    """
    planned = plan_retention(store, keep=keep, since=since, grace=grace)
    held = _held(store)
    taken = [version_id for version_id in planned if version_id not in held]
    for version_id in taken:
        expire(store, version_id, reason=reason, by=by, message=message)
    return taken


def _first_parents(read: Callable[[str], Version], version_id: str, count: int) -> list[str]:
    """``version_id`` and the versions behind it by first parent, ``count`` in all at most."""
    found = [version_id]
    while len(found) < count:
        parents = read(found[-1]).parents
        if not parents:
            break
        found.append(parents[0])
    return found


def _refs(store: Store) -> dict[str, str]:
    """Every ref that holds a version, with the version."""
    found: dict[str, str] = {}
    for name in store.list("refs/"):
        try:
            ref = refs.read(store, name)
        except (refs.RefError, layout.LayoutError):
            continue
        if ref is not None:
            found[name] = ref.target
    return found


def _held(store: Store) -> dict[str, str]:
    """The versions some ref points at, each with one ref that does."""
    held: dict[str, str] = {}
    for name, target in _refs(store).items():
        held.setdefault(target, name)
    return held


def _instant(value: str) -> dt.datetime:
    """An ISO time; one without an offset is taken as UTC."""
    return _utc(dt.datetime.fromisoformat(value.replace("Z", "+00:00")))


def _utc(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)


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
        # As in `push`: a host may have stopped after a lost move.
        _write_ref_again(store, current)
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
        _write_ref_again(store, now)
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
    """The assets and tree files a version reaches: store key to media type, where it is known."""
    needed: dict[str, str] = {}
    if version.tree is not None:
        listed = json.loads((local / version.tree.path).read_text(encoding="utf-8"))
        for item in listed["entries"]:
            needed[objects.tree_key(item["sha256"])] = objects.media_type(item["path"])
    for sha256 in assets:
        needed.setdefault(objects.asset_key(sha256), "")
    return needed


def _upload_objects(store: Store, needed: dict[str, str], local: Mapping[str, Path]) -> None:
    """Put the assets and tree files the repository lacks, or refuse the push."""
    if not needed:
        return
    present = {key for prefix in _HASHED_DIRS.values() for key in store.list(prefix)}
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
