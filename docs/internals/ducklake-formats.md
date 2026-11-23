---
title: DuckLake formats
description: The three format numbers a version carries, and how a writer on a newer DuckDB keeps readers on older releases able to read.
---

## Three format numbers

A version records three formats, each for a different thing. A reader can tell from `version.json` alone whether it can open a version, before it fetches the metadata.

| Field | Refers to | Changes when |
| --- | --- | --- |
| `fdl` | this specification: the layout and `version.json` | the specification changes |
| `ducklake.format` | the shape of DuckLake's bookkeeping tables (`1.0`, `1.1`, …) | DuckLake's specification changes |
| `metadata.storage` | the storage format of the DuckDB file that holds the metadata | DuckDB's storage format changes |

The last two move independently. DuckDB 2.0 writes DuckLake format 1.1 by default, and its own files in a storage format DuckDB 1.5 cannot open. A metadata file made with DuckDB 2.0 defaults is unreadable to DuckDB 1.5 on both counts.

## Why it matters for published data

Readers update DuckDB on their own schedule, and some cannot choose: a browser runs whatever DuckDB WASM build the page ships. A dataset that switches to a new format cuts those readers off.

Data files are not the problem. A new DuckLake format changes the bookkeeping tables, not the Parquet. A dataset keeps its readers by writing its metadata in a format they can open, and moves to the new one when they have caught up.

## Writing an older format from DuckDB 2.0

DuckDB 2.0 can write both older formats. Two settings are needed, one for each format number:

```sql
-- the DuckLake format: create the metadata as 1.0
ATTACH 'ducklake:work/lake.sqlite' AS lake (DUCKLAKE_VERSION '1.0', …);

-- the storage format: freeze into a file DuckDB 1.5 can open
ATTACH 'frozen.duckdb' AS dst (STORAGE_VERSION 'v1.5.0');
COPY FROM DATABASE src TO dst;
```

`SET ducklake_default_version = '1.0'` does the same as `DUCKLAKE_VERSION` for every metadata file the session creates. An existing format 1.0 metadata stays at 1.0 when DuckDB 2.0 writes to it.

A metadata file made this way opens in DuckDB 1.5 and returns the same rows at every snapshot as it does in DuckDB 2.0.

## Rules for writers

- Write the metadata and the data files in a format the dataset's readers can open. A feature only a newer format can describe (a new column type, say) cuts off readers on older releases
- Record `metadata.storage` as the oldest storage format the file can be opened with
