---
title: Reading
description: How a reader resolves a ref, checks a version and attaches its catalog, and what may be cached.
---

## Steps

1. Read `HEAD`, or the ref asked for. If the content starts with `ref: `, read the ref it names
2. Read `versions/<ID>/version.json` for the ID found. Check that its `id` matches and that the reader knows its `fdl` major version
3. If `versions/<ID>/expired.json` exists, stop: the version is past retention
4. Open `versions/<ID>/ducklake.duckdb`, or a `catalogs/` file the reader can open, read-only

```sql
ATTACH 'ducklake:https://example.org/lake/versions/019a3c4e-…/ducklake.duckdb' AS lake (
  DATA_PATH 'https://example.org/lake/data/', OVERRIDE_DATA_PATH true, READ_ONLY
);
```

## Data path

The catalog's `data_path` is the absolute URL of the data folder where the writer published. A reader must pass `DATA_PATH '<root><data_dir>'` and `OVERRIDE_DATA_PATH true`, where `<root>` is wherever it reads the repository from. The statement is then the same at the origin and at a mirror, and a repository copied to another host is read without changing its catalogs.

## Caching

- A version's files never change except as described in [Versions](/spec/versions), so its catalog may be cached forever
- `HEAD` and `refs/` must not be cached
- `expired.json` may appear later. A cache of a version folder should be dropped when it does

Over HTTP, `HEAD` and `refs/` are served as `Content-Type: text/plain; charset=utf-8` with `Cache-Control: no-store`.

## A catalog at a fixed URL

A host may serve the catalog of the version `HEAD` holds at `<root>/ducklake.duckdb`, for readers that do not know FDL. It must then send the version ID in an `FDL-Version` response header. This catalog is served, not stored: the store holds no catalog outside `versions/`.
