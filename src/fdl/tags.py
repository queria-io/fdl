"""The `fdl.` tags a version keeps in its metadata (see the specification's Tags).

Each is a row of DuckLake's ``ducklake_tag`` on a table or a view, with JSON
text as its value. DuckDB writes only ``comment`` tags in SQL, so these rows
are written here, straight into the frozen metadata. Never on a column: DuckDB
does not open a table with a column tag other than ``comment``.

Tags are keyed ``schema.name``, as in `files.json` and ``diff``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .catalog import CatalogError, _open

PRIMARY_KEY = "fdl.primary_key"
ASSETS = "fdl.assets"
CATALOGS = "fdl.catalogs"

Tags = dict[str, dict[str, Any]]


def from_dataset(described: dict | None) -> Tags:
    """The tags a `dataset.json` declares: primary keys and assets of tables, catalogs of views."""
    if not described:
        return {}
    catalogs = described.get("catalogs")

    def of(table: dict) -> dict[str, Any]:
        if table.get("kind") == "view":
            return {CATALOGS: catalogs} if catalogs else {}
        assets = [f["name"] for f in table.get("fields", []) if isinstance(f, dict) and f.get("asset") is True]
        return {
            **({PRIMARY_KEY: table["primary_key"]} if table.get("primary_key") else {}),
            **({ASSETS: assets} if assets else {}),
        }

    tagged = {name: of(table) for name, table in _entries(described).items()}
    return {name: values for name, values in tagged.items() if values}


def names(described: dict) -> set[str]:
    """The tables and views a `dataset.json` lists, keyed as tags are."""
    return set(_entries(described))


def _entries(described: dict) -> dict[str, dict]:
    return {
        f"{table.get('schema', 'main')}.{table['name']}": table
        for table in described.get("tables", [])
        if isinstance(table, dict) and table.get("name")
    }


def write(frozen: Path, tags: Tags, *, after: int | None) -> None:
    """Make ``tags`` the `fdl.` tags of the tables and views alive at the last snapshot.

    ``after`` is the parent's snapshot. The new rows are valid from a snapshot
    past it: when the last snapshot is the parent's, and the tags differ from
    those it holds, a snapshot is made for them, so that the version does not
    hold other tags under the parent's snapshot. Nothing is written when the
    tags are already these. Tags of other keys, such as DuckLake's ``comment``,
    are left as they are.
    """
    con = _open(Path(frozen))
    try:
        snapshot = con.execute("SELECT max(snapshot_id) FROM ducklake_snapshot").fetchone()[0]
        ids = _objects(con, snapshot)
        missing = sorted(set(tags) - set(ids))
        if missing:
            raise CatalogError(f"tags name tables or views the metadata lacks: {', '.join(missing)}")
        held = _read(con, snapshot)
        altered = changed(held, tags, set(held) | set(tags))
        if not altered:
            return
        if after is not None and snapshot <= after:
            snapshot = _new_snapshot(con, snapshot, sorted(ids[name] for name in altered))
        con.execute("DELETE FROM ducklake_tag WHERE key LIKE 'fdl.%' AND begin_snapshot >= ?", [snapshot])
        con.execute(
            "UPDATE ducklake_tag SET end_snapshot = ? WHERE key LIKE 'fdl.%' AND end_snapshot IS NULL",
            [snapshot],
        )
        for name, values in sorted(tags.items()):
            for key, value in sorted(values.items()):
                con.execute(
                    "INSERT INTO ducklake_tag VALUES (?, ?, NULL, ?, ?)",
                    [ids[name], snapshot, key, json.dumps(value, ensure_ascii=False, separators=(",", ":"))],
                )
    finally:
        con.close()


def _new_snapshot(con, last: int, altered: list[int]) -> int:
    """Add a snapshot after ``last`` that changes nothing but the tags, as `COMMENT ON` would."""
    snapshot = last + 1
    con.execute(
        """
        INSERT INTO ducklake_snapshot
        SELECT ?, now(), schema_version + 1, next_catalog_id, next_file_id
        FROM ducklake_snapshot WHERE snapshot_id = ?
        """,
        [snapshot, last],
    )
    con.execute(
        "INSERT INTO ducklake_snapshot_changes (snapshot_id, changes_made) VALUES (?, ?)",
        [snapshot, ",".join(f"altered_table:{object_id}" for object_id in altered)],
    )
    return snapshot


def read(frozen: Path, snapshot: int) -> Tags:
    """The `fdl.` tags of the tables and views alive at ``snapshot``."""
    con = _open(Path(frozen))
    try:
        return _read(con, snapshot)
    finally:
        con.close()


def _read(con, snapshot: int) -> Tags:
    by_id = {object_id: name for name, object_id in _objects(con, snapshot).items()}
    rows = con.execute(
        """
        SELECT object_id, key, value FROM ducklake_tag
        WHERE key LIKE 'fdl.%' AND begin_snapshot <= ? AND (end_snapshot IS NULL OR end_snapshot > ?)
        """,
        [snapshot, snapshot],
    ).fetchall()
    found: Tags = {}
    for object_id, key, value in rows:
        if object_id in by_id:
            found.setdefault(by_id[object_id], {})[key] = json.loads(value)
    return found


def changed(old: Tags, new: Tags, held: set[str]) -> list[str]:
    """The tables and views in ``held`` (those both versions hold) whose tags differ."""
    return sorted(name for name in held if old.get(name, {}) != new.get(name, {}))


def _objects(con, snapshot: int) -> dict[str, int]:
    live = "begin_snapshot <= $s AND (end_snapshot IS NULL OR end_snapshot > $s)"
    rows = con.execute(
        f"""
        SELECT s.schema_name || '.' || o.name, o.id FROM (
            SELECT table_id AS id, table_name AS name, schema_id FROM ducklake_table WHERE {live}
            UNION ALL
            SELECT view_id, view_name, schema_id FROM ducklake_view WHERE {live}
        ) o JOIN ducklake_schema s ON s.schema_id = o.schema_id
        """,
        {"s": snapshot},
    ).fetchall()
    return dict(rows)
