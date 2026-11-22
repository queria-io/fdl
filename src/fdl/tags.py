"""The `fdl.` tags of versions written before the `_fdl` schema (see the specification's
"Tags of earlier versions").

Each is a row of DuckLake's ``ducklake_tag`` on a table or a view, with JSON text as
its value. FDL no longer writes them: it reads them, and the `dataset.json` of
versions older still, to write what they said into `_fdl` (see `fdl_schema`).

Tags are keyed ``schema.name``, as in `files.json` and ``diff``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .catalog import _open

PRIMARY_KEY = "fdl.primary_key"
ASSETS = "fdl.assets"
CATALOGS = "fdl.catalogs"
PROFILE = "fdl.profile"

Tags = dict[str, dict[str, Any]]


def from_dataset(described: dict | None) -> Tags:
    """The tags a `dataset.json` declares: primary keys and assets of tables, catalogs of views,
    and `"profile": false` of either."""
    if not described:
        return {}
    catalogs = described.get("catalogs")

    def of(table: dict) -> dict[str, Any]:
        unprofiled = {PROFILE: False} if table.get("profile") is False else {}
        if table.get("kind") == "view":
            return {**({CATALOGS: catalogs} if catalogs else {}), **unprofiled}
        assets = [f["name"] for f in table.get("fields", []) if isinstance(f, dict) and f.get("asset") is True]
        return {
            **({PRIMARY_KEY: table["primary_key"]} if table.get("primary_key") else {}),
            **({ASSETS: assets} if assets else {}),
            **unprofiled,
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


def read(metadata: Path, snapshot: int) -> Tags:
    """The `fdl.` tags of the tables and views alive at ``snapshot``, in a frozen or working metadata.

    Empty for a metadata that has the schema `_fdl` at ``snapshot``: its tags are ignored there.
    """
    metadata = Path(metadata)
    if metadata.suffix == ".sqlite":
        import duckdb

        con = duckdb.connect()
        con.execute("INSTALL sqlite; LOAD sqlite;")
        con.execute(f"ATTACH '{str(metadata).replace(chr(39), chr(39) * 2)}' AS w (TYPE sqlite, READ_ONLY)")
        con.execute("USE w")
    else:
        con = _open(metadata)
    try:
        if _has_fdl_schema(con, snapshot):
            return {}
        return _read(con, snapshot)
    finally:
        con.close()


def _has_fdl_schema(con, snapshot: int) -> bool:
    return bool(
        con.execute(
            """
            SELECT count(*) FROM ducklake_schema
            WHERE schema_name = '_fdl' AND begin_snapshot <= ? AND (end_snapshot IS NULL OR end_snapshot > ?)
            """,
            [snapshot, snapshot],
        ).fetchone()[0]
    )


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
