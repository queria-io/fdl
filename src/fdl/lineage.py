"""`depends_on` read from a table's SQL.

A table that has `sql` and no `depends_on` gets the tables its SQL reads, as
DuckDB parses them. A table of this dataset is written `schema.name`. A table
of another dataset is written `<repository URL>#schema.name`, where the SQL
names that dataset by the alias it is attached under and `catalogs` in the
description maps the alias to the repository.

Names are matched as DuckDB binds them: without regard to case, and an
unqualified name is looked for in the reading table's own schema first.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Iterator

import duckdb

Key = tuple[str, str]
#: (catalog, schema, table) as the SQL names it; a part left out is "".
Reference = tuple[str, str, str]

DEFAULT_SCHEMA = "main"
#: How DuckLake writes its own catalog in the SQL of a view it stores.
THIS_CATALOG = "{DUCKLAKE_CATALOG}"


def _tables(node: Any, ctes: frozenset[str]) -> Iterator[Reference]:
    """The base tables under ``node``, less the CTEs in scope where each is read."""
    if isinstance(node, list):
        for value in node:
            yield from _tables(value, ctes)
        return
    if not isinstance(node, dict):
        return
    cte_map = node.get("cte_map")
    if isinstance(cte_map, dict):
        ctes = ctes | {
            str(name).lower()
            for entry in cte_map.get("map", [])
            if isinstance(entry, dict) and (name := entry.get("key"))
        }
    table = node.get("table_name")
    if node.get("type") == "BASE_TABLE" and isinstance(table, str):
        catalog, schema = node.get("catalog_name") or "", node.get("schema_name") or ""
        if catalog or schema or table.lower() not in ctes:
            yield (catalog, schema, table)
    for value in node.values():
        yield from _tables(value, ctes)


def references(con: duckdb.DuckDBPyConnection, sql: str) -> list[Reference] | None:
    """The tables ``sql`` reads, less the names of its CTEs. None when DuckDB cannot parse it."""
    # The bare placeholder does not parse; quoted, it is a catalog no alias can name.
    readable = sql.replace(THIS_CATALOG, f'"{THIS_CATALOG}"')
    try:
        (serialized,) = con.execute("SELECT json_serialize_sql(?)", [readable]).fetchone()
    except duckdb.Error:
        return None
    parsed = json.loads(serialized)
    if parsed.get("error"):
        return None
    return list(_tables(parsed, frozenset()))


def resolve(
    reference: Reference, home: str, own: dict[Key, str], catalogs: dict[str, str]
) -> str | None:
    """``reference`` as an entry of `depends_on`, or None when it names nothing known.

    ``home`` is the schema of the table that reads it, ``own`` maps each table
    of this dataset, by its lower-case (schema, name), to how it is written in
    `depends_on`, and ``catalogs`` maps each lower-case alias to its repository.
    """
    catalog, schema, table = reference
    lowered = (catalog.lower(), schema.lower(), table.lower())
    if catalog:
        if catalog != THIS_CATALOG and lowered[0] in catalogs:
            return f"{catalogs[lowered[0]]}#{schema}.{table}"
        # Any other catalog is the one the writer attached this dataset under.
        return own.get(lowered[1:])
    if schema:
        # `a.b` is a schema and a table, or else an attached catalog and a table in main.
        if lowered[1:] in own:
            return own[lowered[1:]]
        return f"{catalogs[lowered[1]]}#{DEFAULT_SCHEMA}.{table}" if lowered[1] in catalogs else None
    return own.get((home.lower(), lowered[2])) or own.get((DEFAULT_SCHEMA, lowered[2]))


def depends_on(
    con: duckdb.DuckDBPyConnection,
    key: Key,
    sql: str,
    own: dict[Key, str],
    catalogs: dict[str, str],
) -> list[str] | None:
    """What the table ``key`` reads, by its ``sql``. None when the SQL cannot be parsed."""
    found = references(con, sql)
    if found is None:
        print(f"fdl: {key[0]}.{key[1]}: could not read depends_on from its sql", file=sys.stderr)
        return None
    resolved = {resolve(reference, key[0], own, catalogs) for reference in found}
    return sorted(entry for entry in resolved if entry and entry != f"{key[0]}.{key[1]}")


def fill(tables: list[dict[str, Any]], catalogs: dict[str, str]) -> list[dict[str, Any]]:
    """``tables`` with `depends_on` read from the `sql` of those that do not have it."""
    pending = [
        i for i, t in enumerate(tables) if "depends_on" not in t and isinstance(t.get("sql"), str)
    ]
    if not pending:
        return tables
    own = {(t["schema"].lower(), t["name"].lower()): f"{t['schema']}.{t['name']}" for t in tables}
    aliases = {alias.lower(): url for alias, url in catalogs.items()}
    filled = list(tables)
    con = duckdb.connect()
    try:
        for i in pending:
            table = tables[i]
            found = depends_on(con, (table["schema"], table["name"]), table["sql"], own, aliases)
            if found is not None:
                filled[i] = {**table, "depends_on": found}
    finally:
        con.close()
    return filled
