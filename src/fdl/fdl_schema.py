"""The `_fdl` schema: what FDL reads from the tables of the metadata (see the specification's
"The _fdl schema").

The writer's tool writes `_fdl` with SQL like any other schema, or with the
functions here (`ensure`, `upsert`, `delete`, `replace`), which hold the only
definition of its tables (`TYPES`, `KEYS`). FDL reads from it
the primary keys, the asset columns, which tables and views get no profile, and the
repositories views read; checks it against the tables and views before freezing;
and, for a version made from one written before `_fdl`, writes into it what that
version said by its `fdl.` tags or its `dataset.json`.

Everything here takes a DuckDB connection with the metadata attached (as ``lake``
unless said otherwise), at the snapshot to read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

SCHEMA = "_fdl"

#: Each table's primary key, as the specification fixes it. `dataset` has none.
KEYS: dict[str, tuple[str, ...]] = {
    "dataset": (),
    "schemas": ("schema",),
    "tables": ("schema", "name"),
    "columns": ("schema", "table", "name"),
    "files": ("path",),
    "catalogs": ("name",),
    "attributes": ("schema", "table", "column", "key"),
}

#: The columns FDL reads, and their types.
_READ_TYPES = {
    ("tables", "primary_key"): "VARCHAR[]",
    ("tables", "profile"): "BOOLEAN",
    ("columns", "asset"): "BOOLEAN",
    ("catalogs", "url"): "VARCHAR",
}

#: Asset columns of `_fdl` itself, which no row of `_fdl.columns` marks.
BUILT_IN_ASSETS = {f"{SCHEMA}.files": ["sha256"]}

_LICENSES = (
    "STRUCT(id VARCHAR, spdx VARCHAR, url VARCHAR, title VARCHAR)[]"
)
_SOURCES = "STRUCT(title VARCHAR, path VARCHAR)[]"
_COVERAGE = {
    "licenses": _LICENSES,
    "sources": _SOURCES,
    "temporal_coverage": 'STRUCT("start" VARCHAR, "end" VARCHAR)',
    "spatial_coverage": "VARCHAR[]",
}

#: Each table's columns and their types, as the specification gives them.
TYPES: dict[str, dict[str, str]] = {
    "dataset": {
        "title": "VARCHAR",
        "description": "VARCHAR",
        "language": "VARCHAR",
        "homepage": "VARCHAR",
        "issued": "DATE",
        "update_frequency": "VARCHAR",
        **_COVERAGE,
        "contributors": "STRUCT(title VARCHAR, path VARCHAR, roles VARCHAR[])[]",
    },
    "schemas": {"schema": "VARCHAR", "title": "VARCHAR", "description": "VARCHAR"},
    "tables": {
        "schema": "VARCHAR",
        "name": "VARCHAR",
        "title": "VARCHAR",
        "description": "VARCHAR",
        **_COVERAGE,
        "primary_key": "VARCHAR[]",
        "profile": "BOOLEAN",
        "sql": "VARCHAR",
        "depends_on": "VARCHAR[]",
    },
    "columns": {
        "schema": "VARCHAR",
        "table": "VARCHAR",
        "name": "VARCHAR",
        "title": "VARCHAR",
        "description": "VARCHAR",
        "asset": "BOOLEAN",
    },
    "files": {"path": "VARCHAR", "sha256": "VARCHAR", "bytes": "BIGINT", "media_type": "VARCHAR"},
    "catalogs": {"name": "VARCHAR", "url": "VARCHAR"},
    "attributes": {"schema": "VARCHAR", "table": "VARCHAR", "column": "VARCHAR", "key": "VARCHAR", "value": "JSON"},
}


def create_sql(table: str, columns: Iterable[str] | None = None, alias: str = "lake") -> str:
    """``CREATE TABLE`` for ``_fdl.<table>`` with ``columns`` (default: all of them) after its primary key."""
    names = list(TYPES[table]) if columns is None else list(dict.fromkeys([*KEYS[table], *columns]))
    body = ", ".join(f"{_q(name)} {TYPES[table][name]}" for name in names)
    return f"CREATE TABLE {alias}.{_q(SCHEMA)}.{_q(table)} ({body})"


class SchemaError(ValueError):
    """`_fdl` says something the specification refuses."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("_fdl: " + "; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Facts:
    """What FDL reads: tables and views keyed ``schema.name``."""

    primary_keys: dict[str, list[str]] = field(default_factory=dict)
    assets: dict[str, list[str]] = field(default_factory=dict)
    catalogs: dict[str, str] = field(default_factory=dict)
    unprofiled: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.primary_keys or self.assets or self.catalogs or self.unprofiled)

    def union(self, other: Facts) -> Facts:
        """These, with what ``other`` says where these say nothing."""
        assets = {**other.assets}
        for name, columns in self.assets.items():
            assets[name] = list(dict.fromkeys([*columns, *other.assets.get(name, [])]))
        return Facts(
            primary_keys={**other.primary_keys, **self.primary_keys},
            assets=assets,
            catalogs={**other.catalogs, **self.catalogs},
            unprofiled=self.unprofiled | other.unprofiled,
        )

    def only(self, tables: set[str]) -> Facts:
        """What these say of ``tables`` (and the catalogs)."""
        return Facts(
            primary_keys={k: v for k, v in self.primary_keys.items() if k in tables},
            assets={k: v for k, v in self.assets.items() if k in tables},
            catalogs=dict(self.catalogs),
            unprofiled=frozenset(self.unprofiled & tables),
        )


