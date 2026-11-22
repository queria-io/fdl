---
title: Versions
description: The version ID, the fields of version.json, the frozen metadata, and metadata in other DuckLake formats.
---

A version is the files under `versions/<version ID>/`. They must not change once written, except:

- `expired.json` is added and the metadata files deleted when the version falls out of retention ([Data files](/spec/data-files#past-retention))
- on withdrawal, `expired.json` is added with `"reason": "withdrawn"` and the metadata files deleted, and `version.json` is rewritten without the withdrawn data if it holds it ([Data files](/spec/data-files#withdrawal))
- `metadata/<format>.db` may be added later. Files already there are not changed
- `version.json.sig` may be added later, by a writer that signs a version after making it
- `profiles/<schema>/<table>.json` may be added later for a table that has none. Profiles already there are not changed

## Version ID

- A UUIDv7 in lower case with hyphens, for example `019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c`. IDs sort by creation time, and writers do not collide without coordinating
- Not a content hash. The same metadata can be written as different bytes, so a hash would not be stable. Content is checked with `metadata.sha256`
- The number shown to people is `seq`. It is not unique across branches, so it is not the ID
- A reader must refuse an ID in any other form, whether it comes from a ref, a folder name or `parents`. The ID becomes part of a path

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
  "metadata": {
    "path": "metadata.db",
    "storage": "duckdb-v1.5.0",
    "bytes": 4194304,
    "sha256": "9f2c…"
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
  "diff": {
    "tables": { "added": [], "dropped": [] },
    "rows": [{ "table": "main.population", "from": 1741, "to": 1802 }],
    "columns": [
      { "table": "main.population", "kind": "add_column", "column": "note", "breaking": false }
    ],
    "data": ["main.population"]
  },
  "annotations": {}
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `fdl` | must | The specification version it was written for |
| `id` | must | The same ID as the directory. A reader must not read a version where they differ |
| `seq` | must | The number shown to people: 1 for the first, the first parent's `seq` plus 1 after |
| `parents` | must | Parent version IDs. Empty for the first version, one after, two for a merge |
| `created_at` | must | UTC, ISO 8601 |
| `author` | must | Who made it. `name` is required; `email` and others are optional |
| `message` | must | What changed |
| `metadata` | must | The metadata's file name (`metadata.db`), size, sha256, and storage format (`storage`) |
| `ducklake` | must | The DuckLake format, the snapshot frozen, and the range of snapshots the working metadata made for this version after its parent (first and last). Only the snapshot frozen is in the metadata |
| `files` | may | The `files.json` beside the metadata, if written: its file name, size and sha256 ([files.json](#filesjson)) |
| `diff` | may | What changed since the first parent, or in the first version ([diff](#diff)) |
| `annotations` | may | What the format does not interpret, keyed by the name of whoever reads it (a host, a tool), such as the source files a version was made from |

A reader must ignore fields it does not know. Versions written by fdl-cli 0.31 and earlier carry `inputs`, the source files used; a reader may show it and need not. Earlier versions carry `dataset` and `tree` ([Files of earlier versions](#files-of-earlier-versions)).

The `path` of `metadata` and `files`, and of `dataset` and `tree` in earlier versions, is a file name in the version's folder: it holds no `/` or `\` and is not `.` or `..`. A reader must refuse a record with any other.

A version has one parent, or two for a merge: the version of the branch it was made on, then the version merged into it (see [Merging](/spec/writing#merging)). The first version has none. A reader must accept a list of any length, follow the first entry for the history of a branch, and follow every entry to find whether one version descends from another.

The description and SQL of the snapshot frozen stay in the metadata, in DuckLake's `commit_message` and `commit_extra_info`. They are not copied into `version.json`.

### diff

`diff` lists what changed between the version and its first parent, so that a reader can show a history from `version.json` alone. In the first version every table is added. `table` is `schema.table` in every key.

| Key | Holds |
| --- | --- |
| `tables` | `added` and `dropped`: the tables only one of the two holds |
| `rows` | `table`, `from`, `to`: the tables both hold whose row count differs |
| `columns` | The column differences, below |
| `data` | The tables both hold whose data files differ, even with the same rows |
| `tags` | Written by earlier writers: the tables and views both hold whose [tags](#tags-of-earlier-versions) differ |

A writer compares the working metadata at the parent's snapshot with its last snapshot, before freezing. The working metadata is cloned from the parent's metadata, so it holds the parent's snapshot. The writer leaves `diff` out when it does not. Earlier versions carry `dataset` or `descriptions` in `diff`, which say whether `dataset.json` changed; a reader ignores them. A reader compares the two versions' `files.json` for a version without `diff`, or against a version other than its first parent.

Each entry of `columns` is one column difference in a table both hold. `column` is the column's name in the new version.

| `kind` | Also has | `breaking` |
| --- | --- | --- |
| `add_column` | | `false` |
| `drop_column` | | `true` |
| `rename_column` | `from`: the old name | `true` |
| `change_type` | `from_type`, `to_type`: DuckLake's type names | `false` when every value of the old type fits the new one (`int32` to `int64`, `float32` to `float64`), `true` otherwise |

A reader ignores a `kind` it does not know and still reads its `breaking`, and ignores keys of `diff` it does not know.

### Format numbers

| Field | Refers to | Changes when |
| --- | --- | --- |
| `fdl` | this specification | this specification changes |
| `ducklake.format` | the shape of DuckLake's bookkeeping tables | DuckLake's specification changes |
| `metadata.storage` | the storage format of the metadata file | DuckDB's storage format changes |

A reader can tell from `version.json` alone whether it can open a version.

## files.json

`files.json` is an index of each table's data files and each view's columns at `ducklake.snapshot_id`, for readers that cannot open the metadata. The writer makes it from the metadata, and the metadata wins where the two differ. It is optional: a reader must work without it. It plays the part of Git's reachability bitmap (`.bitmap` beside a pack): a list, made from the objects, of what one commit reaches, which speeds up reading and which a reader can do without.

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
      ],
      "columns": [
        { "id": 1, "name": "code", "type": "varchar", "nullable": false },
        { "id": 2, "name": "population", "type": "int64", "nullable": true, "default": "0" },
        {
          "id": 3, "name": "tags", "type": "list", "nullable": true,
          "children": [{ "id": 4, "name": "element", "type": "varchar", "nullable": true }]
        }
      ],
      "primary_key": ["code"]
    }
  },
  "views": {
    "main.population_by_prefecture": {
      "sql": "SELECT prefecture, sum(population) AS population FROM main.population GROUP BY prefecture",
      "columns": [
        { "name": "prefecture", "type": "VARCHAR" },
        { "name": "population", "type": "HUGEINT" }
      ]
    }
  },
  "catalogs": { "census": "https://example.org/census/" },
  "named": [
    "main/population/ducklake-019a3c4e-….parquet",
    "main/population/ducklake-019a3c4f-…-delete.parquet",
    "main/population/ducklake-0199f2a1-….parquet"
  ]
}
```

- `tables` holds every table alive at the snapshot, keyed `schema.table`. A table with no rows has an empty `files`
- `path` is relative to the repository's `data_dir`. Files are in the metadata's order
- `rows` and `bytes` are the row count and size the metadata records. `footer_bytes` is the size of the Parquet footer, when recorded
- `deletes` is the delete file for that data file, if rows were deleted from it
- `columns` lists the table's columns at the snapshot, in the metadata's order. `id` is DuckLake's column ID, which DuckLake writes as the `field_id` of the column in every Parquet data file; it stays the same when the column is renamed. `type` is DuckLake's type name, and `nullable` is `false` for a `NOT NULL` column. `default` is the value, as text, that rows of data files written before the column was added hold, when it is not NULL. A `struct` has its fields in `children`, a `list` its `element`, and a `map` its `key` and `value`. Writers before fdl-cli 0.24 do not write `columns`
- `primary_key` and `assets` are the table's `primary_key` in `_fdl.tables` and its columns whose `asset` is true in `_fdl.columns` ([The _fdl schema](/spec/fdl-schema)), when it has them. `assets` of `_fdl.files` is `["sha256"]`. In a version written before `_fdl` ([Tags of earlier versions](#tags-of-earlier-versions)), they are its tags `fdl.primary_key` and `fdl.assets`
- `renamed_columns` is `true` when a column of the table was ever renamed. Data files written before the rename carry the old name
- `views` holds every view alive at the snapshot, keyed `schema.view`: its `sql` as the metadata records it, and `columns`, the columns its query returns, each with `name` and `type` (DuckDB's type name). The writer finds the columns by binding the query, with the repositories of `catalogs` attached. A writer that cannot bind a view's query leaves its `columns` out. Earlier writers do not write `views`
- `catalogs` is `_fdl.catalogs`, each `name` mapped to its `url`, when it has rows
- `named` lists every data file and delete file the metadata names, at any of its snapshots, relative to `data_dir`. These are the data files the version reaches ([Data files](/spec/data-files)). It holds more than `tables` when the metadata keeps older snapshots

A reader that reads the Parquet files without DuckLake must not read a table whose files have `deletes`, or whose `renamed_columns` is `true`, unless it applies them as DuckLake does. Otherwise it returns deleted rows or misses renamed columns. A reader that finds each column in a data file by its `field_id`, using the `id` in `columns`, reads renamed columns as DuckLake does.

When a version is taken out of retention, `files.json` is deleted with its metadata files.

## profiles/

A profile summarizes one table at `ducklake.snapshot_id`: its row count, what each column holds, its first rows and, for a table with a geometry column, the extent and a sample of its shapes. A reader can show a table, or a host can index it for search, from this one small file, without opening the metadata or reading the data. It holds what was measured from the data, not what a column means. Like `files.json`, it is made from the metadata and the data, which win where the two differ, and it is optional: a reader must work without it.

Each table's profile is at `versions/<ID>/profiles/<schema>/<table>.json`. A view has one too, measured from the rows its query returns. A table or view whose schema or name is empty, `.`, `..` or holds `/` has none. A version made without a profile for a table, by a writer that did not profile or failed to, may get one later; it is measured from the version's metadata and data, like any other profile.

```json profiles/main/towns.json
{
  "rows": 1741,
  "columns": [
    { "name": "town_code", "type": "varchar", "null_count": 0, "distinct_count": 1741,
      "values": [{ "value": "082201", "count": 1 }] },
    { "name": "prefecture", "type": "varchar", "null_count": 0, "distinct_count": 2,
      "values": [{ "value": "Ibaraki", "count": 1200 }, { "value": "Tochigi", "count": 541 }] },
    { "name": "population", "type": "int64", "null_count": 3, "distinct_count": 1650,
      "min": 0, "max": 84211, "mean": 3120.5, "std": 4410.2, "q1": 420, "median": 1630, "q3": 3990,
      "values": [{ "value": "0", "count": 12 }],
      "histogram": [{ "start": 0, "end": 4210.55, "count": 1302 }] },
    { "name": "surveyed_on", "type": "date", "null_count": 0, "distinct_count": 1,
      "min": "2025-10-01", "max": "2025-10-01", "span_days": 0,
      "values": [{ "value": "2025-10-01", "count": 1741 }] },
    { "name": "coastal", "type": "boolean", "null_count": 0, "true_count": 210, "false_count": 1531 },
    { "name": "geom", "type": "geometry" }
  ],
  "head": [{ "town_code": "082201", "prefecture": "Ibaraki", "population": 2210, "surveyed_on": "2025-10-01", "coastal": false }],
  "geometry": {
    "column": "geom",
    "count": 1741,
    "bbox": [139.69, 35.74, 140.85, 37.15],
    "types": ["MULTIPOLYGON", "POLYGON"],
    "sample": { "type": "FeatureCollection", "features": [] }
  }
}
```

- `rows` is the table's row count. `columns` lists every column in the metadata's order, with `name` and `type` (DuckLake's type name) as in `files.json`. For a view, which has no columns in the metadata, they are the columns its query returns, with DuckDB's type names
- Every column but a geometry one has `null_count`. Numbers, text, dates and times have `distinct_count`, which may be estimated on a large table, and `values`
- `values` lists each value, as text, with the number of rows that hold it, most common first and then by value. When the column holds at most 2,000 distinct values, it lists them all and `distinct_count` is exact: a reader tells a whole list by its length matching `distinct_count`. Otherwise it lists the 10 most common. A host can search a whole list by value, such as a town's code or name
- Numbers have `min`, `max`, `mean`, `std`, the quartiles `q1`, `median` and `q3`, and a `histogram` of equal-width buckets between `min` and `max`. Dates and times have `min` and `max` as text, and dates and timestamps `span_days`. Booleans have `true_count` and `false_count`
- `head` holds the first rows the table returns, as JSON objects, leaving out geometry and columns whose values have no JSON form
- `geometry` describes the first geometry column, when the writer could read it: how many rows have a shape, the `bbox` of all of them (`[xmin, ymin, xmax, ymax]` in the column's coordinates), the geometry types present, and a GeoJSON `FeatureCollection` of a random sample of simplified shapes for drawing a map
- A writer may leave out any key it cannot compute. A reader ignores keys it does not know
- A writer reuses its parent's profile for a table whose data files (in `files.json`) and columns are the parent's, so profiling reads only the tables that changed. It reuses a view's when the view's `sql` is the parent's and every table and view its query reads is unchanged: a table by its data files, a view by the same rule. A view whose tables cannot be told from its query (one that reads a table function, say) reuses its parent's only when every view's `sql` is the parent's and no table's data files changed. It makes the profile again when the parent's `values` follow another limit than its own
- A writer makes no profile for a table or view whose `profile` in `_fdl.tables` is `false` ([The _fdl schema](/spec/fdl-schema)), nor for the tables of `_fdl`. A writer's tool sets it for the tables people are not meant to read, such as the intermediate tables a build makes, so that profiling does not read them. A host may also make profiles only of what it shows
- Profiles are part of the version and do not change with it. They are deleted with the metadata files when the version is taken out of retention

## The frozen metadata

The metadata is the file `metadata.path` names in the version's folder, `metadata.db`. It is a DuckLake metadata file in DuckDB's file format.

- A reader opens the file `metadata.path` names. Versions written by fdl-cli 0.26 and earlier name it `ducklake.duckdb` and record it as `catalog`, which a reader reads as `metadata`

- It holds one snapshot, `ducklake.snapshot_id`, and only the bookkeeping rows that snapshot reads. Earlier snapshots, the rows that ended before it and the files scheduled for deletion are left out. A table rebuilt by a tool such as dbt is written to all new files, so earlier snapshots in one metadata share little with the last one and would only make the file larger. Versions written by fdl-cli 0.31 and earlier also hold earlier snapshots
- It holds no inlined rows. All data is in data files
- Every data file it names is under `data_dir`, recorded relative to the metadata's data path. A writer must check this; a host may check it too
- Its `data_path` is the absolute URL of the data folder where the writer published: the root plus `data_dir`. Readers override it (see [Reading](/spec/reading))
- A reader reads an earlier version by opening that version's metadata ([Reading](/spec/reading#an-earlier-version)), not by `AT (VERSION => n)` in another version's metadata

## The _fdl schema

What FDL needs to know beyond the columns DuckLake records (primary keys, assets, the repositories views read, which tables get no profile), and what the dataset means to people, is kept in the metadata as rows of ordinary tables in the schema `_fdl` ([The _fdl schema](/spec/fdl-schema)). A writer's tool writes them with SQL before freezing. They are part of the data: a change to them makes a version, and they are merged like any table.

DuckLake's `comment` (`COMMENT ON`) is not FDL's. A writer keeps comments as they are, and FDL does not read them.

## Tags of earlier versions

Versions written before the `_fdl` schema keep what FDL reads as DuckLake tags of a table or a view: rows of `ducklake_tag` with the table's or view's ID as `object_id`, a key that starts with `fdl.`, and JSON text as the value. A version is one of them when its metadata has no schema `_fdl` and has `fdl.` tags.

| Tag | On | Read as |
| --- | --- | --- |
| `fdl.primary_key` | a table | `_fdl.tables.primary_key` |
| `fdl.assets` | a table | `_fdl.columns.asset` true for each column it names |
| `fdl.catalogs` | a view | rows of `_fdl.catalogs` |
| `fdl.profile` | a table or a view | `_fdl.tables.profile` |

- A reader reads them from the metadata, as `tags` of `duckdb_tables()` and `duckdb_views()`, or from the `ducklake_tag` rows, or a table's from `files.json`
- A writer that makes a version from such a version, by committing, reverting or merging, writes what it read into `_fdl` unless `_fdl` already says it, and writes no `fdl.` tags. The tags the working metadata keeps from the parent stay in the new version, and readers ignore them there, because its metadata has `_fdl`. In `diff` and in [When nothing changed](/spec/writing#when-nothing-changed) it compares them with those it read from the parent, so a table whose primary key and assets stay the same has not changed, and the tables of `_fdl` that hold only what the parent's tags said count as the parent's: they are not listed as added, and make no version on their own

### Assets

A table can point at files, such as the PDFs of a set of reports or the photos of a set of facilities, as Git LFS points at large files: the row holds the file's sha256, and the file is stored beside the data.

```sql
-- the table main.reports, where _fdl.columns marks pdf_sha256 as an asset
SELECT title, pdf_sha256, pdf_media_type FROM lake.main.reports;
```

- A column whose `asset` is true in `_fdl.columns`, and `sha256` of `_fdl.files`, holds, in each row, the lower-case hex sha256 of a file, or null. Its type is `varchar`
- Each file is stored at `assets/<sha256>` at the root (see [Layout](/spec/layout#paths)). The same content is stored once, and versions share it
- The file's name, size and media type are columns of the same table, as the writer names them. A writer should keep at least the media type, so a reader can tell how to show the file before it gets it
- To change a file, a writer stores the new content and puts its sha256 in the row. Older versions keep pointing at the old content
- A writer uploads a file with its media type as `Content-Type`. Every asset a version's rows hold must be in the repository before the version is written
- A reader finds a file at `<root>assets/<sha256>`, wherever it reads the repository from, and checks its sha256 before it shows it. A row whose file is missing is shown as broken
- A file that is not in the repository, such as one on the publisher's site, is held as a URL in an ordinary column. It is not an asset, and FDL does not manage it

## Metadata in other formats

One version may carry a metadata file per DuckLake format, so that a reader picks the file it can open instead of switching DuckDB.

```
versions/<ID>/
  metadata.db                the metadata as made (the one version.json describes)
  metadata/1.0.db            written out in format 1.0
  metadata/1.1.db            written out in format 1.1
```

- Every `metadata/<format>.db` must name the same data files and return the same rows
- A reader tries the formats it can open, newest first, then falls back to the metadata `metadata.path` names
- A writer that cannot produce a declared format for a version leaves that file out. A reader that can open none of the files of a version cannot read it
- A version that uses something an older format cannot express has no metadata in that format
- A format past its `until` in `fdl.json` is not written for new versions. Past versions may keep theirs
- Data files must be written so that the oldest format declared in `fdl.json` can read them
- `metadata.storage` should be the oldest DuckDB storage format the declared readers can open

[DuckLake formats](/internals/ducklake-formats) shows how to write an older format from DuckDB 2.0.

## Files of earlier versions

Versions written before tags and the `_fdl` schema held what FDL needs carry two more files beside the metadata. A version is one of them when its `version.json` has `dataset` or `tree`.

```
versions/<version ID>/
  dataset.json       what the version holds, described by the writer
  tree.json          other files of the version, such as a README, by path (optional)
objects/<sha256>     the files tree.json lists (shared, immutable)
```

- `dataset` and `tree` of `version.json` name these files, with their size and sha256, as `files` does
- A reader may ignore `dataset.json`, `tree.json` and `objects/`. A reader that shows them shows what the writer wrote then, not what the version means now
- Such a version has neither tags nor `_fdl`. A reader and a writer read its primary keys and assets from `dataset.json` instead: `primary_key` of an entry of `tables` (keyed by `schema` and `name`) is `_fdl.tables.primary_key`, and each entry of its `fields` with `"asset": true` is a column whose `asset` is true. `catalogs` at the top of `dataset.json` is `_fdl.catalogs`. Merging with such a version as the base, and deleting the assets no version reaches, depend on this
- `tree.json` lists `entries`, each a `path`, the file's `sha256` and `bytes`. Each file is at `objects/<sha256>`, a shared object the version reaches ([Data files](/spec/data-files#shared-objects)). Versions written by fdl-cli 0.31 and earlier stored these files at `assets/<sha256>`; a repository with such versions copies them to `objects/<sha256>`
- `dataset.json` and `tree.json` are deleted with the metadata files when the version is taken out of retention. Once no live version has a `tree.json`, every file under `objects/` is reached by none, and is deleted after the grace period
- A writer does not write these files for a new version. A writer that makes a version from such a version, by committing, reverting or merging, writes the primary keys, assets and catalogs it read into `_fdl`. In `diff` and in [When nothing changed](/spec/writing#when-nothing-changed) it compares them with those it read from the parent's `dataset.json`, so a table whose primary key and assets stay the same has not changed, and the tables of `_fdl` that hold only what the parent's `dataset.json` said count as the parent's
