"""What changed between two versions, or in a working catalog since its checkout.

Both are brought to one shape first: each table's row count, data files,
columns and words. A version gives them from its `dataset.json` and
`files.json`; a working catalog gives them from two of its snapshots. Then one
comparison lists the differences, in six kinds:

- tables added and dropped
- row counts
- columns added, dropped, renamed or given another type (``changes``)
- data files: a table whose files differ, even with the same rows
- descriptions: the dataset's, a table's or a column's title or description
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from . import catalog, layout
from .store import Store, is_missing
from .version import Version

#: A table as compared: rows, data files, columns (name to type) and words.
Table = dict[str, Any]


def _text(item: dict) -> tuple[str, str]:
    return (item.get("title") or "", item.get("description") or "")


def from_version(dataset: dict | None, files: dict | None) -> dict[str, Table]:
    """Each table of a version, from its `dataset.json` and `files.json`."""
    indexed = (files or {}).get("tables", {})
    described = {
        f"{table.get('schema')}.{table.get('name')}": table
        for table in (dataset or {}).get("tables", [])
        if table.get("kind") != "view"
    }
    names = described.keys() | indexed.keys()
    return {
        name: {
            "rows": described.get(name, {}).get("rows"),
            "files": _file_set(indexed[name]) if name in indexed else None,
            "columns": {
                field["name"]: field.get("type")
                for field in described.get(name, {}).get("fields", [])
            }
            if name in described
            else None,
            "text": _text(described[name]) if name in described else None,
            "field_text": {
                field["name"]: _text(field) for field in described.get(name, {}).get("fields", [])
            },
        }
        for name in names
    }


def from_catalog(catalog_path: Path, snapshot: int) -> dict[str, Table]:
    """Each table of a catalog at ``snapshot``: rows and data files, with columns."""
    listed = catalog.table_files(catalog_path, snapshot, absolute=True)
    columns = catalog.table_columns(catalog_path, snapshot)
    return {
        name: {
            "rows": sum(f["rows"] for f in table["files"])
            - sum(f["deletes"]["rows"] for f in table["files"] if "deletes" in f),
            "files": _file_set(table),
            "columns": columns.get(name, {}),
            "text": None,
            "field_text": {},
        }
        for name, table in listed.items()
    }


def _file_set(table: dict) -> frozenset[str]:
    files = table.get("files", [])
    return frozenset(f["path"] for f in files) | frozenset(
        f["deletes"]["path"] for f in files if "deletes" in f
    )


def _columns_by_name(name: str, old: dict | None, new: dict | None) -> list[dict]:
    """Column changes found by name, for when no ``changes`` were recorded."""
    if old is None or new is None:
        return []
    added = [
        {"table": name, "kind": "add_column", "column": c, "breaking": False}
        for c in new
        if c not in old
    ]
    dropped = [
        {"table": name, "kind": "drop_column", "column": c, "breaking": True}
        for c in old
        if c not in new
    ]
    retyped = [
        {
            "table": name,
            "kind": "change_type",
            "column": c,
            "from_type": old[c],
            "to_type": new[c],
            "breaking": catalog.is_breaking_type_change(old[c], new[c]),
        }
        for c in new
        if c in old and old[c] != new[c] and old[c] is not None and new[c] is not None
    ]
    return added + dropped + retyped


def compare(
    old: dict[str, Table],
    new: dict[str, Table],
    *,
    changes: Iterable[dict] | None = None,
    old_dataset: dict | None = None,
    new_dataset: dict | None = None,
) -> dict[str, Any]:
    """The differences from ``old`` to ``new``.

    ``changes`` are the column changes ``new`` recorded against ``old`` (its
    parent), which know renames; without them columns are compared by name.
    """
    kept = sorted(old.keys() & new.keys())
    recorded = list(changes) if changes is not None else None
    columns = (
        [c for c in recorded if c.get("table") in kept]
        if recorded is not None
        else [c for name in kept for c in _columns_by_name(name, old[name]["columns"], new[name]["columns"])]
    )
    rows = [
        {"table": name, "from": old[name]["rows"], "to": new[name]["rows"]}
        for name in kept
        if None not in (old[name]["rows"], new[name]["rows"]) and old[name]["rows"] != new[name]["rows"]
    ]
    data = [
        name
        for name in kept
        if None not in (old[name]["files"], new[name]["files"]) and old[name]["files"] != new[name]["files"]
    ]
    words = (["dataset"] if old_dataset is not None and new_dataset is not None and _text(old_dataset) != _text(new_dataset) else []) + [
        target
        for name in kept
        for target in (
            ([name] if None not in (old[name]["text"], new[name]["text"]) and old[name]["text"] != new[name]["text"] else [])
            + [
                f"{name}.{field}"
                for field, text in new[name]["field_text"].items()
                if field in old[name]["field_text"] and old[name]["field_text"][field] != text
            ]
        )
    ]
    return {
        "tables": {
            "added": sorted(new.keys() - old.keys()),
            "dropped": sorted(old.keys() - new.keys()),
        },
        "rows": rows,
        "columns": columns,
        "data": data,
        "descriptions": words,
    }


def is_empty(result: dict[str, Any]) -> bool:
    return not any(
        [result["tables"]["added"], result["tables"]["dropped"], result["rows"], result["columns"], result["data"], result["descriptions"]]
    )


def lines(result: dict[str, Any]) -> list[str]:
    """The differences as lines for a terminal, one change a line."""

    def column(change: dict) -> str:
        where = f"{change['table']}.{change['column']}"
        mark = "  (breaking)" if change.get("breaking") else ""
        kind = change.get("kind")
        if kind == "add_column":
            return f"+ column {where}{mark}"
        if kind == "drop_column":
            return f"- column {where}{mark}"
        if kind == "rename_column":
            return f"~ column {change['table']}.{change.get('from')} -> {change['column']}{mark}"
        if kind == "change_type":
            return f"~ column {where}: {change.get('from_type')} -> {change.get('to_type')}{mark}"
        return f"~ column {where}: {kind}{mark}"

    return (
        [f"+ table {name}" for name in result["tables"]["added"]]
        + [f"- table {name}" for name in result["tables"]["dropped"]]
        + [f"~ rows {r['table']}: {r['from']:,} -> {r['to']:,}" for r in result["rows"]]
        + [column(c) for c in result["columns"]]
        + [f"~ data {name}" for name in result["data"]]
        + [f"~ description {target}" for target in result["descriptions"]]
    )


# -- reading what a version holds ---------------------------------------------


def _get_json(store: Store, key: str) -> dict | None:
    try:
        return json.loads(store.get(key))
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if is_missing(exc):
            return None
        raise


def described(store: Store, version: Version) -> dict | None:
    """A version's `dataset.json`, or ``None`` for one written without it or past retention."""
    path = version.dataset.path if version.dataset else layout.DATASET
    return _get_json(store, layout.version_dir(version.id) + path)