# -- what versions written before _fdl said ------------------------------------------


def from_tags(tags: dict[str, dict[str, Any]]) -> Facts:
    """What `fdl.` tags say (`tags.read`), as `_fdl` would."""
    catalogs: dict[str, str] = {}
    for values in tags.values():
        found = values.get("fdl.catalogs")
        if isinstance(found, dict):
            catalogs.update({k: v for k, v in found.items() if isinstance(v, str)})
    return Facts(
        primary_keys={n: list(v["fdl.primary_key"]) for n, v in tags.items() if _names(v.get("fdl.primary_key"))},
        assets={n: list(v["fdl.assets"]) for n, v in tags.items() if _names(v.get("fdl.assets"))},
        catalogs=catalogs,
        unprofiled=frozenset(n for n, v in tags.items() if v.get("fdl.profile") is False),
    )


def from_dataset(described: dict | None) -> Facts:
    """What an earlier version's `dataset.json` says, as `_fdl` would."""
    from . import tags

    return from_tags(tags.from_dataset(described))


def from_index(index: dict | None) -> Facts:
    """The primary keys, assets and catalogs a `files.json` repeats (no profiles)."""
    tables = (index or {}).get("tables", {})
    catalogs = (index or {}).get("catalogs")
    return Facts(
        primary_keys={n: list(t["primary_key"]) for n, t in tables.items() if _names(t.get("primary_key"))},
        assets={n: list(t["assets"]) for n, t in tables.items() if _names(t.get("assets"))},
        catalogs=dict(catalogs) if isinstance(catalogs, dict) else {},
    )


def _names(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, str) for v in value)


# -- reading ----------------------------------------------------------------------------


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


@dataclass
class Objects:
    """The tables and views of a metadata: ``(schema, name)`` to each column's type."""

    tables: dict[tuple[str, str], dict[str, str]]
    views: set[tuple[str, str]]


def objects(con, alias: str = "lake") -> Objects:
    rows = con.execute(
        """
        SELECT t.schema_name, t.table_name, c.column_name, c.data_type
        FROM duckdb_tables() t
        LEFT JOIN duckdb_columns() c
          ON c.database_name = t.database_name AND c.schema_name = t.schema_name AND c.table_name = t.table_name
        WHERE t.database_name = ?
        ORDER BY t.schema_name, t.table_name, c.column_index
        """,
        [alias],
    ).fetchall()
    tables: dict[tuple[str, str], dict[str, str]] = {}
    for schema, table, column, kind in rows:
        columns = tables.setdefault((schema, table), {})
        if column is not None:
            columns[column] = kind
    views = {
        (schema, name)
        for schema, name in con.execute(
            "SELECT schema_name, view_name FROM duckdb_views() WHERE database_name = ? AND NOT internal", [alias]
        ).fetchall()
    }
    return Objects(tables=tables, views=views)


def _has_schema(con, alias: str) -> bool:
    return bool(
        con.execute(
            "SELECT count(*) FROM duckdb_schemas() WHERE database_name = ? AND schema_name = ?", [alias, SCHEMA]
        ).fetchone()[0]
    )


