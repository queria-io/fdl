"""`dataset.json`: what a version holds, in words a reader can search.

The writer describes the dataset and its tables; `fdl commit` checks that
description against the catalog and fills in what the catalog already knows
(column types, whether they may be null, row counts, comments). The result is
part of the version, beside the catalog.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import yaml

from . import layout, lineage

#: Where `fdl commit` looks when no file is named, in this order.
DEFAULT_FILES = (layout.DATASET, "dataset.yml", "dataset.yaml")

DATASET_KEYS = {
    "name", "title", "description", "keywords", "language", "homepage", "issued",
    "update_frequency", "licenses", "sources", "contributors", "temporal_coverage",
    "spatial_coverage", "schemas", "tables", "catalogs", "annotations",
}
TABLE_KEYS = {
    "schema", "name", "title", "description", "keywords", "sql", "depends_on",
    "primary_key", "licenses", "sources", "temporal_coverage", "spatial_coverage",
    "fields", "rows", "kind", "annotations",
}
FIELD_KEYS = {"name", "title", "description", "type", "nullable", "asset", "annotations"}
#: What `annotations` was called before fdl 0.17. Still read, as `annotations`.
OLD_ANNOTATIONS = "extensions"


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


#: Tables may each be described in their own file, next to what builds them.
TABLE_FILES = ("*.table.json", "*.table.yml", "*.table.yaml")


def gather(path: Path) -> dict[str, Any]:
    """The description in ``path`` with the tables described in table files.

    A table file (``<name>.table.yml``) holds one entry of ``tables``. They are
    looked for below the description's folder, skipping hidden folders.
    """
    declared = read(path)
    root = Path(path).resolve().parent
    files = sorted(
        found
        for pattern in TABLE_FILES
        for found in root.rglob(pattern)
        if not any(part.startswith(".") for part in found.relative_to(root).parts[:-1])
    )
    if not files:
        return declared
    return {**declared, "tables": [*declared.get("tables", []), *map(read, files)]}


def describe(declared: dict[str, Any], frozen: Path) -> dict[str, Any]:
    """The `dataset.json` of a version: ``declared`` checked against the catalog.

    The catalog wins for what it records (types, nulls, rows); the writer's
    words win over comments in the catalog. A table described but not in the
    catalog is refused, and a table in the catalog but not described is listed
    with what the catalog says about it.
    """
    declared = _renamed(declared)
    _check_keys(declared, DATASET_KEYS, "dataset.json")
    for key in ("name", "title"):
        if not isinstance(declared.get(key), str) or not declared[key].strip():
            raise DatasetError(f"dataset.json needs a {key}")
    found = _catalog_tables(frozen)
    views = _catalog_views(frozen)
    described: dict[tuple[str, str], dict[str, Any]] = {}
    for table in declared.get("tables", []):
        _check_keys(table, TABLE_KEYS, "a table")
        key = (table.get("schema"), table.get("name"))
        if key not in found and key not in views:
            raise DatasetError(f"{key[0]}.{key[1]} is described but not in the catalog")
        if key in described:
            raise DatasetError(f"{key[0]}.{key[1]} is described twice")
        described[key] = table
    tables = [_merge(described.get(key, {}), physical) for key, physical in found.items()]
    tables += [_merge_view(described.get(key, {}), view) for key, view in views.items()]
    return {**declared, "tables": lineage.fill(tables, _catalogs(declared))}


def _catalogs(declared: dict[str, Any]) -> dict[str, str]:
    """`catalogs`: the alias each other dataset is attached under, to its repository URL."""
    catalogs = declared.get("catalogs", {})
    if not isinstance(catalogs, dict) or not all(
        isinstance(alias, str) and isinstance(url, str) and "#" not in url
        for alias, url in catalogs.items()
    ):
        raise DatasetError("catalogs maps each alias to the URL of a repository (without #)")
    return catalogs


def _renamed(declared: dict[str, Any]) -> dict[str, Any]:
    """``declared`` with `extensions`, the old name of `annotations`, renamed."""
    found = []

    def rename(record: Any) -> Any:
        if not isinstance(record, dict) or OLD_ANNOTATIONS not in record:
            return record
        if "annotations" in record:
            raise DatasetError("annotations and extensions are the same thing: keep annotations")
        found.append(record)
        return {
            ("annotations" if key == OLD_ANNOTATIONS else key): value
            for key, value in record.items()
        }

    def table(record: Any) -> Any:
        record = rename(record)
        if isinstance(record, dict) and isinstance(record.get("fields"), list):
            return {**record, "fields": [rename(field) for field in record["fields"]]}
        return record

    renamed = rename(declared)
    if isinstance(renamed, dict) and isinstance(renamed.get("tables"), list):
        renamed = {**renamed, "tables": [table(record) for record in renamed["tables"]]}
    if found:
        print("fdl: extensions is now called annotations; rename it in the description", file=sys.stderr)
    return renamed


def _merge_view(table: dict[str, Any], view: dict[str, Any]) -> dict[str, Any]:
    """A view: its SQL from the catalog, and the fields as the writer describes them.

    The catalog records no columns for a view, so its fields are not checked
    and carry no type, and a view has no rows to count.
    """
    for field in table.get("fields", []):
        _check_keys(field, FIELD_KEYS, "a field")
    return {
        **_drop_none({"description": view["comment"]}),
        **{key: value for key, value in table.items() if key != "rows"},
        "schema": view["schema"],
        "name": view["name"],
        "kind": "view",
        "sql": view["sql"],
    }


def _catalog_views(frozen: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """The live views of a frozen catalog."""
    con = duckdb.connect(str(frozen), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT s.schema_name, v.view_name, v.sql,
                   (SELECT value FROM ducklake_tag g WHERE g.object_id = v.view_id
                      AND g.key = 'comment' AND g.end_snapshot IS NULL)
            FROM ducklake_view v
            JOIN ducklake_schema s ON s.schema_id = v.schema_id AND s.end_snapshot IS NULL
            WHERE v.end_snapshot IS NULL
            ORDER BY s.schema_name, v.view_name
            """
        ).fetchall()
    finally:
        con.close()
    return {
        (schema, name): {"schema": schema, "name": name, "sql": sql, "comment": comment}
        for schema, name, sql, comment in rows
    }


