"""A DuckLake catalog in its two formats, and the conversion between them.

    a working catalog    SQLite, the one a build writes to
    a frozen catalog     DuckDB, the one readers attach over HTTP

SQLite is the working format because a pipeline writes to the catalog from
several connections at once (dbt runs its models in threads), which SQLite
handles with WAL and a busy timeout. DuckDB is the frozen format because that
is what readers attach over HTTP, where a single file that can be read by range
requests is what matters. Neither can do the other's job, so the conversion
runs both ways.

The conversion copies DuckLake's bookkeeping tables row by row. Inline data
(DuckLake keeps small writes inside the catalog rather than in parquet) is
copied as it is into a working catalog and refused on the way to a frozen one:
SQLite has no column types, so the rows would be read back wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

#: Options that make SQLite usable by more than one writer. WAL is set on both
#: fresh and existing catalogs, so a catalog left in `delete` mode by an older
#: version migrates on the first attach.
SQLITE_OPTIONS = ("META_JOURNAL_MODE 'WAL'", "BUSY_TIMEOUT 5000")

#: Rows kept inside the catalog instead of written to parquet. Zero: see the
#: comment where it is used.
DATA_INLINING_ROW_LIMIT = 0

#: Block size of the published catalog. DuckDB's default (256 KiB) gives each of
#: DuckLake's twenty-odd bookkeeping tables a block of its own, so even a
#: one-table catalog is about 5 MB, and a reader attaching over HTTP touches
#: nearly all of it: one range request per block. 16 KiB brings that catalog
#: under 1 MB and the requests from 29 to 9.
PUBLISHED_BLOCK_SIZE = 16384


class CatalogError(RuntimeError):
    """The catalog could not be created, opened or converted."""


def data_path_of(catalog_url: str) -> str:
    """Where a catalog's data files sit, given where the catalog itself is.

    DuckLake derives one from the other and so does everything else here, so
    the rule lives in one place.
    """
    return f"{catalog_url}.files/"


def _escape(value: str) -> str:
    return str(value).replace("'", "''")


def _attach_options(
    data_path: str, meta_type: Literal["duckdb", "sqlite"]
) -> list[str]:
    options = [
        f"DATA_PATH '{_escape(data_path)}'",
        "OVERRIDE_DATA_PATH true",
        f"META_TYPE '{meta_type}'",
        # Left at its default, DuckLake keeps small tables inside the catalog
        # and writes no parquet at all, which publishes a dataset with no data
        # files. Off, always: what gets served has to be readable by range
        # requests over HTTP.
        f"DATA_INLINING_ROW_LIMIT {DATA_INLINING_ROW_LIMIT}",
    ]
    if meta_type == "sqlite":
        options.extend(SQLITE_OPTIONS)
    return options


def attach_sql(
    catalog_path: Path, *, alias: str, data_path: str, read_only: bool = False
) -> list[str]:
    """The statements that open a catalog, in the order they must run.

    ``META_TYPE`` is always declared. Left to detect the backend from a bare
    `ducklake:<file>` path, DuckDB passes a `storage_version` option to the
    SQLite attach underneath, which SQLite rejects.
    """
    catalog_path = Path(catalog_path)
    meta_type: Literal["duckdb", "sqlite"] = (
        "sqlite" if catalog_path.suffix == ".sqlite" else "duckdb"
    )
    options = _attach_options(data_path, meta_type)
    if read_only:
        options.append("READ_ONLY")
    return [
        "INSTALL ducklake; LOAD ducklake;",
        "INSTALL sqlite; LOAD sqlite;" if meta_type == "sqlite" else "",
        f"ATTACH 'ducklake:{_escape(str(catalog_path))}' AS {alias} "
        f"({', '.join(options)})",
    ]


def create(catalog_path: Path, *, alias: str, data_path: str) -> bool:
    """Create the catalog if it is not there. Returns whether it was created."""
    import duckdb

    catalog_path = Path(catalog_path)
    if catalog_path.exists():
        return False
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        for statement in attach_sql(
            catalog_path, alias=alias, data_path=data_path
        ):
            if statement:
                con.execute(statement)
    except Exception as exc:  # noqa: BLE001 - duckdb raises several types
        raise CatalogError(f"could not create {catalog_path}: {exc}") from exc
    finally:
        con.close()
    return True


#: What a database can leave beside itself when a write does not close
#: cleanly. DuckDB writes `<db>.wal`; SQLite writes `<db>-journal`, or
#: `-wal` and `-shm` in WAL mode. **Both formats pass through `convert`**
#: (`push` writes DuckDB, `pull` writes SQLite), so both sets are handled.
_SIDECAR_SUFFIXES = (".wal", "-wal", "-shm", "-journal")


def _sidecars(path: Path) -> list[Path]:
    return [path.with_name(path.name + suffix) for suffix in _SIDECAR_SUFFIXES]


def _refuse_inline_rows(con) -> None:
    """Stop when the SQLite catalog ``src`` still holds rows inline.

    SQLite has no column types, so its inline tables are all VARCHAR and copy
    over as such. Readers then misread the rows: DuckDB 2.0 stops with
    "expected JSON for column N but read VARCHAR", and 1.5 reads a DOUBLE
    column as NULL without a word (both reproduced 2026-10-26). The writer
    flushes them before freezing, so finding any here is a bug.

    The emptied tables are not published either (`convert`): 2.0 checks the
    VARCHAR types even with no rows in them, and DuckLake makes an inline
    table again, with the right types, the next time a write is inlined.
    """
    tables = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_catalog = 'src' "
        "AND starts_with(table_name, 'ducklake_inlined_data_') "
        "AND table_name <> 'ducklake_inlined_data_tables'"
    ).fetchall()
    held = [
        name
        for (name,) in tables
        if con.execute(f"SELECT 1 FROM src.main.{name} LIMIT 1").fetchone()
    ]
    if held:
        raise CatalogError(
            f"the catalog still holds rows inline ({', '.join(held)}); "
            f"they would be published as VARCHAR. Flush them first."
        )


def convert(
    source: Path,
    target: Path,
    *,
    data_path: str,
) -> None:
    """Copy a catalog into the other format, replacing ``target``.

    Written through a temporary file and renamed once it is complete, so an
    interrupted conversion never leaves a half-copied catalog where a working
    one used to be.
    """
    import duckdb

    source = Path(source)
    target = Path(target)
    if not source.is_file():
        raise CatalogError(f"no catalog at {source}")
    target_type: Literal["duckdb", "sqlite"] = (
        "sqlite" if target.suffix == ".sqlite" else "duckdb"
    )
    source_type: Literal["duckdb", "sqlite"] = (
        "sqlite" if source.suffix == ".sqlite" else "duckdb"
    )

    tmp = target.with_name(target.name + ".tmp")
    leftovers = [tmp, *_sidecars(tmp)]
    # A run killed before its cleanup leaves these behind. Attached as they
    # are, the copy would land on top of that catalog and keep its tables.
    for path in leftovers:
        if path.exists():
            path.unlink()
    try:
        con = duckdb.connect()
        try:
            con.execute("INSTALL ducklake; LOAD ducklake;")
            con.execute("INSTALL sqlite; LOAD sqlite;")
            if target_type == "duckdb":
                # The block size is fixed when the file is created, so the
                # file is made here before DuckLake attaches to it.
                con.execute(
                    f"ATTACH '{_escape(str(tmp))}' AS blk "
                    f"(BLOCK_SIZE {PUBLISHED_BLOCK_SIZE})"
                )
                con.execute("DETACH blk")
            # Provision the destination through the extension so its
            # bookkeeping tables exist, then talk to both sides as plain
            # databases: the tables being copied are DuckLake's own, which the
            # extension does not expose.
            con.execute(
                f"ATTACH 'ducklake:{_escape(str(tmp))}' AS dst "
                f"({', '.join(_attach_options(data_path, target_type))})"
            )
            con.execute("DETACH dst")

            con.execute(
                f"ATTACH '{_escape(str(source))}' AS src"
                + (" (TYPE sqlite)" if source_type == "sqlite" else "")
            )
            con.execute(
                f"ATTACH '{_escape(str(tmp))}' AS dst"
                + (" (TYPE sqlite)" if target_type == "sqlite" else "")
            )

            # Publishing: the inline tables are all empty here, and are left out
            publishing = source_type == "sqlite" and target_type == "duckdb"
            if publishing:
                _refuse_inline_rows(con)

            provisioned = {
                row[0]
                for row in con.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_catalog = 'dst'"
                ).fetchall()
            }
            for (table,) in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_catalog = 'src'"
            ).fetchall():
                if publishing and table.startswith("ducklake_inlined_data_"):
                    # The registry too, so no reader looks for the tables. The
                    # destination is fresh, so its own registry is empty.
                    continue
                if table in provisioned:
                    con.execute(f"DELETE FROM dst.main.{table}")
                    con.execute(
                        f"INSERT INTO dst.main.{table} SELECT * FROM src.main.{table}"
                    )
                else:
                    # Tables a fresh catalog does not provision, such as the
                    # inline data of small writes.
                    con.execute(
                        f"CREATE TABLE dst.main.{table} AS "
                        f"SELECT * FROM src.main.{table}"
                    )
            con.execute("CHECKPOINT dst")
        finally:
            con.close()

        if target.exists():
            target.unlink()
        # Sidecars next to the target belong to the catalog being replaced.
        # Keeping them replays one database's log into another the next time
        # the file is opened, so they go with the file they belong to.
        for path in _sidecars(target):
            if path.exists():
                path.unlink()
        tmp.rename(target)
    except CatalogError:
        raise
    except Exception as exc:  # noqa: BLE001 - duckdb raises several types
        raise CatalogError(f"could not convert {source} to {target}: {exc}") from exc
    finally:
        for path in leftovers:
            if path.exists():
                path.unlink()


# -- what a catalog records about itself ---------------------------------------

DATA_PATH_KEY = "data_path"

#: The key a catalog records its own DuckLake format version under.
FORMAT_KEY = "version"

#: Every table that names a file. All of them must hold relative paths.
_FILE_TABLES = ("ducklake_data_file", "ducklake_delete_file")


@dataclass(frozen=True)
class AbsolutePath:
    table: str
    path: str


def _open(catalog_path: Path):
    import duckdb

    try:
        return duckdb.connect(str(catalog_path))
    except Exception as exc:  # noqa: BLE001 - duckdb raises several types here
        hint = ""
        if catalog_path.suffix == ".sqlite":
            # The working format. Readers get the DuckDB one.
            hint = " This is the working catalog; convert it first."
        raise CatalogError(
            f"cannot open the catalog {catalog_path}: {exc}{hint}"
        ) from exc


def _table_exists(con, table: str) -> bool:
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [table],
    ).fetchone()
    return bool(row and row[0])


def recorded_data_path(catalog_path: Path) -> str | None:
    """Return the data path currently recorded in the catalog."""
    con = _open(Path(catalog_path))
    try:
        if not _table_exists(con, "ducklake_metadata"):
            return None
        row = con.execute(
            f"SELECT value FROM ducklake_metadata WHERE key = '{DATA_PATH_KEY}'"
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def recorded_format(catalog_path: Path) -> str | None:
    """Return the DuckLake format version the catalog records for itself."""
    con = _open(Path(catalog_path))
    try:
        if not _table_exists(con, "ducklake_metadata"):
            return None
        row = con.execute(
            f"SELECT value FROM ducklake_metadata "
            f"WHERE key = '{FORMAT_KEY}' AND scope IS NULL"
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def set_data_path(catalog_path: Path, value: str) -> None:
    """Rewrite the catalog's data path to ``value``.

    ``value`` is where readers will find the data files, which can differ
    from the place the build wrote them to.
    """
    catalog_path = Path(catalog_path)
    con = _open(catalog_path)
    row = None
    try:
        if _table_exists(con, "ducklake_metadata"):
            con.execute(
                "UPDATE ducklake_metadata SET value = ? "
                f"WHERE key = '{DATA_PATH_KEY}' AND scope IS NULL",
                [value],
            )
            row = con.execute(
                f"SELECT count(*) FROM ducklake_metadata "
                f"WHERE key = '{DATA_PATH_KEY}' AND value = ?",
                [value],
            ).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        raise CatalogError(
            f"{catalog_path}: this is not a DuckLake catalog (no "
            f"{DATA_PATH_KEY} to rewrite)."
        )


def absolute_paths(catalog_path: Path) -> list[AbsolutePath]:
    """Return every file reference that is not relative to the data path."""
    con = _open(Path(catalog_path))
    found: list[AbsolutePath] = []
    try:
        for table in _FILE_TABLES:
            if not _table_exists(con, table):
                continue
            rows = con.execute(
                f"SELECT path FROM {table} WHERE NOT path_is_relative"
            ).fetchall()
            found.extend(AbsolutePath(table=table, path=row[0]) for row in rows)
    finally:
        con.close()
    return found


def data_files(catalog_path: Path) -> list[str]:
    """Return the data files' keys, relative to the data path.

    DuckLake stores each part separately (schema, table, file), so the key is
    the three joined. Used where the
    bucket cannot be listed.
    """
    con = _open(Path(catalog_path))
    keys: set[str] = set()
    try:
        for table in _FILE_TABLES:
            if not _table_exists(con, table):
                continue
            rows = con.execute(f"""
                SELECT DISTINCT s.path, t.path, f.path
                FROM {table} f
                JOIN ducklake_table t ON t.table_id = f.table_id
                JOIN ducklake_schema s ON s.schema_id = t.schema_id
                WHERE f.path_is_relative
            """).fetchall()
            keys.update("".join(part or "" for part in row) for row in rows)
    finally:
        con.close()
    return sorted(keys)


# -- what a version records about its catalog ----------------------------------


def storage_version(catalog_path: Path) -> str:
    """The file's storage format as a version records it, such as
    ``duckdb-v1.5.0`` (fdl.md's `catalog.storage`).

    A reader on an older DuckDB cannot open a newer file at all, whatever the
    DuckLake format inside it, so a version records both. DuckDB reports the
    tag as ``v1.5.0+``; the ``+`` (and later) is what every storage tag means,
    so it is dropped rather than written into the format.
    """
    import duckdb

    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{_escape(str(catalog_path))}' AS f (READ_ONLY)")
        row = con.execute(
            "SELECT tags['storage_version'] FROM duckdb_databases() "
            "WHERE database_name = 'f'"
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 - duckdb raises several types
        raise CatalogError(f"cannot open the catalog {catalog_path}: {exc}") from exc
    finally:
        con.close()
    if not row or not row[0]:
        raise CatalogError(f"{catalog_path} records no storage version")
    return f"duckdb-{row[0].rstrip('+')}"


def snapshot_range(catalog_path: Path, after: int | None) -> tuple[int, int]:
    """The first and last snapshot past ``after`` (the parent's last one).

    A version holds the snapshots its writer made since the version it was
    built on. With nothing new the range is the last snapshot alone, which is
    what a version made again from the same state holds.
    """
    con = _open(Path(catalog_path))
    try:
        last = con.execute("SELECT max(snapshot_id) FROM ducklake_snapshot").fetchone()[0]
        first = con.execute(
            "SELECT min(snapshot_id) FROM ducklake_snapshot WHERE snapshot_id > ?",
            [-1 if after is None else after],
        ).fetchone()[0]
    except Exception as exc:  # noqa: BLE001 - duckdb raises several types
        raise CatalogError(f"{catalog_path} is not a DuckLake catalog: {exc}") from exc
    finally:
        con.close()
    if last is None:
        raise CatalogError(f"{catalog_path} holds no snapshot")
    return (first if first is not None else last, last)
