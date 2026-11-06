"""Profiles: a summary of each table, written beside a version for readers that show it.

A profile holds a table's row count, statistics of each column, its first rows
and, for a table with a geometry column, a sample of its shapes. A reader can
show a table from this one small file without opening the catalog or reading
the data. It is made from the catalog and the data, which win where the two
differ, and a reader works without it (see the specification, "profiles").
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import catalog

#: Rows in `head`. A glance; more is what a query is for.
HEAD_ROWS = 10
#: Shapes in `geometry`, sampled from the whole table rather than its first rows.
GEOMETRY_SAMPLE = 500
#: Vertex tolerance of the sampled shapes, in the column's units (about 11 m in degrees).
SIMPLIFY_TOLERANCE = 0.0001
HISTOGRAM_BUCKETS = 20
#: A column with at most this many distinct values lists them all in `values`.
ALL_VALUES = 1000
#: Otherwise `values` lists the most common.
TOP_VALUES = 10
#: Above this many rows, distinct values are estimated rather than counted.
EXACT_DISTINCT_ROWS = 1_000_000

_NUMERIC = {
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT",
    "UINTEGER", "UBIGINT", "UHUGEINT", "FLOAT", "DOUBLE", "DECIMAL", "REAL", "NUMERIC",
    "INT8", "INT16", "INT32", "INT64", "INT128", "UINT8", "UINT16", "UINT32", "UINT64",
    "UINT128", "FLOAT32", "FLOAT64",
}  # fmt: skip
_TEMPORAL = {"DATE", "TIME", "TIMESTAMP", "TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE", "TIMETZ", "INTERVAL"}
_DATED = {"DATE", "TIMESTAMP", "TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE"}
_BOOLEAN = {"BOOLEAN"}
_TEXT = {"VARCHAR", "UUID"}


def _base(data_type: str) -> str:
    return re.sub(r"\(.*\)", "", data_type.upper()).strip()


def kind(data_type: str) -> str:
    """The family of a DuckLake type, which decides the statistics a column gets."""
    base = _base(data_type)
    if base in _NUMERIC:
        return "numeric"
    if base in _TEMPORAL:
        return "temporal"
    if base in _BOOLEAN:
        return "boolean"
    if base in _TEXT:
        return "text"
    if base == "GEOMETRY":
        return "geometry"
    return "other"


def path(schema: str, table: str) -> str | None:
    """The profile's path under the version, or None for a name a path cannot hold."""
    if any(not part or part in (".", "..") or "/" in part for part in (schema, table)):
        return None
    return f"profiles/{schema}/{table}.json"


def tables(described: dict[str, Any]) -> list[dict[str, Any]]:
    """The tables of a `dataset.json` that get a profile: every table, no view."""
    return [t for t in described.get("tables", []) if t.get("kind") != "view" and path(t["schema"], t["name"])]


def columns_of(table: dict[str, Any]) -> list[dict[str, str]]:
    return [{"name": f["name"], "type": f.get("type", "")} for f in table.get("fields", [])]


def build(working: Path, wanted: Iterable[dict[str, Any]], *, data_path: str, setup: Iterable[str] = ()) -> dict[str, bytes]:
    """Profiles of ``wanted`` (tables of `dataset.json`) from the working catalog's last snapshot.

    Returns path to content. A table that cannot be read gets no profile, and
    a geometry sample that cannot be made (no spatial extension) is left out.
    """
    import duckdb

    wanted = list(wanted)
    if not wanted:
        return {}
    con = duckdb.connect()
    try:
        for statement in setup:
            con.execute(statement)
        for statement in catalog.attach_sql(Path(working), alias="lake", data_path=data_path, read_only=True):
            if statement:
                con.execute(statement)
        spatial = _load_spatial(con)
        made: dict[str, bytes] = {}
        for table in wanted:
            try:
                profile = _profile(con, table, spatial)
            except duckdb.Error:
                continue
            made[path(table["schema"], table["name"])] = _dump(profile)
        return made
    finally:
        con.close()


