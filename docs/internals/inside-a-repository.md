---
title: Inside a repository
description: The files a repository holds after each step, from init to a tag and a retention run, and the order they are written in.
---

This page follows one repository through its life and shows which files appear at each step and in what order they are written. The rules behind each step are in the [specification](/spec); this page shows them at work.

## After init

```
fdl.json
HEAD
```

```json fdl.json
{
  "fdl": "0.1",
  "id": "5f0c8a52-6d3e-4f8e-9b1a-2c7d4e9f0a13",
  "data_dir": "data/"
}
```

```text HEAD
ref: refs/heads/main
```

`fdl.json` is written with `If-None-Match: *`, so running `init` twice cannot replace the `id` of a repository that already exists. `HEAD` points at `main`, which does not exist yet. A reader that follows `HEAD` now finds no version, and says so.

## While writing

After `fdl checkout` and a `CREATE TABLE`, DuckLake has written Parquet into the repository:

```
fdl.json
HEAD
data/main/towns/ducklake-0199f1….parquet
```

No ref and no version names the file, so no reader can reach it. If the writer gives up, the file is left behind and maintenance deletes it after the grace period.

## The first commit

`fdl commit` does its work in this order:

1. Copy the working catalog from SQLite into a DuckDB file, keeping the snapshots up to the last one
2. Set the catalog's `data_path` to the repository's data folder, and refuse if any file is recorded with an absolute path elsewhere
3. Check `dataset.json` against the catalog and fill in row counts and column types
4. Write `version.json` next to them, with a new UUIDv7 as the ID
5. Check that every data file the catalog names is in the repository
6. Upload `versions/<ID>/ducklake.duckdb`
7. Upload `versions/<ID>/dataset.json`
8. Upload `versions/<ID>/version.json`
9. Create `refs/heads/main` with `If-None-Match: *`

```
fdl.json
HEAD
refs/heads/main                                   019a2f10-…
versions/019a2f10-…/ducklake.duckdb
versions/019a2f10-…/dataset.json
versions/019a2f10-…/version.json
data/main/towns/ducklake-0199f1….parquet
```

The order is what keeps readers safe. A reader starts from a ref, and the ref is written last. If the commit stops after step 6, 7 or 8, the repository holds a version folder no ref points at. Readers never see it, and it changes nothing.

`version.json` is written after the catalog and `dataset.json` for the same reason: a tool that lists `versions/` can treat a folder without `version.json` as unfinished.

## The second commit

Bob checks out `main`, adds a column and commits:

```
refs/heads/main                                   019a3c4e-…
versions/019a2f10-…/…
versions/019a3c4e-…/ducklake.duckdb
versions/019a3c4e-…/dataset.json
versions/019a3c4e-…/version.json
data/main/towns/ducklake-0199f1….parquet          named by both versions
data/main/towns/ducklake-019a3b….parquet          named by the second only
```

```json versions/019a3c4e-…/version.json
{
  "fdl": "0.1",
  "id": "019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c",
  "seq": 2,
  "parents": ["019a2f10-0c4d-7e88-a1b2-55c0d9e3f7aa"],
  "created_at": "2026-10-15T09:00:00Z",
  "author": { "name": "bob" },
  "message": "Add a note column",
  "catalog": { "path": "ducklake.duckdb", "storage": "duckdb-v1.5.0", "bytes": 4194304, "sha256": "9f2c…" },
  "dataset": { "path": "dataset.json", "bytes": 2048, "sha256": "4c1d…" },
  "ducklake": { "format": "1.0", "snapshot_id": 12, "snapshots": [8, 12] }
}
```

`main` is moved with `If-Match: <the ETag read at the start of the push>`. Had Alice moved it in the meantime, the ETag would not match and the store would refuse the write. Bob's version folder would stay, unreferenced, and Bob would check out again.

The first Parquet file is not copied. Both catalogs name it at the same path, which is why data files must never be overwritten.

## A tag

```text refs/tags/2025-final
019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c
```

A tag is created with `If-None-Match: *` and never moved. Readers who attach `refs/tags/2025-final` get the same catalog for as long as the tag exists, whatever happens to `main`. A version a ref points at is never taken out of retention.

## Taking versions out of retention

Catalogs are small, but a dataset that is rebuilt every day keeps every Parquet file any version names. `fdl expire --keep 30` takes the versions behind the newest 30 out of retention. Each goes through three steps:

1. Put `versions/<ID>/expired.json`
2. Delete `versions/<ID>/ducklake.duckdb` and any `catalogs/`
3. After the grace period, delete data files that no live version names

```
versions/019a2f10-…/version.json
versions/019a2f10-…/expired.json      {"expired_at": "2026-12-01T00:00:00Z", "reason": "retention"}
```

`version.json` stays, so `fdl log` still lists the version and the graph of parents stays whole. A reader asked for an expired version sees `expired.json` and stops instead of fetching a catalog that is gone.

The mark is written first so that a reader never finds a version with no catalog and no explanation. Data files go last, and only after the grace period, because a writer may have placed a file and be about to commit a version that names it.

`fdl expire` does the first two steps. Deleting data files needs a process that can list the repository and read every live catalog, since one file can be named by many versions.