def to_json(description: dict[str, Any]) -> bytes:
    # YAML reads `issued: 2026-10-30` as a date; it is written back as the same text.
    return (json.dumps(description, ensure_ascii=False, indent=2, default=str) + "\n").encode()


def _check_keys(record: Any, allowed: set[str], what: str) -> None:
    """Refuse a record that is not an object, and warn of keys the format does not define.

    Such keys are kept as written: readers ignore them, and checking what a
    host makes of them is the host's (Queria's `queria validate`, say).
    """
    if not isinstance(record, dict):
        raise DatasetError(f"{what} has to be a JSON object")
    unknown = sorted(set(record) - allowed)
    if unknown:
        print(
            f"fdl: {what} has keys the format does not define: {', '.join(unknown)}; "
            f"kept as written (did you mean annotations.<your name>?)",
            file=sys.stderr,
        )


def _merge(table: dict[str, Any], physical: dict[str, Any]) -> dict[str, Any]:
    written = {}
    for field in table.get("fields", []):
        _check_keys(field, FIELD_KEYS, "a field")
        if field.get("name") not in physical["columns"]:
            raise DatasetError(
                f"{physical['schema']}.{physical['name']} has no column {field.get('name')!r}"
            )
        if "asset" in field and not isinstance(field["asset"], bool):
            raise DatasetError(f"{field['name']}: asset is true or false")
        if field.get("asset") and physical["columns"][field["name"]]["type"].lower() != "varchar":
            raise DatasetError(
                f"{physical['schema']}.{physical['name']}.{field['name']} is an asset column, "
                f"so its type has to be varchar (a sha256 in hex)"
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
        # `kind` is the catalog's to say, and a table carries none.
        **{key: value for key, value in table.items() if key != "kind"},
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
            SELECT t.table_id, s.schema_name, t.table_name,
                   -- The live files' rows less what their delete files remove.
                   -- ducklake_table_stats keeps counting rows deleted or updated.
                   (SELECT coalesce(sum(d.record_count), 0) - coalesce(sum(x.delete_count), 0)
                      FROM ducklake_data_file d
                      LEFT JOIN ducklake_delete_file x
                        ON x.data_file_id = d.data_file_id AND x.end_snapshot IS NULL
                      WHERE d.table_id = t.table_id AND d.end_snapshot IS NULL),
                   (SELECT value FROM ducklake_tag g WHERE g.object_id = t.table_id
                      AND g.key = 'comment' AND g.end_snapshot IS NULL)
            FROM ducklake_table t
            JOIN ducklake_schema s ON s.schema_id = t.schema_id AND s.end_snapshot IS NULL
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
