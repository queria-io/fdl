"""The reader adapter for fdl: `python conformance/adapters/fdl_reader.py <root> <ref>`.

Finds the version as `fdl url` does, attaches its catalog the way the
specification says (Reading) and counts the rows of every table.
"""

from __future__ import annotations

import json
import sys

import duckdb

from fdl import layout, refs, repo
from fdl.stores import open_store


def main(root: str, ref: str) -> None:
    store = open_store(root)
    version = repo.readable(store, ref)
    catalog = store.url.rstrip("/") + "/" + layout.version_catalog(version.id)
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(
        f"ATTACH 'ducklake:{catalog}' AS lake "
        f"(DATA_PATH '{repo.data_url(store)}', OVERRIDE_DATA_PATH true, READ_ONLY)"
    )
    names = con.execute(
        "SELECT schema_name, table_name FROM duckdb_tables() WHERE database_name = 'lake'"
    ).fetchall()
    tables = {
        f"{schema}.{name}": {"rows": con.execute(f'SELECT count(*) FROM lake."{schema}"."{name}"').fetchone()[0]}
        for schema, name in names
    }
    print(json.dumps({"version": version.id, "seq": version.seq, "tables": tables}))


if __name__ == "__main__":
    try:
        main(sys.argv[1], sys.argv[2])
    except (repo.RepoError, refs.RefError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        sys.exit(1)