def _select(con, alias: str, found: Objects, table: str, columns: tuple[str, ...]) -> list[dict[str, Any]]:
    """The rows of ``_fdl.<table>``, with the ``columns`` it has (the others None)."""
    held = found.tables.get((SCHEMA, table))
    if held is None:
        return []
    present = [c for c in columns if c in held]
    if not present:
        return []
    rows = con.execute(
        f"SELECT {', '.join(_q(c) for c in present)} FROM {alias}.{_q(SCHEMA)}.{_q(table)}"
    ).fetchall()
    return [{c: dict(zip(present, row)).get(c) for c in columns} for row in rows]


def read(con, alias: str = "lake") -> Facts:
    """What `_fdl` says at the attached snapshot, `_fdl.files` among the asset columns."""
    if not _has_schema(con, alias):
        return Facts()
    found = objects(con, alias)
    keys: dict[str, list[str]] = {}
    unprofiled: set[str] = set()
    for row in _select(con, alias, found, "tables", ("schema", "name", "primary_key", "profile")):
        name = f"{row['schema']}.{row['name']}"
        if row["primary_key"]:
            keys[name] = list(row["primary_key"])
        if row["profile"] is False:
            unprofiled.add(name)
    assets: dict[str, list[str]] = {}
    for row in _select(con, alias, found, "columns", ("schema", "table", "name", "asset")):
        if row["asset"] is True and (row["schema"], row["table"]) in found.tables:
            assets.setdefault(f"{row['schema']}.{row['table']}", []).append(row["name"])
    if (SCHEMA, "files") in found.tables:
        assets.update(BUILT_IN_ASSETS)
    catalogs = {
        row["name"]: row["url"] for row in _select(con, alias, found, "catalogs", ("name", "url")) if row["name"]
    }
    return Facts(
        primary_keys=keys,
        assets={name: sorted(set(columns), key=columns.index) for name, columns in assets.items()},
        catalogs=catalogs,
        unprofiled=frozenset(unprofiled),
    )


def rows(con, alias: str = "lake") -> dict[str, list[str]]:
    """Every row of `_fdl`, by table, each as JSON of its non-null values, sorted: for comparing."""
    if not _has_schema(con, alias):
        return {}
    found = objects(con, alias)
    held: dict[str, list[str]] = {}
    for schema, table in found.tables:
        if schema != SCHEMA:
            continue
        cursor = con.execute(f"SELECT * FROM {alias}.{_q(SCHEMA)}.{_q(table)}")
        names = [d[0] for d in cursor.description]
        held[table] = sorted(
            json.dumps({k: v for k, v in zip(names, row) if v is not None}, sort_keys=True, default=str)
            for row in cursor.fetchall()
        )
    return held


def expected_rows(facts: Facts) -> dict[str, list[str]]:
    """The rows `fill` writes for ``facts`` into an empty `_fdl`, as `rows` gives them."""
    tables: dict[str, dict[str, Any]] = {}
    for name, key in facts.primary_keys.items():
        tables.setdefault(name, {})["primary_key"] = list(key)
    for name in facts.unprofiled:
        tables.setdefault(name, {})["profile"] = False
    held: dict[str, list[str]] = {}

    def add(table: str, record: dict) -> None:
        held.setdefault(table, []).append(json.dumps(record, sort_keys=True, default=str))

    for name, values in tables.items():
        schema, table = name.split(".", 1)
        add("tables", {"schema": schema, "name": table, **values})
    for name, columns in facts.assets.items():
        if name in BUILT_IN_ASSETS:
            continue
        schema, table = name.split(".", 1)
        for column in columns:
            add("columns", {"schema": schema, "table": table, "name": column, "asset": True})
    for name, url in facts.catalogs.items():
        add("catalogs", {"name": name, "url": url})
    return {table: sorted(found) for table, found in held.items()}


# -- checking ---------------------------------------------------------------------------


