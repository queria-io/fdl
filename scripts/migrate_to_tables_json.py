"""A one-off migration of a dataset to `tables.json`, outside the fdl package.

    uv run python scripts/migrate_to_tables_json.py REMOTE

Versions written by fdl-cli 0.33 and earlier keep their declarations in
`files.json` and the `fdl.` tags of their metadata, or, the earliest, in
`dataset.json`; some name their metadata `ducklake.duckdb` or keep earlier
snapshots in it, and some list other files in `tree.json`. `migrate` brings each
live version to the current layout, in place and under the same ID. Run it
once per dataset, with nobody pushing meanwhile; running it again skips what
is done:

1. Read the declarations from the tags, `files.json` or `dataset.json`.
2. Freeze the metadata again as `metadata.db` with one snapshot and no `fdl.` tags.
3. Write `tables.json`.
4. Write `version.json` again: `tables` in place of `files`, `dataset` and `tree`,
   `diff.tags` as `diff.declarations`, without `inputs`. Its signature goes.
5. Delete `files.json`, `dataset.json`, `tree.json` and the old metadata, and,
   once no version has a `tree.json`, `objects/`.

A version past retention keeps only its record, which is written again in the
same shape without `tables`. This script goes once every dataset is migrated.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

from fdl import catalog, declared, layout, repo
from fdl.catalog import _open
from fdl.cli import _read_setup
from fdl.store import Store, is_missing
from fdl.stores import open_store
from fdl.version import DuckLake, File, Metadata, Version

#: What earlier versions kept beside the record, deleted once migrated.
_EARLIER_FILES = ("files.json", "dataset.json", "tree.json", "version.json.sig", "ducklake.duckdb")
_EARLIER_DIRS = ("catalogs/",)
_OBJECTS_DIR = "objects/"


def migrate(store: Store, *, setup: Iterable[str] = (), echo=print) -> list[str]:
    """Migrate every version of the dataset that is not migrated yet. Returns their IDs."""
    setup = list(setup)
    done: list[str] = []
    for version_id in repo._version_ids(store):
        record = json.loads(store.get(layout.version_record(version_id)))
        if _is_current(record):
            continue
        if repo.is_expired(store, version_id):
            _write_record(store, _version(record, metadata=_metadata(record), tables=None))
        else:
            _migrate_live(store, record, setup)
        _delete_earlier(store, version_id)
        echo(f"migrated {version_id}")
        done.append(version_id)
    leftovers = store.list(_OBJECTS_DIR)
    if leftovers and not any(
        "tree" in json.loads(store.get(layout.version_record(v))) for v in repo._version_ids(store)
    ):
        store.delete(leftovers)
        echo(f"deleted {len(leftovers)} file(s) under {_OBJECTS_DIR}")
    return done


def _is_current(record: dict) -> bool:
    earlier = ("files", "dataset", "tree", "catalog", "inputs")
    return "metadata" in record and not any(key in record for key in earlier) and "tags" not in (record.get("diff") or {})


def _metadata(record: dict) -> Metadata:
    held = record.get("metadata") or record["catalog"]
    return Metadata(path=held["path"], storage=held["storage"], bytes=int(held["bytes"]), sha256=held["sha256"])


def _version(record: dict, *, metadata: Metadata, tables: File | None) -> Version:
    diff = dict(record["diff"]) if isinstance(record.get("diff"), dict) else None
    if diff is not None:
        if "tags" in diff:
            diff["declarations"] = diff.pop("tags")
        diff.pop("dataset", None)
        diff.pop("descriptions", None)
    ducklake = record["ducklake"]
    return Version(
        fdl=record["fdl"],
        id=record["id"],
        seq=int(record["seq"]),
        parents=tuple(record["parents"]),
        created_at=record["created_at"],
        author=dict(record["author"]),
        message=record["message"],
        metadata=metadata,
        ducklake=DuckLake(
            format=ducklake["format"], snapshot_id=int(ducklake["snapshot_id"]), snapshots=tuple(ducklake["snapshots"])
        ),
        diff=diff,
        annotations=dict(record.get("annotations", record.get("extensions", {}))),
        tables=tables,
    )


def _write_record(store: Store, version: Version) -> None:
    store.put(layout.version_record(version.id), version.to_json(), content_type="application/json")


def _migrate_live(store: Store, record: dict, setup: list[str]) -> None:
    version_id = record["id"]
    folder = layout.version_dir(version_id)
    held = _metadata(record)
    with tempfile.TemporaryDirectory(prefix="fdl-migrate-") as tmp:
        target = Path(tmp) / "version"
        target.mkdir()
        frozen = target / layout.METADATA
        frozen.write_bytes(store.get(folder + held.path))
        if hashlib.sha256(frozen.read_bytes()).hexdigest() != held.sha256:
            raise repo.RepoError(f"{version_id}: the metadata's sha256 is not the one version.json records")
        snapshot = int(record["ducklake"]["snapshot_id"])
        facts = _declarations(store, record, frozen, snapshot)
        catalog.keep_last_snapshot(frozen)
        _drop_tags(frozen)
        described = declared.describe(frozen, facts, data_path=repo.data_url(store), setup=setup)
        tables = repo._write_tables_index(target, frozen, snapshot, described)
        metadata = Metadata(
            path=layout.METADATA,
            storage=catalog.storage_version(frozen),
            bytes=frozen.stat().st_size,
            sha256=hashlib.sha256(frozen.read_bytes()).hexdigest(),
        )
        # The files first, the record that names them last.
        store.put_file(folder + layout.METADATA, frozen, content_type="application/octet-stream")
        store.put_file(folder + layout.TABLES, target / layout.TABLES, content_type="application/json")
        _write_record(store, _version(record, metadata=metadata, tables=tables))
        shutil.rmtree(target, ignore_errors=True)


def _delete_earlier(store: Store, version_id: str) -> None:
    folder = layout.version_dir(version_id)
    keys = [folder + name for name in _EARLIER_FILES]
    for directory in _EARLIER_DIRS:
        keys += store.list(folder + directory)
    store.delete(keys)


def _json(store: Store, key: str) -> dict | None:
    try:
        return json.loads(store.get(key))
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if is_missing(exc):
            return None
        raise


# -- the declarations of earlier versions ----------------------------------------------


def _declarations(store: Store, record: dict, frozen: Path, snapshot: int) -> declared.Facts:
    folder = layout.version_dir(record["id"])
    if record.get("dataset"):
        return _from_tags(_tags_of_dataset(_json(store, folder + record["dataset"]["path"])))
    index = _json(store, folder + record["files"]["path"]) if record.get("files") else None
    return declared.from_index(index).union(_from_tags(_read_tags(frozen, snapshot)))


def _from_tags(tags: dict[str, dict[str, Any]]) -> declared.Facts:
    def names(value: Any) -> bool:
        return isinstance(value, list) and bool(value) and all(isinstance(v, str) for v in value)

    dependencies: dict[str, dict[str, str]] = {}
    for values in tags.values():
        found = values.get("fdl.catalogs")
        if isinstance(found, dict):
            dependencies.update({k: {"url": v} for k, v in found.items() if isinstance(v, str)})
    return declared.Facts(
        primary_keys={n: list(v["fdl.primary_key"]) for n, v in tags.items() if names(v.get("fdl.primary_key"))},
        assets={n: list(v["fdl.assets"]) for n, v in tags.items() if names(v.get("fdl.assets"))},
        dependencies=dependencies,
        unprofiled=frozenset(n for n, v in tags.items() if v.get("fdl.profile") is False),
    )


def _tags_of_dataset(described: dict | None) -> dict[str, dict[str, Any]]:
    """What a `dataset.json` declares, as the tags that followed it say it."""
    if not described:
        return {}
    catalogs = described.get("catalogs")
    found: dict[str, dict[str, Any]] = {}
    for table in described.get("tables", []):
        if not isinstance(table, dict) or not table.get("name"):
            continue
        values: dict[str, Any] = {"fdl.profile": False} if table.get("profile") is False else {}
        if table.get("kind") == "view":
            if catalogs:
                values["fdl.catalogs"] = catalogs
        else:
            assets = [f["name"] for f in table.get("fields", []) if isinstance(f, dict) and f.get("asset") is True]
            if table.get("primary_key"):
                values["fdl.primary_key"] = table["primary_key"]
            if assets:
                values["fdl.assets"] = assets
        if values:
            found[f"{table.get('schema', 'main')}.{table['name']}"] = values
    return found


def _read_tags(metadata: Path, snapshot: int) -> dict[str, dict[str, Any]]:
    """The `fdl.` tags of the tables and views alive at ``snapshot``."""
    con = _open(metadata)
    live = "begin_snapshot <= $s AND (end_snapshot IS NULL OR end_snapshot > $s)"
    try:
        if not catalog._table_exists(con, "ducklake_tag"):
            return {}
        objects = dict(
            con.execute(
                f"""
                SELECT o.id, s.schema_name || '.' || o.name FROM (
                    SELECT table_id AS id, table_name AS name, schema_id FROM ducklake_table WHERE {live}
                    UNION ALL
                    SELECT view_id, view_name, schema_id FROM ducklake_view WHERE {live}
                ) o JOIN ducklake_schema s ON s.schema_id = o.schema_id
                """,
                {"s": snapshot},
            ).fetchall()
        )
        rows = con.execute(
            f"SELECT object_id, key, value FROM ducklake_tag WHERE key LIKE 'fdl.%' AND {live}", {"s": snapshot}
        ).fetchall()
    finally:
        con.close()
    found: dict[str, dict[str, Any]] = {}
    for object_id, key, value in rows:
        if object_id in objects:
            found.setdefault(objects[object_id], {})[key] = json.loads(value)
    return found


def _drop_tags(metadata: Path) -> None:
    """Delete the `fdl.` tags: `tables.json` holds the declarations now."""
    con = _open(metadata)
    try:
        if catalog._table_exists(con, "ducklake_tag"):
            con.execute("DELETE FROM ducklake_tag WHERE key LIKE 'fdl.%'")
            con.execute("CHECKPOINT")
    finally:
        con.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: migrate_to_tables_json.py REMOTE")
    store = open_store(sys.argv[1])
    done = migrate(store, setup=_read_setup(store))
    print(f"{len(done)} version(s) migrated" if done else "Nothing to migrate")
