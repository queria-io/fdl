"""`dataset.json`: what a version holds, in words a reader can search.

The writer describes the dataset and its tables; `fdl commit` checks that
description against the catalog and fills in what the catalog already knows
(column types, whether they may be null, row counts, comments). The result is
part of the version, beside the catalog.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import yaml

from . import layout

#: Where `fdl commit` looks when no file is named, in this order.
DEFAULT_FILES = (layout.DATASET, "dataset.yml", "dataset.yaml")

DATASET_KEYS = {
    "name", "title", "description", "keywords", "language", "homepage", "issued",
    "update_frequency", "licenses", "sources", "contributors", "temporal_coverage",
    "spatial_coverage", "schemas", "tables", "extensions",
}
TABLE_KEYS = {
    "schema", "name", "title", "description", "keywords", "sql", "depends_on",
    "primary_key", "licenses", "sources", "temporal_coverage", "spatial_coverage",
    "fields", "rows", "extensions",
}
FIELD_KEYS = {"name", "title", "description", "type", "nullable", "extensions"}


class DatasetError(ValueError):
    """A description the format does not take."""


def find(directory: Path = Path(".")) -> Path:
    """The description in ``directory``: dataset.json, else dataset.yml."""
    for name in DEFAULT_FILES:
        if (Path(directory) / name).is_file():
            return Path(directory) / name
    raise DatasetError(
        f"no {' or '.join(DEFAULT_FILES)} here: every version is described by one"
    )


def read(path: Path) -> dict[str, Any]:
    """A description written as JSON, or as YAML (``.yml`` / ``.yaml``)."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DatasetError(f"no {path}: every version is described by a {layout.DATASET}") from exc
    try:
        declared = (
            yaml.safe_load(text) if path.suffix in (".yml", ".yaml") else json.loads(text)
        )
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise DatasetError(f"{path} could not be read: {exc}") from exc
    if not isinstance(declared, dict):
        raise DatasetError(f"{path} has to be a JSON object")
    return declared


def describe(declared: dict[str, Any], frozen: Path) -> dict[str, Any]:
    """The `dataset.json` of a version: ``declared`` checked against the catalog.

    The catalog wins for what it records (types, nulls, rows); the writer's
    words win over comments in the catalog. A table described but not in the
    catalog is refused, and a table in the catalog but not described is listed
    with what the catalog says about it.
    """
    _check_keys(declared, DATASET_KEYS, "dataset.json")
    for key in ("name", "title"):
        if not isinstance(declared.get(key), str) or not declared[key].strip():
            raise DatasetError(f"dataset.json needs a {key}")
    found = _catalog_tables(frozen)
    described: dict[tuple[str, str], dict[str, Any]] = {}
    for table in declared.get("tables", []):
        _check_keys(table, TABLE_KEYS, "a table")
        key = (table.get("schema"), table.get("name"))
        if key not in found:
            raise DatasetError(f"{key[0]}.{key[1]} is described but not in the catalog")
        if key in described:
            raise DatasetError(f"{key[0]}.{key[1]} is described twice")
        described[key] = table
    tables = [_merge(described.get(key, {}), physical) for key, physical in found.items()]
    return {**declared, "tables": tables}


def to_json(description: dict[str, Any]) -> bytes:
    # YAML reads `issued: 2026-10-30` as a date; it is written back as the same text.
    return (json.dumps(description, ensure_ascii=False, indent=2, default=str) + "\n").encode()


def _check_keys(record: Any, allowed: set[str], what: str) -> None:
    if not isinstance(record, dict):
        raise DatasetError(f"{what} has to be a JSON object")
    unknown = sorted(set(record) - allowed)
    if unknown:
        raise DatasetError(
            f"{what} has keys the format does not define: {', '.join(unknown)} "
            f"(put them under extensions.<your name>)"
        )


def _merge(table: dict[str, Any], physical: dict[str, Any]) -> dict[str, Any]:
    written = {}
    for field in table.get("fields", []):
        _check_keys(field, FIELD_KEYS, "a field")
        if field.get("name") not in physical["columns"]:
            raise DatasetError(
                f"{physical['schema']}.{physical['name']} has no column {field.get('name')!r}"
            )
        written[field["name"]] = field
    fields = [
        {
            **_drop_none({"description": column["comment"]}),
            **written.get(name, {}),
            "name": name,
            "type": column["type"],
            "nullable": column["nullable"],
        }
        for name, column in physical["columns"].items()
    ]
    return {
        **_drop_none({"description": physical["comment"]}),
        **table,
        "schema": physical["schema"],
        "name": physical["name"],
        "rows": physical["rows"],
        "fields": fields,
    }


def _drop_none(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if value is not None}


def _catalog_tables(frozen: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """The live tables of a frozen catalog, read from its metadata tables."""
    con = duckdb.connect(str(frozen), read_only=True)
    try:
        tables = con.execute(
            """
            SELECT t.table_id, s.schema_name, t.table_name, st.record_count,
                   (SELECT value FROM ducklake_tag g WHERE g.object_id = t.table_id
                      AND g.key = 'comment' AND g.end_snapshot IS NULL)
            FROM ducklake_table t
            JOIN ducklake_schema s ON s.schema_id = t.schema_id AND s.end_snapshot IS NULL
            LEFT JOIN ducklake_table_stats st ON st.table_id = t.table_id
            WHERE t.end_snapshot IS NULL
            ORDER BY s.schema_name, t.table_name
            """
        ).fetchall()
        columns = con.execute(
            """
            SELECT c.table_id, c.column_name, c.column_type, c.nulls_allowed,
                   (SELECT value FROM ducklake_column_tag g WHERE g.table_id = c.table_id
                      AND g.column_id = c.column_id AND g.key = 'comment'
                      AND g.end_snapshot IS NULL)
            FROM ducklake_column c
            WHERE c.end_snapshot IS NULL AND c.parent_column IS NULL
            ORDER BY c.table_id, c.column_order
            """
        ).fetchall()
    finally:
        con.close()
    by_table: dict[int, dict[str, dict[str, Any]]] = {}
    for table_id, name, type_, nullable, comment in columns:
        by_table.setdefault(table_id, {})[name] = {
            "type": type_,
            "nullable": bool(nullable),
            "comment": comment,
        }
    return {
        (schema, name): {
            "schema": schema,
            "name": name,
            "rows": int(rows or 0),
            "comment": comment,
            "columns": by_table.get(table_id, {}),
        }
        for table_id, schema, name, rows, comment in tables
    }