def check(con, alias: str = "lake", *, view_columns: dict[tuple[str, str], set[str]] | None = None) -> list[str]:
    """What the specification refuses in `_fdl` (its Checks), as messages. Empty when none.

    ``view_columns`` are the columns of the views that could be bound: rows naming a
    column of a view that could not be bound are not checked.
    """
    if not _has_schema(con, alias):
        return []
    found = objects(con, alias)
    view_columns = view_columns or {}
    problems: list[str] = []
    for table, key in KEYS.items():
        held = found.tables.get((SCHEMA, table))
        if held is None:
            continue
        lacking = [c for c in key if c not in held]
        if lacking:
            problems.append(f"{SCHEMA}.{table} lacks {', '.join(lacking)} of its primary key")
    for (table, column), kind in _READ_TYPES.items():
        held = found.tables.get((SCHEMA, table), {})
        if column in held and held[column].upper() != kind:
            problems.append(f"{SCHEMA}.{table}.{column} is {held[column]}, not {kind}")
    if problems:
        return problems
    schemas = {schema for schema, _ in found.tables} | {schema for schema, _ in found.views}

    def has(schema: str, name: str) -> bool:
        return (schema, name) in found.tables or (schema, name) in found.views

    def has_column(schema: str, name: str, column: str) -> bool:
        if (schema, name) in found.tables:
            return column in found.tables[(schema, name)]
        return (schema, name) not in view_columns or column in view_columns[(schema, name)]

    if (SCHEMA, "dataset") in found.tables:
        count = con.execute(f"SELECT count(*) FROM {alias}.{_q(SCHEMA)}.dataset").fetchone()[0]
        if count > 1:
            problems.append(f"{SCHEMA}.dataset has {count} rows, not one at most")
    for row in _select(con, alias, found, "schemas", ("schema",)):
        if row["schema"] not in schemas:
            problems.append(f"{SCHEMA}.schemas names {row['schema']}, which is not there")
    for row in _select(con, alias, found, "tables", ("schema", "name", "primary_key")):
        where = f"{row['schema']}.{row['name']}"
        if not has(row["schema"], row["name"]):
            problems.append(f"{SCHEMA}.tables names {where}, which is not there")
            continue
        key = row["primary_key"]
        if not key:
            continue
        if (row["schema"], row["name"]) in found.views:
            problems.append(f"{SCHEMA}.tables gives the view {where} a primary_key")
            continue
        if len(set(key)) != len(key):
            problems.append(f"{where}: primary_key names a column twice")
        missing = [c for c in key if c not in found.tables[(row["schema"], row["name"])]]
        if missing:
            problems.append(f"{where}: primary_key names columns it lacks: {', '.join(map(str, missing))}")
    for row in _select(con, alias, found, "columns", ("schema", "table", "name", "asset")):
        where = f"{row['schema']}.{row['table']}.{row['name']}"
        if not has(row["schema"], row["table"]) or not has_column(row["schema"], row["table"], row["name"]):
            problems.append(f"{SCHEMA}.columns names {where}, which is not there")
            continue
        if row["asset"] is not True:
            continue
        if (row["schema"], row["table"]) in found.views:
            problems.append(f"{SCHEMA}.columns marks {where} of a view as an asset")
        elif found.tables[(row["schema"], row["table"])][row["name"]].upper() != "VARCHAR":
            problems.append(f"{where} is an asset column, so its type has to be VARCHAR (a sha256 in hex)")
    for row in _select(con, alias, found, "attributes", ("schema", "table", "column")):
        schema, table, column = (row[k] or "" for k in ("schema", "table", "column"))
        if schema and schema not in schemas:
            problems.append(f"{SCHEMA}.attributes names the schema {schema}, which is not there")
        elif table and not has(schema, table):
            problems.append(f"{SCHEMA}.attributes names {schema}.{table}, which is not there")
        elif column and table and not has_column(schema, table, column):
            problems.append(f"{SCHEMA}.attributes names {schema}.{table}.{column}, which is not there")
    return problems


# -- writing what an earlier version said ----------------------------------------------


