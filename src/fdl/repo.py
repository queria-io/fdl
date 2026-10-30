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
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from . import catalog, layout, refs
from .store import Store, is_conflict, is_missing
from .version import Catalog, DuckLake, Version, new_id, parse, utc_now


class RepoError(RuntimeError):
    """The repository cannot do what was asked, and nothing was changed."""


class NotFastForward(RepoError):
    """The version is not built on what the ref holds now. Pull and build again."""


@dataclass(frozen=True)
class Config:
    fdl: str
    data_dir: str


def init(store: Store, *, data_dir: str = layout.DEFAULT_DATA_DIR) -> Config:
    """Start a repository: `fdl.json` and HEAD, and no versions yet.

    An existing repository is left as it is and its settings returned, so
    running this twice is harmless.
    """
    if not data_dir.endswith("/"):
        raise RepoError(f"data_dir has to end with '/': {data_dir!r}")
    record = {"fdl": layout.FDL_VERSION, "data_dir": data_dir}
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
    return Config(fdl=record["fdl"], data_dir=record.get("data_dir", layout.DEFAULT_DATA_DIR))


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


def commit(
    working: Path,
    out_dir: Path,
    *,
    parent: Version | None,
    data_path: str,
    author: dict,
    message: str,
    inputs: tuple[dict, ...] = (),
    extensions: dict | None = None,
) -> Version:
    """Freeze the working catalog into a version under ``out_dir/<ID>/``.

    ``data_path`` is where readers find the data files (the remote's root and
    data_dir), written into the frozen catalog. Every file reference must be
    relative to it, or the version would point readers at the writer's disk.
    Nothing is uploaded: that is `push`.
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    frozen = Path(out_dir) / f".freezing-{new_id()}.duckdb"
    try:
        catalog.convert(working, frozen, data_path=data_path)
        catalog.set_data_path(frozen, data_path)
        absolute = catalog.absolute_paths(frozen)
        if absolute:
            raise RepoError(
                f"{len(absolute)} file(s) are recorded with an absolute path "
                f"(first: {absolute[0].path}); rebuild so they sit under the data path"
            )
        return record(
            frozen,
            out_dir,
            parent=parent,
            author=author,
            message=message,
            inputs=inputs,
            extensions=extensions,
        )
    finally:
        frozen.unlink(missing_ok=True)


def checkout(store: Store, working: Path, *, ref: str = layout.HEAD) -> Version | None:
    """Make a working catalog from the version ``ref`` holds.

    Before the first version there is nothing to copy, and an empty working
    catalog is created instead. Returns the version checked out, which a
    commit from this working catalog builds on.
    """
    working = Path(working)
    config = read_config(store)
    data_path = _data_url(store, config)
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
        catalog.convert(frozen, working, data_path=data_path)
    finally:
        frozen.unlink(missing_ok=True)
    return version


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
    extensions: dict | None = None,
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
            inputs=tuple(inputs),
            extensions=dict(extensions or {}),
        )
        (target / layout.VERSION_RECORD).write_bytes(version.to_json())
    except BaseException:
        # Whatever stopped it (a full disk included), a half-made version is a
        # copy of the catalog that nothing will ever read.
        shutil.rmtree(target, ignore_errors=True)
        raise
    return version


def push(
    store: Store,
    local: Path,
    version: Version,
    *,
    ref: str = layout.DEFAULT_BRANCH,
    move_ref: bool = True,
) -> None:
    """Upload a committed version, then move ``ref`` to it.

    In the order the format asks for: the data files must already be there,
    then the catalog, then `version.json`, and the ref last. A push stopped
    half way leaves files no ref points at, which readers never see.

    ``move_ref=False`` uploads the version and stops: for a host that moves
    its refs itself (after an approval, say).
    """
    config = read_config(store)
    current_ref = refs.read(store, layout.check_ref(ref))
    if current_ref is not None and current_ref.target == version.id:
        # This push again, after a run whose answer was lost.
        return
    _refuse_unless_fast_forward(current_ref, version)

    frozen = Path(local) / layout.CATALOG
    missing = _missing_data_files(store, config, frozen)
    if missing:
        raise RepoError(
            f"{len(missing)} data file(s) the catalog names are not in the "
            f"repository (first: {missing[0]}). Rebuild from the working "
            f"catalog and commit again."
        )
    upload(store, local, version)
    if not move_ref:
        return
    try:
        refs.write(store, ref, version.id, expected=current_ref)
    except refs.RefConflict as exc:
        # A write sent again after a lost answer is refused by its own first
        # attempt. That moved the ref to this version, which is what was asked.
        now = refs.read(store, ref)
        if now is not None and now.target == version.id:
            return
        raise NotFastForward(f"{ref} moved during the push. Pull and build again.") from exc


def upload(store: Store, local: Path, version: Version) -> None:
    """Put a committed version's files, catalog first and the record last.

    No checks and no ref: for a host that checks and moves its refs itself.
    """
    _put_once(store, layout.version_catalog(version.id), Path(local) / layout.CATALOG)
    _put_once(store, layout.version_record(version.id), Path(local) / layout.VERSION_RECORD)


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
    store.delete(
        [
            layout.version_catalog(version_id),
            *store.list(layout.version_dir(version_id) + layout.CATALOGS_DIR),
        ]
    )


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
