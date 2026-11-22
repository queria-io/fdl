"""Run the collection cases every implementation of the spec agrees on (cases/collection.json)."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest

from fdl import catalog, repo
from fdl.stores import LocalStore

CASES = json.loads((Path(__file__).parent.parent / "cases" / "collection.json").read_text())


def _time(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda case: case["name"])
def test_collection_case(tmp_path: Path, case: dict) -> None:
    root = tmp_path / "repo"
    for key, value in case["files"].items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    for key, placed in case["objects"].items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        when = _time(placed).timestamp()
        os.utime(path, (when, when))
    for key, tables in case.get("metadata", {}).items():
        _make_metadata(root / key, root / repo.read_config(LocalStore(root)).data_dir, tables)
    store = LocalStore(root)
    try:
        plan = repo.plan_collection(
            store, grace=dt.timedelta(seconds=CASES["grace_seconds"]), now=_time(CASES["now"])
        )
        deleted = repo.collect(store, plan=plan)
    except repo.RepoError:
        # Stopping without deleting is how this implementation deletes nothing.
        deleted = []
    assert sorted(deleted) == sorted(case["deleted"])
    for key in case["objects"]:
        assert (root / key).exists() == (key not in case["deleted"])


def _make_metadata(path: Path, data: Path, tables: dict) -> None:
    """A DuckLake metadata at ``path`` whose tables hold the rows a case gives, with data files under ``data``."""
    import duckdb

    def q(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    con = duckdb.connect()
    try:
        for statement in catalog.attach_sql(path, alias="lake", data_path=data.as_posix() + "/"):
            if statement:
                con.execute(statement)
        for name, table in tables.items():
            schema, table_name = name.split(".", 1)
            con.execute(f"CREATE SCHEMA IF NOT EXISTS lake.{q(schema)}")
            target = f"lake.{q(schema)}.{q(table_name)}"
            con.execute(f"CREATE TABLE {target} ({', '.join(f'{q(c)} {t}' for c, t in table['columns'].items())})")
            for row in table["rows"]:
                columns = list(row)
                con.execute(
                    f"INSERT INTO {target} ({', '.join(map(q, columns))}) VALUES ({', '.join('?' for _ in columns)})",
                    [row[c] for c in columns],
                )
    finally:
        con.close()