def fill(con, facts: Facts, alias: str = "lake", *, begin: bool = True) -> bool:
    """Write into `_fdl` what ``facts`` say and `_fdl` does not, in one transaction
    (``begin=False``: in the transaction the caller began).

    For the tables and views alive now; a value `_fdl` already gives (a primary
    key, an asset flag, a profile flag, a catalog) is left as it is. Returns
    whether anything was written.
    """
    found = objects(con, alias)
    alive = {f"{s}.{n}" for s, n in found.tables} | {f"{s}.{n}" for s, n in found.views}
    facts = facts.only(alive)
    if not facts:
        return False
    have = _has_schema(con, alias)
    said = read(con, alias) if have else Facts()
    held_tables = {(r["schema"], r["name"]): r for r in _select(con, alias, found, "tables", ("schema", "name", "primary_key", "profile"))}
    held_columns = {
        (r["schema"], r["table"], r["name"]): r for r in _select(con, alias, found, "columns", ("schema", "table", "name", "asset"))
    }
    statements: list[str] = []
    target = lambda table: f"{alias}.{_q(SCHEMA)}.{_q(table)}"  # noqa: E731

    def ensure(table: str, columns: dict[str, str]) -> None:
        held = found.tables.get((SCHEMA, table))
        if held is None:
            statements.append(create_sql(table, alias=alias))
            found.tables[(SCHEMA, table)] = dict.fromkeys(columns, "")
            return
        for column, kind in columns.items():
            if column not in held:
                statements.append(f"ALTER TABLE {target(table)} ADD COLUMN {_q(column)} {kind}")
                held[column] = kind

    def values(*items: Any) -> str:
        return ", ".join(_literal(item) for item in items)

    for name in sorted(set(facts.primary_keys) | set(facts.unprofiled)):
        schema, table = name.split(".", 1)
        key = facts.primary_keys.get(name) if name not in said.primary_keys and (schema, table) not in found.views else None
        unprofiled = name in facts.unprofiled and (held_tables.get((schema, table)) or {}).get("profile") is None
        if not key and not unprofiled:
            continue
        ensure("tables", {"primary_key": "VARCHAR[]", "profile": "BOOLEAN"})
        sets = [*([f"primary_key = {_literal(key)}"] if key else []), *(["profile = false"] if unprofiled else [])]
        if (schema, table) in held_tables:
            statements.append(
                f"UPDATE {target('tables')} SET {', '.join(sets)} WHERE \"schema\" = {_lit(schema)} AND \"name\" = {_lit(table)}"
            )
        else:
            statements.append(
                f"INSERT INTO {target('tables')} (\"schema\", \"name\", primary_key, profile) "
                f"VALUES ({values(schema, table, key, False if unprofiled else None)})"
            )
    for name, columns in sorted(facts.assets.items()):
        schema, table = name.split(".", 1)
        if (schema, table) not in found.tables or name in BUILT_IN_ASSETS:
            continue
        for column in columns:
            row = held_columns.get((schema, table, column))
            if row is not None and row["asset"] is not None:
                continue
            ensure("columns", {"asset": "BOOLEAN"})
            if row is not None:
                statements.append(
                    f"UPDATE {target('columns')} SET asset = true WHERE \"schema\" = {_lit(schema)} "
                    f"AND \"table\" = {_lit(table)} AND \"name\" = {_lit(column)}"
                )
            else:
                statements.append(
                    f"INSERT INTO {target('columns')} (\"schema\", \"table\", \"name\", asset) "
                    f"VALUES ({values(schema, table, column, True)})"
                )
    for name, url in sorted(facts.catalogs.items()):
        if name in said.catalogs:
            continue
        ensure("catalogs", {"url": "VARCHAR"})
        statements.append(f"INSERT INTO {target('catalogs')} (\"name\", url) VALUES ({values(name, url)})")
    if not statements:
        return False
    if begin:
        con.execute("BEGIN")
    try:
        if not have:
            con.execute(f"CREATE SCHEMA {alias}.{_q(SCHEMA)}")
        for statement in statements:
            con.execute(statement)
        if begin:
            con.execute("COMMIT")
    except BaseException:
        if begin:
            con.execute("ROLLBACK")
        raise
    return True


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, list):
        return "[" + ", ".join(_literal(v) for v in value) + "]::VARCHAR[]"
    return _lit(str(value))


# -- writing rows ----------------------------------------------------------------------
#
# The tables of `_fdl` are defined here once: a tool that writes `_fdl` (fdl meta,
# or a tool that builds it from the writer's files) calls these instead of keeping
# its own CREATE TABLE or list of columns.


def has_schema(con, alias: str = "lake") -> bool:
    """Whether the attached metadata has the schema `_fdl`."""
    return _has_schema(con, alias)


def ensure(con, table: str, columns: Iterable[str] = (), alias: str = "lake") -> dict[str, str]:
    """Make the schema `_fdl`, ``_fdl.<table>`` with its primary key, and ``columns``
    where they are missing, with the types the specification gives. Returns each
    column the table has and its type.

    Raises `SchemaError` for a table or column the specification does not define.
    """
    if table not in TYPES:
        raise SchemaError([f"{SCHEMA}.{table} is not a table of {SCHEMA}"])
    unknown = [c for c in columns if c not in TYPES[table]]
    if unknown:
        raise SchemaError([f"{SCHEMA}.{table} has no column {', '.join(unknown)}"])
    if not _has_schema(con, alias):
        con.execute(f"CREATE SCHEMA {alias}.{_q(SCHEMA)}")
    held = objects(con, alias).tables.get((SCHEMA, table))
    if held is None:
        con.execute(create_sql(table, columns, alias=alias))
        return {c: TYPES[table][c] for c in dict.fromkeys([*KEYS[table], *columns])}
    for column in columns:
        if column not in held:
            con.execute(f"ALTER TABLE {alias}.{_q(SCHEMA)}.{_q(table)} ADD COLUMN {_q(column)} {TYPES[table][column]}")
            held[column] = TYPES[table][column]
    return held


