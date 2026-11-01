---
title: Data files
description: Rules for data files and source files, when a data file may be deleted, and how a version is taken out of retention.
---

## Data files

- Named as DuckLake names them: `<data_dir><schema>/<table>/ducklake-<uuid>.parquet`. Delete files go in the same place
- Never overwritten. Several versions may name the same file
- A writer may place data files before making a version. Until a version names them, readers do not see them
- Compacting small files (`ducklake_merge_adjacent_files`) is allowed at any time: it writes new files and deletes none

## When a data file may be deleted

Only when both hold:

1. No catalog of a live version names it. A live version is one without `expired.json`
2. It was placed longer ago than the grace period. The grace period protects files a writer has placed but not yet made a version of

DuckLake's own cleanup (`ducklake_cleanup_old_files`, `ducklake_expire_snapshots`) sees only one catalog, so it must not be used to delete from a repository. Deleting is for a process that sees the whole repository.

## Past retention

A version is taken out of retention in this order:

1. Put `versions/<ID>/expired.json`
2. Delete the version's catalogs: `ducklake.duckdb` and `catalogs/`
3. After the grace period, delete the data files that meet the conditions above

```json expired.json
{ "expired_at": "2026-12-01T00:00:00Z", "reason": "retention" }
```

- `version.json` stays, so the history still says the version was there and what it was, and the graph of parents stays whole
- A reader that sees `expired.json` treats the version as past retention and does not open its catalog
- A version a ref points at must not be taken out

## Source files

- `sources/<sha256>`, named by the lower-case hex sha256 of the content. The same content is stored once
- Reached from the `sha256` in a version's `inputs`
- Whether to store them, and who may read them, is the host's decision. They may be someone else's copyrighted work
