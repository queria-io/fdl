---
title: Working catalogs
description: "Where writing happens: a DuckLake catalog in SQLite, checked out from a version and frozen into the next one."
---

A working catalog is a DuckLake catalog in a SQLite file, on the writer's own disk or in a container. It is the only place anyone writes to. It is to FDL what a working tree is to Git: made from a version, changed freely, and turned into the next version when the change is ready.

## The cycle

| Step | Command | What happens |
| --- | --- | --- |
| check out | `fdl checkout` | Copy the catalog of the branch's version into SQLite, and remember that version |
| write | any DuckDB client | DuckLake writes Parquet into the repository and bookkeeping into SQLite |
| commit | `fdl commit` | Freeze the last snapshot into a new version, kept beside the working catalog |
| push | `fdl push` | Upload the version and move the branch to it |

Checking out copies DuckLake's bookkeeping tables from the frozen DuckDB file into SQLite as they are. Freezing does the reverse. Neither rewrites a Parquet file, so both are fast even for a large dataset: the catalog is small and the data stays where it is.

## Why SQLite

A working catalog has to be written by DuckDB and kept between sessions. SQLite does both without a database server, works the same on a laptop and in a container, and copies in and out of DuckDB's format without loss.

There is one kind of working catalog. A host that writes on behalf of its users (a scheduled import, a web form) checks out, writes, commits and pushes the same way a person does on a laptop. When two of them push at once, the conditional write on the ref settles it: one wins, and the other checks out again.

## Attaching

`fdl checkout` prints the statement to attach the working catalog with:

```sql
ATTACH 'ducklake:work/lake.sqlite' AS lake (
  DATA_PATH 's3://my-bucket/towns/data/', OVERRIDE_DATA_PATH true,
  META_TYPE 'sqlite', DATA_INLINING_ROW_LIMIT 0,
  META_JOURNAL_MODE 'WAL', BUSY_TIMEOUT 5000
);
```

- `DATA_PATH` is the repository's data folder, so new Parquet files land where readers will look for them. For a repository over HTTP it is a folder beside the working catalog instead, and `fdl push` uploads the new files (see [Pushing over HTTP](/spec/http#writing-to-it))
- `DATA_INLINING_ROW_LIMIT 0` keeps every row out of the catalog. A version must not carry inlined rows, and `fdl commit` refuses a working catalog that holds some
- `META_TYPE 'sqlite'` has to be stated. Left to guess from the file, DuckDB passes an option to SQLite that SQLite rejects

## Data files written before a push

DuckLake writes Parquet as you go, so data files reach the repository before any version names them. Readers never see them: a reader only opens catalogs of versions, and no version names those files yet.

A file that stays unnamed for longer than the grace period (7 days by default) may be deleted by maintenance. Push within that time. If a push is refused because a file it names is missing, rebuild the table in the working catalog, commit and push again. A version committed and never pushed names its files only on your disk, so they are deleted in the same way.

## Snapshots in the working catalog

Do not run `ducklake_expire_snapshots` or `ducklake_cleanup_old_files` on a working catalog. They only see the working catalog, not the repository, and would delete files that other versions still name. Removing old versions and files is done on the repository as a whole (see [Retention](/internals/inside-a-repository#taking-versions-out-of-retention)).