def _expression(kind: str, value: Any) -> tuple[str, Any]:
    """The SQL expression and parameter that give ``value`` as a ``kind``."""
    upper = kind.upper()
    if upper == "JSON":
        return "CAST(? AS JSON)", json.dumps(value, ensure_ascii=False, default=str)
    if value is None:
        return "NULL", None
    if isinstance(value, str) and "STRUCT" not in upper and not upper.endswith("[]"):
        return f"CAST(? AS {kind})", value
    return (
        f"CAST(from_json(?, {_lit(json.dumps(structure(kind)))}) AS {kind})",
        json.dumps(value, ensure_ascii=False, default=str),
    )


def _unwritable(con, table: str, row: dict[str, Any], types: dict[str, str]) -> list[str]:
    """The values of ``row`` that cannot be written as their column's type, as messages."""
    import duckdb

    where = _where_of(table, row)
    found = []
    for column, value in row.items():
        if column not in types or value is None:
            continue
        expression, parameter = _expression(types[column], value)
        try:
            con.execute(f"SELECT {expression}", [parameter]).fetchone()
        except duckdb.Error:
            found.append(f"{table}: {column} cannot be written as {types[column]} (at {where}.{column})")
    return found


def upsert(con, table: str, row: dict[str, Any], alias: str = "lake") -> None:
    """Write ``row`` into ``_fdl.<table>``, making what it needs (`ensure`).

    ``row`` holds the table's primary key and the columns to set; a value of None
    clears the column. The row with that key is changed, or added when there is
    none; `_fdl.dataset` has no key and one row at most. A row left with nothing
    but its key is removed. Values are Python values (a list for ``VARCHAR[]``, a
    dict for a STRUCT, any JSON value for `_fdl.attributes`); a string is cast to
    a scalar type (``'true'``, ``'2026-01-01'``). Raises `SchemaError` for a row
    without its key, or a table or column the specification does not define.
    """
    import duckdb

    if table not in TYPES:
        raise SchemaError([f"{SCHEMA}.{table} is not a table of {SCHEMA}"])
    lacking = [k for k in KEYS[table] if row.get(k) is None]
    if lacking:
        raise SchemaError([f"a row of {SCHEMA}.{table} needs {', '.join(lacking)}"])
    key = {k: row[k] for k in KEYS[table]}
    values = {k: v for k, v in row.items() if k not in key}
    held = ensure(con, table, values, alias)
    problems = _unwritable(con, table, row, held)
    if problems:
        raise SchemaError(problems)
    target = f"{alias}.{_q(SCHEMA)}.{_q(table)}"
    where = " AND ".join(f"{_q(k)} = ?" for k in key)
    where = f" WHERE {where}" if where else ""
    parts = {column: _expression(held[column], value) for column, value in values.items()}
    try:
        exists = con.execute(f"SELECT count(*) FROM {target}{where}", list(key.values())).fetchone()[0]
        if exists and parts:
            sets = ", ".join(f"{_q(c)} = {e}" for c, (e, _) in parts.items())
            params = [p for e, p in parts.values() if e != "NULL"]
            con.execute(f"UPDATE {target} SET {sets}{where}", [*params, *key.values()])
        elif not exists and any(v is not None for v in values.values()):
            given = {c: ep for c, ep in parts.items() if ep[0] != "NULL"}
            names = [*key, *given]
            con.execute(
                f"INSERT INTO {target} ({', '.join(map(_q, names))}) "
                f"VALUES ({', '.join(['?'] * len(key) + [e for e, _ in given.values()])})",
                [*key.values(), *(p for _, p in given.values())],
            )
    except duckdb.ConversionException as exc:
        raise SchemaError([f"{SCHEMA}.{table}: {exc}"]) from exc
    rest = [c for c in held if c not in KEYS[table]]
    if rest:
        empty = " AND ".join(f"{_q(c)} IS NULL" for c in rest)
        con.execute(f"DELETE FROM {target}{where}{' AND ' if where else ' WHERE '}{empty}", list(key.values()))


