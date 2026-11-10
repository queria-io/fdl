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
from typing import Iterable, Literal

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

#: The DuckLake format and DuckDB storage version every catalog is written in,
#: whatever DuckDB writes it. DuckDB 2.0 would otherwise write format 1.1 in
#: storage v2.0.0, which DuckDB 1.5 (and the DuckDB-WASM built on it) cannot
#: open.
DUCKLAKE_FORMAT = "1.0"
PUBLISHED_STORAGE_VERSION = "v1.5.0"


def _duckdb_major() -> int:
    import duckdb

    return int(duckdb.__version__.split(".")[0])


def _format_options() -> list[str]:
    """The option that makes a new catalog in `DUCKLAKE_FORMAT`.

    DuckDB 1.5 writes that format already and rejects the option.
    """
    return [f"DUCKLAKE_VERSION '{DUCKLAKE_FORMAT}'"] if _duckdb_major() >= 2 else []


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
    catalog_path: Path,
    *,
    alias: str,
    data_path: str,
    read_only: bool = False,
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
    if not catalog_path.exists():
        # A new catalog, made in the format every reader can open.
        options.extend(_format_options())
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


def _downgrade_storage(path: Path) -> None:
    """Rewrite a DuckDB file in `PUBLISHED_STORAGE_VERSION`.

    DuckDB 2.0 upgrades the file's storage when DuckLake attaches to it, even
    one created older. A copy into a file created in the old version keeps it.
    """
    import duckdb

    copy = path.with_name(path.name + ".old")
    for leftover in [copy, *_sidecars(copy)]:
        if leftover.exists():
            leftover.unlink()
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{_escape(str(path))}' AS src (READ_ONLY)")
        con.execute(
            f"ATTACH '{_escape(str(copy))}' AS dst "
            f"(STORAGE_VERSION '{PUBLISHED_STORAGE_VERSION}', "
            f"BLOCK_SIZE {PUBLISHED_BLOCK_SIZE})"
        )
        con.execute("COPY FROM DATABASE src TO dst")
        con.execute("CHECKPOINT dst")
    finally:
        con.close()
    copy.replace(path)


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
                f"({', '.join(_attach_options(data_path, target_type) + _format_options())})"
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
        if target_type == "duckdb" and _duckdb_major() >= 2:
            _downgrade_storage(tmp)

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


def pin_files(catalog_path: Path, data_url: str) -> int:
    """Turn every relative file reference into an absolute one under
    ``data_url``, and return how many there were.

    For a working catalog whose data path is a folder on this machine: the
    files already published stay readable where they are, and new ones land
    in the folder. `unpin_files` undoes it.
    """
    return _rewrite_files(catalog_path, data_url, pin=True)


def unpin_files(catalog_path: Path, data_url: str) -> int:
    """Turn every absolute file reference under ``data_url`` back into a
    relative one, and return how many there were."""
    return _rewrite_files(catalog_path, data_url, pin=False)


def _rewrite_files(catalog_path: Path, data_url: str, *, pin: bool) -> int:
    con = _open(Path(catalog_path))
    changed = 0
    try:
        for table in _FILE_TABLES:
            if not _table_exists(con, table):
                continue
            key = "data_file_id" if table == "ducklake_data_file" else "delete_file_id"
            rows = con.execute(
                f"""
                SELECT DISTINCT f.{key}, f.path, coalesce(s.path, '') || coalesce(t.path, '')
                FROM {table} f
                JOIN ducklake_table t ON t.table_id = f.table_id
                JOIN ducklake_schema s ON s.schema_id = t.schema_id
                WHERE f.path_is_relative = ?
                """,
                [pin],
            ).fetchall()
            for row_id, path, prefix in rows:
                if pin:
                    new_path, relative = data_url + prefix + path, False
                elif path.startswith(data_url + prefix):
                    new_path, relative = path[len(data_url + prefix) :], True
                else:
                    continue
                con.execute(
                    f"UPDATE {table} SET path = ?, path_is_relative = ? WHERE {key} = ?",
                    [new_path, relative, row_id],
                )
                changed += 1
    finally:
        con.close()
    return changed


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