def reusable(previous: bytes, table: dict[str, Any]) -> bool:
    """Whether a parent's profile describes ``table`` as it is: the same columns and types."""
    try:
        held = json.loads(previous).get("columns", [])
    except ValueError:
        return False
    return [{"name": c.get("name"), "type": c.get("type")} for c in held] == columns_of(table)



def _load_spatial(con) -> bool:
    try:
        con.execute("LOAD spatial")
        return True
    except Exception:  # noqa: BLE001 - not installed: try once, then do without
        try:
            con.execute("INSTALL spatial")
            con.execute("LOAD spatial")
            return True
        except Exception:  # noqa: BLE001
            return False


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _profile(con, table: dict[str, Any], spatial: bool) -> dict[str, Any]:
    relation = f"lake.{_q(table['schema'])}.{_q(table['name'])}"
    fields = columns_of(table)
    rows = int(con.execute(f"SELECT count(*) FROM {relation}").fetchone()[0])

    measured = [f for f in fields if kind(f["type"]) != "geometry"]
    exprs = [e for f in measured for e in _aggregates(f, rows)]
    values = con.execute(f"SELECT {', '.join(exprs)} FROM {relation}").fetchone() if exprs else ()
    stats: list[dict[str, Any]] = []
    cursor = 0
    for field in fields:
        if kind(field["type"]) == "geometry":
            stats.append(dict(field))
            continue
        width = len(_aggregates(field, rows))
        stats.append(_column(con, relation, field, rows, values[cursor : cursor + width]))
        cursor += width

    shown = [f["name"] for f in fields if kind(f["type"]) not in ("geometry", "other")]
    head: list[dict[str, Any]] = []
    if shown:
        encoded = con.execute(
            f"SELECT to_json(list(t)) FROM (SELECT {', '.join(_q(c) for c in shown)} FROM {relation} LIMIT {HEAD_ROWS}) t"
        ).fetchone()
        head = json.loads(encoded[0]) if encoded and encoded[0] else []

    profile: dict[str, Any] = {"rows": rows, "columns": stats, "head": head}
    geometry = next((f["name"] for f in fields if kind(f["type"]) == "geometry"), None)
    if geometry is not None and spatial:
        try:
            profile["geometry"] = _geometry(con, relation, geometry)
        except Exception:  # noqa: BLE001 - the rest stands without the shapes
            pass
    return profile


def _aggregates(field: dict[str, str], rows: int) -> list[str]:
    c = _q(field["name"])
    distinct = f"count(DISTINCT {c})" if rows <= EXACT_DISTINCT_ROWS else f"approx_count_distinct({c})"
    family = kind(field["type"])
    head = [f"count({c})"]
    if family == "numeric":
        v = f"{c}::DOUBLE"
        return head + [
            distinct,
            f"min({v})",
            f"max({v})",
            f"avg({v})",
            f"stddev_samp({v})",
            f"approx_quantile({v}, [0.25, 0.5, 0.75])",
        ]
    if family == "text":
        return head + [distinct]
    if family == "temporal":
        span = (
            f"datediff('day', min({c})::TIMESTAMP, max({c})::TIMESTAMP)" if _base(field["type"]) in _DATED else "NULL"
        )
        return head + [f"min({c})::VARCHAR", f"max({c})::VARCHAR", span, distinct]
    if family == "boolean":
        return head + [f"count_if({c})", f"count_if(NOT {c})"]
    return head