def delete(con, table: str, key: dict[str, Any], alias: str = "lake") -> int:
    """Remove the row of ``_fdl.<table>`` with ``key`` (every row of `_fdl.dataset`). Returns how many."""
    if not _has_schema(con, alias) or (SCHEMA, table) not in objects(con, alias).tables:
        return 0
    target = f"{alias}.{_q(SCHEMA)}.{_q(table)}"
    where = " AND ".join(f"{_q(k)} = ?" for k in KEYS[table])
    where = f" WHERE {where}" if where else ""
    params = [key[k] for k in KEYS[table]]
    count = con.execute(f"SELECT count(*) FROM {target}{where}", params).fetchone()[0]
    con.execute(f"DELETE FROM {target}{where}", params)
    return count


def structure(kind: str) -> Any:
    """A DuckDB type as the structure `from_json` takes: a STRUCT as an object, a list as a one-item list."""
    text = kind.strip()
    if text.endswith("[]"):
        return [structure(text[:-2])]
    if not text.upper().startswith("STRUCT(") or not text.endswith(")"):
        return text
    fields: dict[str, Any] = {}
    for part in _split(text[len("STRUCT(") : -1]):
        part = part.strip()
        if part.startswith('"'):
            end = part.index('"', 1)
            while part[end + 1 : end + 2] == '"':
                end = part.index('"', end + 2)
            name, rest = part[1:end].replace('""', '"'), part[end + 1 :]
        else:
            name, _, rest = part.partition(" ")
        fields[name] = structure(rest)
    return fields


def _split(text: str) -> list[str]:
    """``text`` split at the commas outside parentheses and quotes."""
    parts, depth, quoted, start = [], 0, False, 0
    for at, char in enumerate(text):
        if char == '"':
            quoted = not quoted
        elif not quoted and char == "(":
            depth += 1
        elif not quoted and char == ")":
            depth -= 1
        elif not quoted and char == "," and depth == 0:
            parts.append(text[start:at])
            start = at + 1
    return [*parts, text[start:]]




def _where_of(table: str, row: dict[str, Any]) -> str:
    """Where a row of ``_fdl.<table>`` points, for messages: ``schema.table.column``, a path or a name."""
    if table == "dataset":
        return "the dataset"
    if table == "attributes":
        return ".".join(str(row.get(k)) for k in ("schema", "table", "column") if row.get(k)) or "the dataset"
    return ".".join(str(row.get(k)) for k in KEYS[table])


def replace(con, rows: dict[str, list[dict[str, Any]]], alias: str = "lake") -> None:
    """Make `_fdl` hold ``rows`` and nothing else, in the transaction the caller began.

    Every table of `_fdl` is dropped; each table ``rows`` names is made with the
    columns the specification gives, and its rows written. Values are as `upsert`
    takes them. Raises `SchemaError` for a table or column the specification does
    not define, a value that cannot be written as its column's type, or `_fdl`
    failing its checks.
    """
    problems: list[str] = []
    for table, items in rows.items():
        if table not in TYPES:
            problems.append(f"{SCHEMA}.{table} is not a table of {SCHEMA}")
            continue
        for row in items:
            where = _where_of(table, row)
            problems += [f"{table}: {where} lacks {k}" for k in KEYS[table] if row.get(k) is None]
            problems += [f"{table}: {where} has {c}, which is not a column of {SCHEMA}.{table}" for c in row if c not in TYPES[table]]
            problems += _unwritable(con, table, row, TYPES[table])
    if problems:
        raise SchemaError(problems)
    if _has_schema(con, alias):
        for (schema, table) in objects(con, alias).tables:
            if schema == SCHEMA:
                con.execute(f"DROP TABLE {alias}.{_q(SCHEMA)}.{_q(table)}")
    if not rows:
        if _has_schema(con, alias):
            con.execute(f"DROP SCHEMA {alias}.{_q(SCHEMA)}")
        return
    if not _has_schema(con, alias):
        con.execute(f"CREATE SCHEMA {alias}.{_q(SCHEMA)}")
    for table, items in rows.items():
        con.execute(create_sql(table, alias=alias))
        target = f"{alias}.{_q(SCHEMA)}.{_q(table)}"
        for row in items:
            given = {c: _expression(TYPES[table][c], v) for c, v in row.items() if v is not None}
            con.execute(
                f"INSERT INTO {target} ({', '.join(map(_q, given))}) VALUES ({', '.join(e for e, _ in given.values())})",
                [p for _, p in given.values()],
            )
    found = check(con, alias)
    if found:
        raise SchemaError(found)