def table_files(catalog_path: Path, snapshot_id: int, *, absolute: bool = False) -> dict[str, dict]:
    """Each table's files at ``snapshot_id``, keyed ``schema.table``.

    For readers without DuckDB: what to read for a table, the rows each data
    file has deleted, and whether a column was ever renamed. Paths are
    relative to the data path, as in `data_files`. A working catalog reads
    files already in a repository over HTTP by their URL: ``absolute`` keeps
    those as they are rather than refusing them.
    """
    con = _open(Path(catalog_path))
    live = "begin_snapshot <= ? AND (end_snapshot IS NULL OR end_snapshot > ?)"
    try:
        tables = con.execute(
            """
            SELECT t.table_id, s.schema_name || '.' || t.table_name,
                   coalesce(s.path, '') || coalesce(t.path, '')
            FROM ducklake_table t
            JOIN ducklake_schema s ON s.schema_id = t.schema_id
            WHERE t.begin_snapshot <= ? AND (t.end_snapshot IS NULL OR t.end_snapshot > ?)
              AND s.begin_snapshot <= ? AND (s.end_snapshot IS NULL OR s.end_snapshot > ?)
            ORDER BY 2
            """,
            [snapshot_id] * 4,
        ).fetchall()
        files = con.execute(
            f"""
            SELECT data_file_id, table_id, path, path_is_relative, record_count,
                   file_size_bytes, footer_size
            FROM ducklake_data_file WHERE {live}
            ORDER BY table_id, file_order NULLS LAST, data_file_id
            """,
            [snapshot_id] * 2,
        ).fetchall()
        deletes = (
            con.execute(
                f"""
                SELECT data_file_id, path, path_is_relative, delete_count, file_size_bytes
                FROM ducklake_delete_file WHERE {live}
                """,
                [snapshot_id] * 2,
            ).fetchall()
            if _table_exists(con, "ducklake_delete_file")
            else []
        )
        # A renamed column keeps its id under a new name, while files written
        # before the rename still carry the old one.
        renamed = {
            row[0]
            for row in con.execute(
                """
                SELECT DISTINCT table_id FROM ducklake_column
                WHERE begin_snapshot <= ?
                GROUP BY table_id, column_id
                HAVING count(DISTINCT column_name) > 1
                """,
                [snapshot_id],
            ).fetchall()
        }
    except Exception as exc:  # noqa: BLE001 - duckdb raises several types
        raise CatalogError(f"{catalog_path} is not a DuckLake catalog: {exc}") from exc
    finally:
        con.close()

    prefixes = {table_id: prefix for table_id, _, prefix in tables}

    def where(table_id: int, path: str, relative: bool) -> dict:
        if not relative:
            if absolute:
                return {"path": path}
            raise CatalogError(f"{catalog_path} names a data file by an absolute path: {path}")
        return {"path": prefixes[table_id] + path}

    table_of = {data_id: table_id for data_id, table_id, *_ in files}
    deleted = {
        data_id: {**where(table_of[data_id], path, relative), "rows": rows, "bytes": size}
        for data_id, path, relative, rows, size in deletes
        if data_id in table_of
    }
    listed = {
        table_id: [
            {
                **where(table_id, path, relative),
                "rows": rows,
                "bytes": size,
                **({"footer_bytes": footer} if footer is not None else {}),
                **({"deletes": deleted[data_id]} if data_id in deleted else {}),
            }
            for data_id, owner, path, relative, rows, size, footer in files
            if owner == table_id
        ]
        for table_id in prefixes
    }
    return {
        name: {
            "files": listed[table_id],
            **({"renamed_columns": True} if table_id in renamed else {}),
        }
        for table_id, name, _ in tables
    }


#: Type changes that keep every value: SQL written for the old type still runs.
_WIDENINGS = {
    ("int8", "int16"), ("int8", "int32"), ("int8", "int64"),
    ("int16", "int32"), ("int16", "int64"),
    ("int32", "int64"),
    ("uint8", "uint16"), ("uint8", "uint32"), ("uint8", "uint64"),
    ("uint8", "int16"), ("uint8", "int32"), ("uint8", "int64"),
    ("uint16", "uint32"), ("uint16", "uint64"), ("uint16", "int32"), ("uint16", "int64"),
    ("uint32", "uint64"), ("uint32", "int64"),
    ("float32", "float64"),
}


def is_breaking_type_change(old: str, new: str) -> bool:
    """Whether SQL written for a column of type ``old`` may fail on ``new``."""
    return (old, new) not in _WIDENINGS