def _column(con, relation: str, field: dict[str, str], rows: int, values) -> dict[str, Any]:
    out: dict[str, Any] = {**field, "null_count": rows - int(values[0])}
    family = kind(field["type"])
    if family == "numeric":
        lo, hi, mean, std = (_number(v) for v in values[2:6])
        q1, median, q3 = [_number(q) for q in values[6]] if values[6] else (None, None, None)
        out["distinct_count"] = int(values[1] or 0)
        out.update(min=lo, max=hi, mean=mean, std=std, q1=q1, median=median, q3=q3)
        _values(con, relation, field["name"], out)
        out["histogram"] = _histogram(con, relation, field["name"], lo, hi)
    elif family == "text":
        out["distinct_count"] = int(values[1] or 0)
        _values(con, relation, field["name"], out)
    elif family == "temporal":
        lo, hi, span, distinct = values[1:5]
        out.update(min=lo, max=hi, span_days=None if span is None else int(span), distinct_count=int(distinct or 0))
        _values(con, relation, field["name"], out)
    elif family == "boolean":
        out.update(true_count=int(values[1] or 0), false_count=int(values[2] or 0))
    return out


def _histogram(con, relation: str, name: str, lo: float | None, hi: float | None) -> list[dict[str, float]]:
    if lo is None or hi is None:
        return []
    width = (hi - lo) / HISTOGRAM_BUCKETS if hi > lo else 1.0
    found = con.execute(
        f"SELECT least(floor(({_q(name)}::DOUBLE - ?) / ?)::BIGINT, ?), count(*) "
        f"FROM {relation} WHERE {_q(name)} IS NOT NULL GROUP BY 1 ORDER BY 1",
        [lo, width, HISTOGRAM_BUCKETS - 1],
    ).fetchall()
    return [{"start": lo + int(b) * width, "end": lo + (int(b) + 1) * width, "count": int(n)} for b, n in found]


def _values(con, relation: str, name: str, out: dict[str, Any]) -> None:
    """Every value with its count when there are at most `ALL_VALUES`, else the most common.

    Counted here, so `distinct_count` is exact whenever the list is whole: a
    reader tells a whole list by its length matching `distinct_count`.
    """
    found = con.execute(
        f"SELECT {_q(name)}::VARCHAR, count(*) FROM {relation} "
        f"WHERE {_q(name)} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {ALL_VALUES + 1}"
    ).fetchall()
    if len(found) <= ALL_VALUES:
        out["distinct_count"] = len(found)
    else:
        found = found[:TOP_VALUES]
        out["distinct_count"] = max(out.get("distinct_count", 0), ALL_VALUES + 1)
    out["values"] = [{"value": v, "count": int(n)} for v, n in found]


def _geometry(con, relation: str, name: str) -> dict[str, Any]:
    c = _q(name)
    total, xmin, ymin, xmax, ymax = con.execute(
        f"SELECT count({c}), min(ST_XMin({c})), min(ST_YMin({c})), max(ST_XMax({c})), max(ST_YMax({c})) "
        f"FROM {relation}"
    ).fetchone()
    kinds = [
        k
        for (k,) in con.execute(
            f"SELECT DISTINCT ST_GeometryType({c})::VARCHAR FROM {relation} WHERE {c} IS NOT NULL ORDER BY 1"
        ).fetchall()
    ]
    sampled = con.execute(
        f"SELECT ST_AsGeoJSON(ST_Simplify(g, {SIMPLIFY_TOLERANCE})) FROM "
        f"(SELECT {c} AS g FROM {relation} WHERE {c} IS NOT NULL) USING SAMPLE reservoir({GEOMETRY_SAMPLE} ROWS)"
    ).fetchall()
    features = []
    for (raw,) in sampled:
        if not raw:
            continue
        shape = json.loads(raw)
        parts = shape.get("geometries", []) if shape.get("type") == "GeometryCollection" else [shape]
        features.extend({"type": "Feature", "geometry": part, "properties": {}} for part in parts)
    return {
        "column": name,
        "count": int(total),
        "bbox": None if xmin is None else [xmin, ymin, xmax, ymax],
        "types": kinds,
        "sample": {"type": "FeatureCollection", "features": features},
    }


def _number(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return None if math.isnan(number) or math.isinf(number) else number


def _dump(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
