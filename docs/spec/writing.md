---
title: Writing
description: Checkout, commit and push - the working catalog, the order of writes, and when a push is refused.
---

## Working catalog

A writer writes to a working catalog: a DuckLake catalog in SQLite, outside the repository. There is one kind of working catalog, whether it is on a laptop or in a host's container.

- It must be attached with `DATA_INLINING_ROW_LIMIT 0`, so that every row goes to a data file
- Its data path is the repository's data folder (`<root><data_dir>`), so new data files land in the repository
- `ducklake_expire_snapshots` and `ducklake_cleanup_old_files` must not be run on it

## Steps

| Step | What happens |
| --- | --- |
| checkout | Make a working catalog from the catalog of the branch's version, and remember that version. Before the first version, make an empty one |
| commit | Freeze the working catalog's last snapshot into a new version, with the remembered version as its parent, and describe it in `dataset.json` |
| push | Check the data files, upload the catalog, `dataset.json`, then `version.json`, then move the ref on condition |

Checking out copies DuckLake's bookkeeping tables from the frozen catalog into SQLite as they are. Freezing does the reverse, keeping the rows up to the last snapshot. Neither rewrites a data file.

When freezing, a writer must:

- refuse a working catalog that holds inlined rows
- set the frozen catalog's `data_path` to the absolute URL of `<root><data_dir>`
- refuse if any data file is recorded with an absolute path, or outside `data_dir`

## Order of writes

1. Data files
2. `versions/<ID>/ducklake.duckdb` (and any `catalogs/`)
3. `versions/<ID>/dataset.json`
4. `versions/<ID>/version.json`
5. The ref, on condition

A writer stopped part way has changed nothing a reader sees. The files it left are not named by any ref, and data files among them are deleted after the grace period.

Files of a version are written once. A writer that finds one already there with the same content goes on; with different content, it stops.

## When a push is refused

A push from a stale working catalog is refused on two checks:

1. The version the branch holds now is not among the new version's `parents`. Someone else pushed first
2. A data file the catalog names is missing from the repository. A file written and left unnamed longer than the grace period may have been deleted

In both cases the writer checks out again, redoes the work and pushes again. Nothing in the repository needs to be cleaned up: the refused version is not named by any ref.
