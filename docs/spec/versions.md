---
title: Versions
description: The version ID, the fields of version.json, the frozen catalog, and catalogs in other DuckLake formats.
---

A version is the files under `versions/<version ID>/`. They must not change once written, except:

- `expired.json` is added and the catalogs deleted when the version falls out of retention ([Data files](/spec/data-files#past-retention))
- on withdrawal, `expired.json` is added with `"reason": "withdrawn"` and the catalogs deleted, and `version.json` itself is deleted if it holds the withdrawn data ([Data files](/spec/data-files#withdrawal))
- `catalogs/<format>.duckdb` may be added later. Files already there are not changed
- `version.json.sig` may be added later, by a writer that signs a version after making it

## Version ID

- A UUIDv7 in lower case with hyphens, for example `019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c`. IDs sort by creation time, and writers do not collide without coordinating
- Not a content hash. The same catalog can be written as different bytes, so a hash would not be stable. Content is checked with `catalog.sha256`
- The number shown to people is `seq`. It is not unique across branches, so it is not the ID

## version.json

```json
{
  "fdl": "0.1",
  "id": "019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c",
  "seq": 12,
  "parents": ["019a2f10-0c4d-7e88-a1b2-55c0d9e3f7aa"],
  "created_at": "2026-10-30T09:00:00Z",
  "author": { "name": "example" },
  "message": "Add the final 2025 figures",
  "catalog": {
    "path": "ducklake.duckdb",
    "storage": "duckdb-v1.5.0",
    "bytes": 4194304,
    "sha256": "9f2c…"
  },
  "dataset": {
    "path": "dataset.json",
    "bytes": 2048,
    "sha256": "4c1d…"
  },
  "ducklake": {
    "format": "1.0",
    "snapshot_id": 345,
    "snapshots": [301, 345]
  },
  "files": {
    "path": "files.json",
    "bytes": 1536,
    "sha256": "7a0e…"
  },
  "changes": [
    { "table": "main.population", "kind": "add_column", "column": "note", "breaking": false }
  ],
  "inputs": [
    {
      "source_id": "example:population-2025",
      "sha256": "1b7e…",
      "retrieved_at": "2026-10-29T03:12:00Z",
      "url": "https://example.org/population-2025.csv"
    }
  ],
  "extensions": {}
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `fdl` | must | The specification version it was written for |
| `id` | must | The same ID as the directory. A reader must not read a version where they differ |
| `seq` | must | The number shown to people: 1 for the first, the first parent's `seq` plus 1 after |
| `parents` | must | Parent version IDs. Empty for the first version, one after |
| `created_at` | must | UTC, ISO 8601 |
| `author` | must | Who made it. `name` is required; `email` and others are optional |
| `message` | must | What changed |
| `catalog` | must | The catalog's file name, size, sha256, and storage format (`storage`) |
| `dataset` | must | The `dataset.json` beside the catalog: its file name, size and sha256. Versions written before it was required lack it |
| `ducklake` | must | The DuckLake format, the snapshot frozen, and the range of snapshots this version adds after its parent (first and last) |
| `files` | may | The `files.json` beside the catalog, if written: its file name, size and sha256 ([files.json](#filesjson)) |
| `changes` | may | Column differences from the parent. `breaking` says whether SQL written for the parent may fail (a column dropped, renamed or recreated) |
| `inputs` | may | Source files used. How `source_id` is assigned is up to the writer |
| `extensions` | may | Host-specific fields, keyed by the host's name |

A reader must ignore fields it does not know.

In FDL 0.1 a writer writes at most one parent. `parents` is a list so that a later version of this specification can describe merges without changing the field; a reader must accept a list of any length and follow the first entry for history.

The description and SQL of each snapshot stay in the catalog, in DuckLake's `commit_message` and `commit_extra_info`. They are not copied into `version.json`.

### Format numbers

| Field | Refers to | Changes when |
| --- | --- | --- |
| `fdl` | this specification | this specification changes |
| `ducklake.format` | the shape of DuckLake's bookkeeping tables | DuckLake's specification changes |
| `catalog.storage` | the storage format of the catalog file | DuckDB's storage format changes |

A reader can tell from `version.json` alone whether it can open a version.

## files.json

`files.json` is an index of each table's data files at `ducklake.snapshot_id`, for readers that cannot open the catalog. The writer makes it from the catalog, and the catalog wins where the two differ. It is optional: a reader must work without it. It plays the part of Git's reachability bitmap (`.bitmap` beside a pack): a list, made from the objects, of what one commit reaches, which speeds up reading and which a reader can do without.

```json files.json
{
  "tables": {
    "main.population": {
      "files": [
        {
          "path": "main/population/ducklake-019a3c4e-….parquet",
          "rows": 1741,
          "bytes": 52311,
          "footer_bytes": 2048,
          "deletes": { "path": "main/population/ducklake-019a3c4f-…-delete.parquet", "rows": 3, "bytes": 812 }
        }
      ]
    }
  }
}
```

- `tables` holds every table alive at the snapshot, keyed `schema.table`. A table with no rows has an empty `files`
- `path` is relative to the repository's `data_dir`. Files are in the catalog's order
- `rows` and `bytes` are the row count and size the catalog records. `footer_bytes` is the size of the Parquet footer, when recorded
- `deletes` is the delete file for that data file, if rows were deleted from it
- `renamed_columns` is `true` when a column of the table was ever renamed. Data files written before the rename carry the old name

A reader that reads the Parquet files without DuckLake must not read a table whose files have `deletes`, or whose `renamed_columns` is `true`, unless it applies them as DuckLake does. Otherwise it returns deleted rows or misses renamed columns.

When a version is taken out of retention, `files.json` is deleted with its catalogs.

## The frozen catalog

`ducklake.duckdb` is a DuckLake catalog in DuckDB's file format.

- It holds bookkeeping rows up to `ducklake.snapshot_id` and none after
- It holds no inlined rows. All data is in data files
- Every data file it names is under `data_dir`, recorded relative to the catalog's data path. A writer must check this; a host may check it too
- Its `data_path` is the absolute URL of the data folder where the writer published: the root plus `data_dir`. Readers override it (see [Reading](/spec/reading))
- Until DuckLake can tag snapshots, a writer must not run `ducklake_expire_snapshots` on its working catalog in a way that removes the snapshot a version was frozen at

## dataset.json

What a version holds, in words: the dataset, its tables and their columns. It is part of the version, written once with it, so a reader can tell what a version holds and under what terms without opening the catalog, and an old version keeps the description it was published with.

The writer describes; the writer's tool checks the description against the frozen catalog and completes it from there.

```json
{
  "name": "population",
  "title": "Population by town",
  "description": "Census population of each town, by age and sex",
  "keywords": ["population", "census"],
  "language": "en",
  "homepage": "https://example.org/population",
  "issued": "2026-10-30",
  "update_frequency": "yearly",
  "licenses": [{ "id": "CC-BY-4.0", "spdx": "CC-BY-4.0", "url": "https://creativecommons.org/licenses/by/4.0/" }],
  "sources": [{ "title": "2025 census", "path": "https://example.org/census-2025" }],
  "contributors": [{ "title": "Example City", "roles": ["publisher"] }],
  "temporal_coverage": { "start": "2025", "end": "2025" },
  "spatial_coverage": ["JP"],
  "schemas": [{ "name": "main", "title": "Tables" }],
  "tables": [
    {
      "schema": "main",
      "name": "population",
      "title": "Population",
      "description": "One row per town, age group and sex",
      "primary_key": ["town_code", "age_group", "sex"],
      "depends_on": [],
      "sql": "SELECT …",
      "rows": 120000,
      "fields": [
        { "name": "town_code", "title": "Town code", "description": "…", "type": "varchar", "nullable": false }
      ]
    }
  ],
  "extensions": {}
}
```

- `name` and `title` are required. Everything else may be left out
- A writer may keep its description as YAML (`dataset.yml`), and may keep each table in its own file (`<name>.table.yml`, one entry of `tables`). The version always holds the whole description as one JSON
- Each of `licenses` is an SPDX identifier, or an object with `id` and what the writer wants to add (`spdx`, `url`, `title`)
- `tables` lists every table of the catalog's last snapshot. The writer's tool adds the tables the writer did not describe, with what the catalog knows of them, and refuses a described table or column the catalog lacks
- A view of the catalog is listed after the tables with `"kind": "view"` and its `sql` from the catalog. The catalog records no columns for a view, so its `fields` are the writer's, without `type`, `nullable` or `rows`. `kind` is left out for a table
- From the catalog: each table's `rows`, and each field's `type` (DuckLake's type name) and `nullable`. Comments in the catalog (`COMMENT ON`) are used where the writer gave no `description`
- Keys this specification does not define are refused. A host's or a writer's own fields go under `extensions`, keyed by their name, at the top, in a table or in a field
- What a license or a source says is the writer's claim. A host may check it before it publishes

## Catalogs in other formats

One version may carry a catalog per DuckLake format, so that a reader picks the file it can open instead of switching DuckDB.

```
versions/<ID>/
  ducklake.duckdb            the catalog as made (the one version.json describes)
  catalogs/1.0.duckdb        written out in format 1.0
  catalogs/1.1.duckdb        written out in format 1.1
```

- Every `catalogs/<format>.duckdb` must name the same data files and return the same rows
- A reader tries the formats it can open, newest first, then falls back to `ducklake.duckdb`
- A writer that cannot produce a declared format for a version leaves that file out. A reader that can open none of the files of a version cannot read it
- A version that uses something an older format cannot express has no catalog in that format
- A format past its `until` in `fdl.json` is not written for new versions. Past versions may keep theirs
- Data files must be written so that the oldest format declared in `fdl.json` can read them
- `catalog.storage` should be the oldest DuckDB storage format the declared readers can open

[DuckLake formats](/internals/ducklake-formats) shows how to write an older format from DuckDB 2.0.
