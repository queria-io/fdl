"""The rows of `_fdl` in a working metadata, read and written by name and key, as
`git config` reads and writes a config file.

A name is ``.`` for the dataset, ``schema``, ``schema.table`` (a table or a view)
or ``schema.table.column``. A key is a column of the `_fdl` table for that name
(``title``, ``primary_key``, ``asset``, ...), or, with a ``-`` in it, the key of
a row of `_fdl.attributes` (``example-theme``). Every function takes a DuckDB
connection with the working metadata attached as ``lake``; the caller begins
and ends the transaction. The rows are written with `fdl_schema.upsert`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .fdl_schema import KEYS, SCHEMA, TYPES, SchemaError, _q, check, delete, objects, upsert

#: The tables a name stands for, by how many parts it has.
_BY_PARTS = {1: "schemas", 2: "tables", 3: "columns"}


class MetaError(ValueError):
    """A name, key or value `fdl meta` cannot take."""


@dataclass(frozen=True)
class Target:
    """What a name stands for: a row of ``_fdl.<table>`` and the names attributes give it."""

    name: str
    table: str
    key: dict[str, str]
    attributes: tuple[str, str, str]


def target(name: str) -> Target:
    if name == ".":
        return Target(name, "dataset", {}, ("", "", ""))
    parts = name.split(".", 2)
    if not all(parts):
        raise MetaError(f"{name!r} is not a name: use ., schema, schema.table or schema.table.column")
    table = _BY_PARTS[len(parts)]
    padded = (*parts, "", "")[:3]
    return Target(name, table, dict(zip(KEYS[table], parts)), padded)


@dataclass(frozen=True)
class Field:
    """Where a key's value is kept: a column of the rows of ``_fdl.<table>`` that ``where`` picks."""

    table: str
    where: dict[str, str]
    column: str


def field(at: Target, key: str) -> Field:
    columns = [c for c in TYPES[at.table] if c not in KEYS[at.table]]
    if key in columns:
        return Field(at.table, at.key, key)
    if "-" in key.strip("-"):
        schema, table, column = at.attributes
        return Field("attributes", {"schema": schema, "table": table, "column": column, "key": key}, "value")
    raise MetaError(
        f"{key!r} is not a key of {at.name}: {', '.join(columns)}, or <reader>-<name> for an attribute"
    )


def _name(table: str, row: dict[str, Any]) -> str:
    if table == "attributes":
        parts = [row["schema"], row["table"], row["column"]]
        return ".".join(p for p in parts if p) or "."
    return ".".join(row[k] for k in KEYS[table]) or "."


def items(con) -> list[tuple[str, str, Any]]:
    """Every value `_fdl` holds for a name and a key, as ``(name, key, value)``, by name.

    The tables `fdl meta` does not write (`_fdl.files`, `_fdl.catalogs`) are left out.
    """
    found = objects(con)
    held: list[tuple[str, str, Any]] = []
    for table in ("dataset", "schemas", "tables", "columns", "attributes"):
        if (SCHEMA, table) not in found.tables:
            continue
        cursor = con.execute(f"SELECT * FROM lake.{_q(SCHEMA)}.{_q(table)}")
        names = [d[0] for d in cursor.description]
        for values in cursor.fetchall():
            row = dict(zip(names, values))
            if any(row.get(k) is None for k in KEYS[table]):
                continue
            name = _name(table, row)
            if table == "attributes":
                if row.get("value") is not None:
                    held.append((name, row["key"], json.loads(row["value"])))
                continue
            held.extend(
                (name, column, value)
                for column, value in row.items()
                if column not in KEYS[table] and value is not None
            )
    return sorted(held, key=lambda item: (item[0] != ".", item[0], item[1]))


def get(con, at: Target, key: str) -> Any:
    """The value of ``key`` for ``at``. Raises `KeyError` when none is set."""
    field(at, key)
    for name, held, value in items(con):
        if name == at.name and held == key:
            return value
    raise KeyError(key)


def _parse(kind: str, values: list[str]) -> Any:
    """The value ``values`` give a column of type ``kind``, as `fdl_schema.upsert` takes it."""
    upper = kind.upper()
    if upper.endswith("[]") and "STRUCT" not in upper:
        if not values:
            raise MetaError("give one value or more")
        return list(values)
    if len(values) != 1:
        raise MetaError(f"give one value ({kind})")
    value = values[0]
    if upper == "JSON" or "STRUCT" in upper:
        try:
            return json.loads(value)
        except ValueError:
            if upper != "JSON":
                raise MetaError(f"give the value as JSON ({kind})") from None
    return value


def set_value(con, at: Target, key: str, values: list[str]) -> None:
    """Set ``key`` of ``at`` to ``values`` (one, or several for a list), making the
    schema, table, column or row it needs. Raises `SchemaError` when that makes
    `_fdl` fail a check it passed (a name that is not there, a primary key on a view)."""
    to = field(at, key)
    before = set(check(con))
    held = objects(con).tables.get((SCHEMA, to.table)) or {}
    kind = held.get(to.column) or TYPES[to.table][to.column]
    upsert(con, to.table, {**to.where, to.column: _parse(kind, values)})
    problems = [p for p in check(con) if p not in before]
    if problems:
        raise SchemaError(problems)


def unset(con, at: Target, key: str) -> bool:
    """Clear ``key`` of ``at``, and drop a row left with nothing but its names.
    Returns whether it was set."""
    try:
        get(con, at, key)
    except KeyError:
        return False
    to = field(at, key)
    if to.table == "attributes":
        delete(con, "attributes", to.where)
    else:
        upsert(con, to.table, {**to.where, to.column: None})
    return True


def show(value: Any) -> str:
    """A value as `fdl meta` prints it: text as it is when on one line, anything else as JSON."""
    if isinstance(value, str) and "\n" not in value:
        return value
    return json.dumps(value, ensure_ascii=False, default=str)
