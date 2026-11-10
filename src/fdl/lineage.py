"""`depends_on` read from a table's SQL.

A table that has `sql` and no `depends_on` gets the tables its SQL reads, as
DuckDB parses them. A table of this dataset is written `schema.name`. A table
of another dataset is written `<repository URL>#schema.name`, where the SQL
names that dataset by the alias it is attached under and `catalogs` in the
description maps the alias to the repository.
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


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def references(con: duckdb.DuckDBPyConnection, sql: str) -> list[Reference] | None:
    """The tables ``sql`` reads, less the names of its CTEs. None when DuckDB cannot parse it."""
    # The placeholder does not parse; any catalog name not in `catalogs` reads as this dataset.
    readable = sql.replace(THIS_CATALOG, '"this"')
    try:
        (serialized,) = con.execute("SELECT json_serialize_sql(?)", [readable]).fetchone()
    except duckdb.Error:
        return None
    parsed = json.loads(serialized)
    if parsed.get("error"):
        return None
    nodes = list(_walk(parsed))
    ctes = {
        str(name)
        for node in nodes
        if isinstance(node.get("cte_map"), dict)
        for entry in node["cte_map"].get("map", [])
        if isinstance(entry, dict) and (name := entry.get("key"))
    }
    return [
        (node.get("catalog_name") or "", node.get("schema_name") or "", node["table_name"])
        for node in nodes
        if node.get("type") == "BASE_TABLE"
        and isinstance(node.get("table_name"), str)
        and not (not node.get("catalog_name") and not node.get("schema_name") and node["table_name"] in ctes)
    ]


def resolve(reference: Reference, own: set[Key], catalogs: dict[str, str]) -> str | None:
    """``reference`` as an entry of `depends_on`, or None when it names nothing known."""
    catalog, schema, table = reference
    if catalog:
        if catalog in catalogs:
            return f"{catalogs[catalog]}#{schema or DEFAULT_SCHEMA}.{table}"
        # Any other catalog is the one the writer attached this dataset under.
        return f"{schema}.{table}" if (schema, table) in own else None
    if schema:
        # `a.b` is a schema and a table, or else an attached catalog and a table in main.
        if (schema, table) in own:
            return f"{schema}.{table}"
        return f"{catalogs[schema]}#{DEFAULT_SCHEMA}.{table}" if schema in catalogs else None
    return f"{DEFAULT_SCHEMA}.{table}" if (DEFAULT_SCHEMA, table) in own else None


def depends_on(
    con: duckdb.DuckDBPyConnection, key: Key, sql: str, own: set[Key], catalogs: dict[str, str]
) -> list[str] | None:
    """What the table ``key`` reads, by its ``sql``. None when the SQL cannot be parsed."""
    found = references(con, sql)
    if found is None:
        print(f"fdl: {key[0]}.{key[1]}: could not read depends_on from its sql", file=sys.stderr)
        return None
    resolved = {resolve(reference, own, catalogs) for reference in found}
    return sorted(entry for entry in resolved if entry and entry != f"{key[0]}.{key[1]}")


def fill(tables: list[dict[str, Any]], catalogs: dict[str, str]) -> list[dict[str, Any]]:
    """``tables`` with `depends_on` read from the `sql` of those that do not have it."""
    own = {(t["schema"], t["name"]) for t in tables}
    con = duckdb.connect()
    try:

        def filled(table: dict[str, Any]) -> dict[str, Any]:
            if "depends_on" in table or not isinstance(table.get("sql"), str):
                return table
            found = depends_on(con, (table["schema"], table["name"]), table["sql"], own, catalogs)
            return table if found is None else {**table, "depends_on": found}

        return [filled(table) for table in tables]
    finally:
        con.close()
