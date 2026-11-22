"""What changed between two versions, or in a working catalog since its clone.

Both are brought to one shape first: each table's row count, data files and
columns. A version gives them from its `tables.json`; a catalog gives them from
two of its snapshots. Then one comparison lists the differences, in four kinds:

- tables added and dropped
- row counts
- columns added, dropped, renamed or given another type (``changes``)
- data files: a table whose files differ, even with the same rows

Between two versions, and in a commit's own diff, ``declarations`` lists the
tables and views whose declarations changed, and ``dependencies`` the other
datasets the views read whose entry changed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from . import catalog, layout
from .store import Store, is_missing
from .version import Version

#: A table as compared: rows, data files and columns (name to type).
Table = dict[str, Any]


def from_version(index: dict | None) -> dict[str, Table]:
    """Each table of a version, from its `tables.json`."""
    return {
        name: {
            "rows": sum(f["rows"] - f.get("deletes", {}).get("rows", 0) for f in table.get("files", [])),
            "files": _file_set(table),
            "columns": {c["name"]: c.get("type") for c in table.get("columns", [])},
        }
        for name, table in (index or {}).get("tables", {}).items()
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
        }
        for name, table in listed.items()
    }


def between(
    catalog_path: Path,
    before: int | None,
    after: int,
) -> dict[str, Any] | None:
    """The differences between two snapshots of one catalog, ``before`` ``None`` for none.

    What a commit records as its version's ``diff``. ``None`` when the catalog no longer holds
    ``before``: once expired, its tables and files cannot be read.
    """
    if before is not None and not catalog.has_snapshot(catalog_path, before):
        return None
    new = from_catalog(catalog_path, after)
    if before is None:
        return compare({}, new)
    return compare(
        from_catalog(catalog_path, before),
        new,
        changes=catalog.column_changes(catalog_path, before, after),
    )


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
    return {
        "tables": {
            "added": sorted(new.keys() - old.keys()),
            "dropped": sorted(old.keys() - new.keys()),
        },
        "rows": rows,
        "columns": columns,
        "data": data,
    }


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
        + [f"~ declarations {name}" for name in result.get("declarations", [])]
        + [f"~ dependency {name}" for name in result.get("dependencies", [])]
    )


# -- reading what a version holds ---------------------------------------------


def _get_json(store: Store, key: str) -> dict | None:
    try:
        return json.loads(store.get(key))
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if is_missing(exc):
            return None
        raise


def indexed(store: Store, version: Version) -> dict | None:
    """A version's `tables.json`, or ``None`` past retention."""
    return _get_json(store, layout.version_dir(version.id) + version.tables.path) if version.tables else None


def versions(store: Store, old: Version | None, new: Version) -> dict[str, Any]:
    """The differences from ``old`` to ``new``. Before the first version ``old`` is ``None``.

    Against its first parent (or before the first version) a version's own
    ``diff`` is the answer; otherwise, or for a version written without one,
    the two are compared from their files.
    """
    if new.diff is not None and (new.parents[:1] == ((old.id,) if old else ())):
        return new.diff
    before, after = (indexed(store, old) if old else None), indexed(store, new)
    from . import declared

    old_facts, new_facts = declared.from_index(before), declared.from_index(after)
    return {
        **compare(from_version(before), from_version(after)),
        "declarations": declared.changed(old_facts, new_facts, _names(before) & _names(after)),
        "dependencies": declared.dependencies_changed(old_facts, new_facts),
    }


def _names(index: dict | None) -> set[str]:
    return set((index or {}).get("tables", {})) | set((index or {}).get("views", {}))


def working(working_path: Path, base: Version | None) -> dict[str, Any]:
    """What a working catalog changed since the version it was cloned at."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="fdl-status-") as tmp:
        frozen = Path(tmp) / "status.duckdb"
        catalog.convert(working_path, frozen, data_path=catalog.working_data_path(working_path))
        last = catalog.snapshot_range(frozen, None)[1]
        result = between(frozen, base.ducklake.snapshot_id if base else None, last)
        if result is None:
            raise catalog.CatalogError(
                "the working catalog no longer holds the snapshot it was cloned at"
            )
        return result