def _plain(value: Any) -> Any:
    """A value as JSON holds it: a date as its ISO text."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def load(con, alias: str = "lake") -> dict[str, list[dict[str, Any]]]:
    """Every row of `_fdl`, by table, as JSON values; NULL columns left out, the
    value of `_fdl.attributes` parsed. ``{}`` without `_fdl`."""
    if not _has_schema(con, alias):
        return {}
    held: dict[str, list[dict[str, Any]]] = {}
    for schema, table in sorted(objects(con, alias).tables):
        if schema != SCHEMA:
            continue
        cursor = con.execute(f"SELECT * FROM {alias}.{_q(SCHEMA)}.{_q(table)}")
        names = [d[0] for d in cursor.description]
        held[table] = [
            {
                k: (json.loads(v) if table == "attributes" and k == "value" else _plain(v))
                for k, v in zip(names, values)
                if v is not None
            }
            for values in cursor.fetchall()
        ]
    return held


# -- what a commit reads from the working metadata --------------------------------------


@dataclass
class Described:
    """What a commit reads from `_fdl` and the views, at the working metadata's last snapshot."""

    facts: Facts
    #: Each view, keyed ``schema.view``: its ``sql`` and, when it could be bound, its ``columns``.
    views: dict[str, dict[str, Any]]
    #: The rows of `_fdl`, as `rows` gives them.
    rows: dict[str, list[str]]


def describe(
    working,
    *,
    data_path: str,
    setup=(),
    earlier: Facts | None = None,
) -> Described:
    """Read, and check, `_fdl` and the views of a working metadata.

    ``earlier`` is what a version written before `_fdl` said: it is written into
    `_fdl` first where `_fdl` does not say it (see `fill`), in a new snapshot of
    the working metadata. Raises `SchemaError` for what the specification refuses.
    """
    import duckdb

    from . import catalog

    con = duckdb.connect()
    try:
        for statement in [
            *setup,
            *catalog.attach_sql(working, alias="lake", data_path=data_path, read_only=not earlier),
        ]:
            if statement:
                con.execute(statement)
        if earlier:
            fill(con, earlier)
        facts = read(con)
        views = _views(con)
        for name, url in facts.catalogs.items():
            _attach_repository(con, name, url)
        con.execute("USE lake")
        bound: dict[tuple[str, str], set[str]] = {}
        for key, view in views.items():
            schema, name = key.split(".", 1)
            try:
                described = con.execute(f"DESCRIBE SELECT * FROM lake.{_q(schema)}.{_q(name)}").fetchall()
            except duckdb.Error:
                continue
            view["columns"] = [{"name": row[0], "type": row[1]} for row in described]
            bound[(schema, name)] = {row[0] for row in described}
        problems = check(con, view_columns=bound)
        if problems:
            raise SchemaError(problems)
        return Described(facts=facts, views=views, rows=rows(con))
    finally:
        con.close()


def _views(con) -> dict[str, dict[str, Any]]:
    """Each live view and its SQL, as the metadata records it."""
    meta = "__ducklake_metadata_lake.main"
    found = con.execute(
        f"""
        SELECT s.schema_name, v.view_name, v.sql
        FROM {meta}.ducklake_view v JOIN {meta}.ducklake_schema s USING (schema_id)
        WHERE v.end_snapshot IS NULL AND s.end_snapshot IS NULL
        ORDER BY 1, 2
        """
    ).fetchall()
    return {f"{schema}.{name}": {"sql": sql} for schema, name, sql in found}


def _attach_repository(con, name: str, url: str) -> bool:
    """Attach the version another repository's HEAD holds under ``name``. False when it cannot be read."""
    from . import layout, repo
    from .stores import open_store

    try:
        store = open_store(url)
        version = repo.readable(store)
        where = store.url.rstrip("/") + "/" + layout.version_metadata(version)
        con.execute(
            f"ATTACH 'ducklake:{where.replace(chr(39), chr(39) * 2)}' AS {_q(name)} "
            f"(DATA_PATH {_lit(repo.data_url(store))}, OVERRIDE_DATA_PATH true, READ_ONLY)"
        )
        return True
    except Exception:  # noqa: BLE001 - a view that cannot be bound gets no columns
        return False
