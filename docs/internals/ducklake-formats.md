---
title: DuckLake formats
description: The three format numbers a version carries, and how a repository serves readers on older DuckDB releases when DuckLake's format moves.
---

## Three format numbers

A version records three formats, each for a different thing. A reader can tell from `version.json` alone whether it can open a version, before it fetches the catalog.

| Field | Refers to | Changes when |
| --- | --- | --- |
| `fdl` | this specification: the layout and `version.json` | the specification changes |
| `ducklake.format` | the shape of DuckLake's bookkeeping tables (`1.0`, `1.1`, …) | DuckLake's specification changes |
| `catalog.storage` | the storage format of the DuckDB file that holds the catalog | DuckDB's storage format changes |

The last two move independently. DuckDB 2.0 writes DuckLake format 1.1 by default, and its own files in a storage format DuckDB 1.5 cannot open. A catalog made with DuckDB 2.0 defaults is unreadable to DuckDB 1.5 on both counts.

## Why it matters for published data

Readers update DuckDB on their own schedule, and some cannot choose: a browser runs whatever DuckDB WASM build the page ships. A repository that switches to a new format cuts those readers off.

Data files are not the problem. A new DuckLake format changes the bookkeeping tables, not the Parquet. So a version can carry one catalog per format, all naming the same data files, and a reader picks the one it can open.

```
versions/<ID>/
  ducklake.duckdb            the catalog as made (the one version.json describes)
  catalogs/1.0.duckdb        the same version in format 1.0
  catalogs/1.1.duckdb        the same version in format 1.1
```

`fdl.json` declares which formats are published and until when:

```json
"catalog_formats": [
  { "ducklake": "1.1" },
  { "ducklake": "1.0", "until": "2027-03-31" }
]
```

A reader tries the formats it can open, newest first, then falls back to `ducklake.duckdb`. After `until`, new versions are no longer written in that format; past versions keep theirs.

## Writing an older format from DuckDB 2.0

DuckDB 2.0 can write both older formats. Two settings are needed, one for each format number:

```sql
-- the DuckLake format: create the catalog as 1.0
ATTACH 'ducklake:work/lake.sqlite' AS lake (DUCKLAKE_VERSION '1.0', …);

-- the storage format: freeze into a file DuckDB 1.5 can open
ATTACH 'frozen.duckdb' AS dst (STORAGE_VERSION 'v1.5.0');
COPY FROM DATABASE src TO dst;
```

`SET ducklake_default_version = '1.0'` does the same as `DUCKLAKE_VERSION` for every catalog the session creates. An existing format 1.0 catalog stays at 1.0 when DuckDB 2.0 writes to it.

A catalog made this way opens in DuckDB 1.5 and returns the same rows at every snapshot as it does in DuckDB 2.0.

## Rules for writers

- Write data files so that the oldest declared format can read them. A feature only a newer format can describe (a new column type, say) means that version has no catalog in the older format
- Every `catalogs/<format>.duckdb` of a version names the same data files and returns the same rows
- Record `catalog.storage` as the oldest storage format the file can be opened with