def table_columns(catalog_path: Path, snapshot: int) -> dict[str, dict[str, str]]:
    """Each table's top-level columns and their types at ``snapshot``, keyed ``schema.table``."""
    con = _open(Path(catalog_path))
    try:
        rows = con.execute(
            """
            SELECT s.schema_name || '.' || t.table_name, c.column_name, c.column_type
            FROM ducklake_column c
            JOIN ducklake_table t ON t.table_id = c.table_id
              AND t.begin_snapshot <= ? AND (t.end_snapshot IS NULL OR t.end_snapshot > ?)
            JOIN ducklake_schema s ON s.schema_id = t.schema_id
              AND s.begin_snapshot <= ? AND (s.end_snapshot IS NULL OR s.end_snapshot > ?)
            WHERE c.parent_column IS NULL
              AND c.begin_snapshot <= ? AND (c.end_snapshot IS NULL OR c.end_snapshot > ?)
            ORDER BY 1, c.column_order
            """,
            [snapshot] * 6,
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - duckdb raises several types
        raise CatalogError(f"{catalog_path} is not a DuckLake catalog: {exc}") from exc
    finally:
        con.close()
    found: dict[str, dict[str, str]] = {}
    for table, column, kind in rows:
        found.setdefault(table, {})[column] = kind
    return found


def has_snapshot(catalog_path: Path, snapshot: int) -> bool:
    """Whether the catalog still holds ``snapshot`` (expiring one drops it)."""
    con = _open(Path(catalog_path))
    try:
        return bool(
            con.execute(
                "SELECT count(*) FROM ducklake_snapshot WHERE snapshot_id = ?", [snapshot]
            ).fetchone()[0]
        )
    finally:
        con.close()


def column_changes(catalog_path: Path, before: int, after: int) -> list[dict]:
    """The column differences between two snapshots of one catalog, as
    ``changes`` in version.json records them.

    Tables are matched by name. A table kept under the same id is compared
    column by column id, so a rename shows as one; one made again (as a
    rebuild does) is compared by column name. Tables added or dropped are not
    listed. Nested fields are not compared.
    """
    con = _open(Path(catalog_path))
    live = "begin_snapshot <= ? AND (end_snapshot IS NULL OR end_snapshot > ?)"
    try:
        if not con.execute(
            "SELECT count(*) FROM ducklake_snapshot WHERE snapshot_id = ?", [before]
        ).fetchone()[0]:
            return []

        def tables(snapshot: int) -> dict[str, int]:
            return dict(
                con.execute(
                    """
                    SELECT s.schema_name || '.' || t.table_name, t.table_id
                    FROM ducklake_table t JOIN ducklake_schema s ON s.schema_id = t.schema_id
                    WHERE t.begin_snapshot <= ? AND (t.end_snapshot IS NULL OR t.end_snapshot > ?)
                      AND s.begin_snapshot <= ? AND (s.end_snapshot IS NULL OR s.end_snapshot > ?)
                    """,
                    [snapshot] * 4,
                ).fetchall()
            )

        def columns(table_id: int, snapshot: int) -> list[tuple[int, str, str]]:
            return con.execute(
                f"""
                SELECT column_id, column_name, column_type FROM ducklake_column
                WHERE table_id = ? AND parent_column IS NULL AND {live}
                ORDER BY column_order
                """,
                [table_id, snapshot, snapshot],
            ).fetchall()

        old, new = tables(before), tables(after)
        found: list[dict] = []
        for name in sorted(old.keys() & new.keys()):
            was, now = columns(old[name], before), columns(new[name], after)
            same_table = old[name] == new[name]
            key = (lambda c: c[0]) if same_table else (lambda c: c[1])
            was_by, now_by = {key(c): c for c in was}, {key(c): c for c in now}
            for _, column, _ in (c for c in now if key(c) not in was_by):
                found.append({"table": name, "kind": "add_column", "column": column, "breaking": False})
            for _, column, _ in (c for c in was if key(c) not in now_by):
                found.append({"table": name, "kind": "drop_column", "column": column, "breaking": True})
            for k in (key(c) for c in now if key(c) in was_by):
                _, old_name, old_type = was_by[k]
                _, new_name, new_type = now_by[k]
                if old_name != new_name:
                    found.append({
                        "table": name, "kind": "rename_column", "column": new_name,
                        "from": old_name, "breaking": True,
                    })
                if old_type != new_type:
                    found.append({
                        "table": name, "kind": "change_type", "column": new_name,
                        "from_type": old_type, "to_type": new_type,
                        "breaking": is_breaking_type_change(old_type, new_type),
                    })
    except Exception as exc:  # noqa: BLE001 - duckdb raises several types
        raise CatalogError(f"{catalog_path} is not a DuckLake catalog: {exc}") from exc
    finally:
        con.close()
    return found


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


# -- whether anything changed --------------------------------------------------


def working_data_path(working: Path) -> str:
    import sqlite3

    con = sqlite3.connect(f"file:{working}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT value FROM ducklake_metadata WHERE key = ? AND scope IS NULL",
            (DATA_PATH_KEY,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise CatalogError(f"{working} is not a working catalog: {exc}") from exc
    finally:
        con.close()
    if not row:
        raise CatalogError(f"{working} records no data path")
    return row[0]


def last_snapshot(working: Path) -> int:
    """The newest snapshot of a working catalog."""
    import sqlite3

    con = sqlite3.connect(f"file:{working}?mode=ro", uri=True)
    try:
        return con.execute("SELECT max(snapshot_id) FROM ducklake_snapshot").fetchone()[0]
    except sqlite3.Error as exc:
        raise CatalogError(f"{working} is not a working catalog: {exc}") from exc
    finally:
        con.close()


def same_contents(working: Path, since: int, *, data_path: str | None = None) -> bool:
    """Whether the working catalog's last snapshot holds what snapshot ``since`` held.

    ``data_path`` is where the working catalog's new data files are, when not
    where it records (a working catalog of a repository over HTTP writes them
    to a folder on this machine).

    The same tables and views, each table with the same columns and the same
    rows, in any order. A table rebuilt from the same input is written to new
    files, so files are compared first and rows only where the files differ.
    Reading those rows needs the data files: one the writer cannot read raises
    `CatalogError`.
    """
    import duckdb

    working = Path(working)
    last = last_snapshot(working)
    if last == since:
        return True
    con = duckdb.connect()
    try:
        for statement in attach_sql(
            working,
            alias="lake",
            data_path=data_path or working_data_path(working),
            read_only=True,
        ):
            if statement:
                con.execute(statement)
        meta = "__ducklake_metadata_lake.main"

        def live(alias: str) -> str:
            return f"{alias}.begin_snapshot <= $s AND ({alias}.end_snapshot IS NULL OR {alias}.end_snapshot > $s)"

        def tables(snapshot: int) -> dict[tuple[str, str], int]:
            rows = con.execute(
                f"""
                SELECT s.schema_name, t.table_name, t.table_id
                FROM {meta}.ducklake_table t JOIN {meta}.ducklake_schema s USING (schema_id)
                WHERE {live('t')} AND {live('s')}
                """,
                {"s": snapshot},
            ).fetchall()
            return {(schema, name): table_id for schema, name, table_id in rows}

        def views(snapshot: int) -> set[tuple]:
            return set(
                con.execute(
                    f"""
                    SELECT s.schema_name, v.view_name, v.dialect, v.sql
                    FROM {meta}.ducklake_view v JOIN {meta}.ducklake_schema s USING (schema_id)
                    WHERE {live('v')} AND {live('s')}
                    """,
                    {"s": snapshot},
                ).fetchall()
            )

        def files(table_id: int, snapshot: int) -> set[tuple]:
            return set(
                con.execute(
                    f"""
                    SELECT d.data_file_id, x.delete_file_id
                    FROM {meta}.ducklake_data_file d
                    LEFT JOIN {meta}.ducklake_delete_file x
                      ON x.data_file_id = d.data_file_id AND {live('x')}
                    WHERE d.table_id = $t AND {live('d')}
                    """,
                    {"s": snapshot, "t": table_id},
                ).fetchall()
            )

        def shape(name: str, snapshot: int) -> list[tuple]:
            return con.execute(
                f"SELECT column_name, column_type FROM "
                f"(DESCRIBE SELECT * FROM {name} AT (VERSION => {int(snapshot)}))"
            ).fetchall()

        def same_rows(name: str) -> bool:
            # Compared as values, not hashes: DuckDB's hash() gives a NULL
            # list, struct or map the hash of an empty one, so a sum of row
            # hashes misses rows that trade the two. With the counts equal,
            # nothing left by EXCEPT ALL means the same multiset.
            old = f"SELECT * FROM {name} AT (VERSION => {int(since)})"
            new = f"SELECT * FROM {name} AT (VERSION => {int(last)})"
            counts = con.execute(
                f"SELECT (SELECT count(*) FROM ({old})), (SELECT count(*) FROM ({new}))"
            ).fetchone()
            if counts[0] != counts[1]:
                return False
            return bool(con.execute(f"SELECT NOT EXISTS ({old} EXCEPT ALL {new})").fetchone()[0])

        def quoted(identifier: str) -> str:
            return '"' + identifier.replace('"', '""') + '"'

        before, after = tables(since), tables(last)
        if before.keys() != after.keys() or views(since) != views(last):
            return False
        for (schema, table), table_id in after.items():
            name = f"lake.{quoted(schema)}.{quoted(table)}"
            if shape(name, since) != shape(name, last):
                return False
            if before[(schema, table)] == table_id and files(table_id, since) == files(
                table_id, last
            ):
                continue
            if not same_rows(name):
                return False
        return True
    except CatalogError:
        raise
    except Exception as exc:  # noqa: BLE001 - duckdb raises several types
        raise CatalogError(f"could not compare {working} with snapshot {since}: {exc}") from exc
    finally:
        con.close()


def restore(
    working: Path, snapshot: int, *, data_path: str | None = None, setup: Iterable[str] = ()
) -> list[str]:
    """Make the working catalog hold, in a new snapshot, what ``snapshot`` held.

    The tables and views ``snapshot`` held come back as they were there, and
    the others are dropped. A table unchanged since ``snapshot`` is left as it
    is; any other is written again from its rows at ``snapshot``, so its data
    files are new ones and those of the later versions stay in the repository.
    Returns the tables and views it changed, as ``schema.name``.

    ``snapshot`` must still be in the catalog: one that was expired raises
    `CatalogError`. ``setup`` runs first, for the keys reading the files needs.
    """
    import duckdb

    working = Path(working)
    if not has_snapshot(working, snapshot):
        raise CatalogError(f"snapshot {snapshot} is no longer in {working}")
    last = last_snapshot(working)
    con = duckdb.connect()
    try:
        for statement in [*setup, *attach_sql(
            working, alias="lake", data_path=data_path or working_data_path(working)
        )]:
            if statement:
                con.execute(statement)
        # A view's SQL names its tables as the writer did, from inside the catalog.
        con.execute("USE lake")
        meta = "__ducklake_metadata_lake.main"

        def live(alias: str) -> str:
            return f"{alias}.begin_snapshot <= $s AND ({alias}.end_snapshot IS NULL OR {alias}.end_snapshot > $s)"

        def tables(at: int) -> dict[tuple[str, str], int]:
            rows = con.execute(
                f"""
                SELECT s.schema_name, t.table_name, t.table_id
                FROM {meta}.ducklake_table t JOIN {meta}.ducklake_schema s USING (schema_id)
                WHERE {live('t')} AND {live('s')}
                """,
                {"s": at},
            ).fetchall()
            return {(schema, name): table_id for schema, name, table_id in rows}

        def views(at: int) -> dict[tuple[str, str], str]:
            rows = con.execute(
                f"""
                SELECT s.schema_name, v.view_name, v.sql
                FROM {meta}.ducklake_view v JOIN {meta}.ducklake_schema s USING (schema_id)
                WHERE {live('v')} AND {live('s')}
                """,
                {"s": at},
            ).fetchall()
            return {(schema, name): sql for schema, name, sql in rows}

        def schemas(at: int) -> set[str]:
            rows = con.execute(
                f"SELECT s.schema_name FROM {meta}.ducklake_schema s WHERE {live('s')}", {"s": at}
            ).fetchall()
            return {schema for (schema,) in rows}

        def columns(table_id: int) -> list[tuple[str, str | None, bool]]:
            """Top-level columns at ``snapshot``: name, comment, whether nulls are allowed."""
            return con.execute(
                f"""
                SELECT c.column_name, g.value, c.nulls_allowed
                FROM {meta}.ducklake_column c
                LEFT JOIN {meta}.ducklake_column_tag g
                  ON g.table_id = c.table_id AND g.column_id = c.column_id AND g.key = 'comment'
                  AND {live('g')}
                WHERE c.table_id = $t AND c.parent_column IS NULL AND {live('c')}
                ORDER BY c.column_order
                """,
                {"s": snapshot, "t": table_id},
            ).fetchall()

        def table_comment(table_id: int) -> str | None:
            row = con.execute(
                f"SELECT value FROM {meta}.ducklake_tag g WHERE object_id = $t AND key = 'comment' AND {live('g')}",
                {"s": snapshot, "t": table_id},
            ).fetchone()
            return row[0] if row else None

        def changed_since(table_id: int) -> bool:
            # Anything about the table that began or ended after ``snapshot``.
            later = "(begin_snapshot > $s OR end_snapshot > $s)"
            return any(
                con.execute(
                    f"SELECT count(*) FROM {meta}.{kind} WHERE table_id = $t AND {later}",
                    {"s": snapshot, "t": table_id},
                ).fetchone()[0]
                for kind in ("ducklake_data_file", "ducklake_delete_file", "ducklake_column")
            )

        def quoted(identifier: str) -> str:
            return '"' + identifier.replace('"', '""') + '"'

        def literal(text: str) -> str:
            return "'" + text.replace("'", "''") + "'"

        def name(key: tuple[str, str]) -> str:
            return f"lake.{quoted(key[0])}.{quoted(key[1])}"

        old_tables, new_tables = tables(snapshot), tables(last)
        old_views, new_views = views(snapshot), views(last)
        changed: list[str] = []
        con.execute("BEGIN")
        for key in new_views.keys() - old_views.keys():
            con.execute(f"DROP VIEW {name(key)}")
            changed.append(".".join(key))
        for key in new_tables.keys() - old_tables.keys():
            con.execute(f"DROP TABLE {name(key)}")
            changed.append(".".join(key))
        for schema in schemas(last) - schemas(snapshot):
            con.execute(f"DROP SCHEMA lake.{quoted(schema)} CASCADE")
            changed.append(schema)
        for schema in schemas(snapshot):
            con.execute(f"CREATE SCHEMA IF NOT EXISTS lake.{quoted(schema)}")
        for key, table_id in old_tables.items():
            if new_tables.get(key) == table_id and not changed_since(table_id):
                continue
            at = f"{name(key)} AT (VERSION => {int(snapshot)})"
            declared = columns(table_id)
            types = dict(con.execute(f"SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM {at})").fetchall())
            # Declared as the columns were, NOT NULL included, then filled:
            # within the transaction AT still names the table as it was.
            body = ", ".join(
                f"{quoted(column)} {types[column]}" + ("" if nulls else " NOT NULL")
                for column, _, nulls in declared
            )
            # Filled beside the table, then put in its place.
            staging = name((key[0], "__fdl_restore"))
            con.execute(f"CREATE TABLE {staging} ({body})")
            con.execute(f"INSERT INTO {staging} SELECT * FROM {at}")
            if key in new_tables:
                con.execute(f"DROP TABLE {name(key)}")
            con.execute(f"ALTER TABLE {staging} RENAME TO {quoted(key[1])}")
            for column, comment, _ in declared:
                if comment is not None:
                    con.execute(f"COMMENT ON COLUMN {name(key)}.{quoted(column)} IS {literal(comment)}")
            comment = table_comment(table_id)
            if comment is not None:
                con.execute(f"COMMENT ON TABLE {name(key)} IS {literal(comment)}")
            changed.append(".".join(key))
        for key, sql in old_views.items():
            if new_views.get(key) == sql:
                continue
            # DuckLake records the catalog's own name as a placeholder.
            body = sql.replace("{DUCKLAKE_CATALOG}", "lake")
            con.execute(f"CREATE OR REPLACE VIEW {name(key)} AS {body}")
            changed.append(".".join(key))
        con.execute("COMMIT")
        return sorted(changed)
    except CatalogError:
        raise
    except Exception as exc:  # noqa: BLE001 - duckdb raises several types
        raise CatalogError(f"could not restore snapshot {snapshot} in {working}: {exc}") from exc
    finally:
        con.close()