def indexed(store: Store, version: Version) -> dict | None:
    """A version's `files.json`, or ``None`` without one."""
    return _get_json(store, layout.version_dir(version.id) + version.files.path) if version.files else None


def versions(store: Store, old: Version | None, new: Version) -> dict[str, Any]:
    """The differences from ``old`` to ``new``. Before the first version ``old`` is ``None``."""
    new_dataset = described(store, new)
    old_dataset = described(store, old) if old else {}
    return compare(
        from_version(old_dataset, indexed(store, old) if old else {}),
        from_version(new_dataset, indexed(store, new)),
        changes=new.changes if old is not None and new.parents[:1] == (old.id,) and new.changes else None,
        old_dataset=old_dataset if old else None,
        new_dataset=new_dataset if old else None,
    )


def working(working_path: Path, base: Version | None) -> dict[str, Any]:
    """What a working catalog changed since the version it was checked out at."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="fdl-status-") as tmp:
        frozen = Path(tmp) / "status.duckdb"
        catalog.convert(working_path, frozen, data_path=catalog.working_data_path(working_path))
        last = catalog.snapshot_range(frozen, None)[1]
        new = from_catalog(frozen, last)
        if base is None:
            return compare({}, new)
        return compare(
            from_catalog(frozen, base.ducklake.snapshot_id),
            new,
            changes=catalog.column_changes(frozen, base.ducklake.snapshot_id, last),
        )
