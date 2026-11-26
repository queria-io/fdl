---
title: Versions
description: The version ID, the fields of version.json, tables.json, profiles, the frozen metadata and declarations.
---

A version is the files under `versions/<version ID>/`. They must not change once written, except:

- `expired.json` is added and the metadata files deleted when the version falls out of retention ([Data files](/spec/data-files#past-retention))
- on withdrawal, `expired.json` is added with `"reason": "withdrawn"` and the metadata files deleted, and `version.json` is rewritten without the withdrawn data if it holds it ([Data files](/spec/data-files#withdrawal))
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
    "storage": "duckdb-v1.5.0",
    "bytes": 4194304,
    "sha256": "9f2c…"
  },
  "ducklake": {
    "format": "1.0",
    "snapshot_id": 345,
    "snapshots": [301, 345]
  },
  "tables": {
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
  }
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
| `message` | must | What changed. A tool that records how the version was made, such as the commit of the code that built it, adds trailers at the end, as Git does (`Built-From: 1e6e88f…`) |
| `metadata` | must | The size, sha256 and storage format (`storage`) of `metadata.db` |
| `ducklake` | must | The DuckLake format, the snapshot frozen, and the range of snapshots the working metadata made for this version after its parent (first and last). Only the snapshot frozen is in the metadata |
| `tables` | must | The size and sha256 of `tables.json` ([tables.json](#tablesjson)). The record of a version past retention may have none |
| `diff` | may | What changed since the first parent, or in the first version ([diff](#diff)) |

A reader must ignore fields it does not know.

The files of a version have fixed names in its folder: `metadata.db` and `tables.json`. The record does not name them.

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
| `declarations` | The tables and views both hold whose [declarations](#declarations) differ |
| `dependencies` | The names in `dependencies` that only one of the two holds, or whose `url` or `version` differs |

A writer compares the working metadata at the parent's snapshot with its last snapshot, before freezing. The working metadata is cloned from the parent's metadata, so it holds the parent's snapshot. The writer leaves `diff` out when it does not. A reader compares the two versions' `tables.json` for a version without `diff`, or against a version other than its first parent.

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

A reader can tell from `version.json` alone whether it can open a version. [DuckLake formats](/internals/ducklake-formats) shows how a writer on a newer DuckDB writes a format older readers can open.

## tables.json

`tables.json` lists the tables and views of the version at `ducklake.snapshot_id`, with each table's data files and columns, and holds the [declarations](#declarations) FDL reads. Every version has one. A reader finds what a version holds, and how FDL handles each table, without opening the metadata.

```json tables.json
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
    },
    "main.reports": {
      "files": [],
      "columns": [
        { "id": 1, "name": "report_id", "type": "varchar", "nullable": false },
        { "id": 2, "name": "pdf_sha256", "type": "varchar", "nullable": true }
      ],
      "primary_key": ["report_id"],
      "assets": ["pdf_sha256"]
    }
  },
  "views": {
    "main.population_by_area": {
      "sql": "SELECT c.area, sum(p.population) AS population FROM …",
      "columns": [
        { "name": "area", "type": "VARCHAR" },
        { "name": "population", "type": "HUGEINT" }
      ]
    }
  },
  "dependencies": {
    "census": { "url": "https://example.org/census/", "version": "019a2f10-0c4d-7e88-a1b2-55c0d9e3f7aa" }
  }
}
```

- `tables` holds every table alive at the snapshot, keyed `schema.table`. A table with no rows has an empty `files`
- `path` is relative to the dataset's `data_dir`. Files are in the metadata's order
- `rows` and `bytes` are the row count and size the metadata records. `footer_bytes` is the size of the Parquet footer, when recorded
- `deletes` is the delete file for that data file, if rows were deleted from it
- `columns` lists the table's columns at the snapshot, in the metadata's order. `id` is DuckLake's column ID, which DuckLake writes as the `field_id` of the column in every Parquet data file; it stays the same when the column is renamed. `type` is DuckLake's type name, and `nullable` is `false` for a `NOT NULL` column. `default` is the value, as text, that rows of data files written before the column was added hold, when it is not NULL. A `struct` has its fields in `children`, a `list` its `element`, and a `map` its `key` and `value`
- `renamed_columns` is `true` when a column of the table was ever renamed. Data files written before the rename carry the old name
- `views` holds every view alive at the snapshot, keyed `schema.view`. `sql` is the view's SQL as the metadata records it, with DuckLake's `{DUCKLAKE_CATALOG}.` removed: DuckLake writes that placeholder where the SQL names the view's own catalog, so a table of the same dataset is written `main.population`, and a reader runs the SQL with the version's metadata as the default catalog. Names of other catalogs, those `dependencies` gives, stay as written. `columns` lists the columns its query returns, in order, with `name` and `type` (DuckDB's type names). The writer gets them by binding the query, with each entry of `dependencies` attached
- `primary_key`, `assets` and `profile` of a table or a view, and `dependencies`, are declarations. A table or view without a declaration leaves its key out, and a version whose views read no other dataset leaves out `dependencies`

The writer makes `files`, `columns`, `renamed_columns` and `sql` from the metadata when it freezes, and they must agree with it. The declarations are kept only here. A reader ignores keys it does not know.

A reader that reads the Parquet files without DuckLake must not read a table whose files have `deletes`, or whose `renamed_columns` is `true`, unless it applies them as DuckLake does. Otherwise it returns deleted rows or misses renamed columns. A reader that finds each column in a data file by its `field_id`, using the `id` in `columns`, reads renamed columns as DuckLake does.

When a version is taken out of retention, `tables.json` is deleted with its metadata files.

## profiles/

A profile summarizes one table at `ducklake.snapshot_id`: its row count, what each column holds, its first rows and, for a table with a geometry column, the extent and a sample of its shapes. A reader can show a table, or a host can index it for search, from this one small file, without opening the metadata or reading the data. It holds what was measured from the data, not what a column means. It is made from the metadata and the data, which win where the two differ, and it is optional: a reader must work without it.

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

- `rows` is the table's row count. `columns` lists every column in the metadata's order, with `name` and `type` (DuckLake's type name) as in `tables.json`. For a view, which has no columns in the metadata, they are the columns its query returns, with DuckDB's type names
- Every column but a geometry one has `null_count`. Numbers, text, dates and times have `distinct_count`, which may be estimated on a large table, and `values`
- `values` lists each value, as text, with the number of rows that hold it, most common first and then by value. When the column holds at most 2,000 distinct values, it lists them all and `distinct_count` is exact: a reader tells a whole list by its length matching `distinct_count`. Otherwise it lists the 10 most common. A host can search a whole list by value, such as a town's code or name
- Numbers have `min`, `max`, `mean`, `std`, the quartiles `q1`, `median` and `q3`, and a `histogram` of equal-width buckets between `min` and `max`. Dates and times have `min` and `max` as text, and dates and timestamps `span_days`. Booleans have `true_count` and `false_count`
- `head` holds the first rows the table returns, as JSON objects, leaving out geometry and columns whose values have no JSON form
- `geometry` describes the first geometry column, when the writer could read it: how many rows have a shape, the `bbox` of all of them (`[xmin, ymin, xmax, ymax]` in the column's coordinates), the geometry types present, and a GeoJSON `FeatureCollection` of a random sample of simplified shapes for drawing a map
- A writer may leave out any key it cannot compute. A reader ignores keys it does not know
- A writer reuses its parent's profile for a table whose data files (in `tables.json`) and columns are the parent's, so profiling reads only the tables that changed. It reuses a view's when the view's `sql` is the parent's and every table and view its query reads is unchanged: a table by its data files, a view by the same rule. A view that reads another dataset reuses its parent's only when `dependencies` is the parent's and every entry the view reads names a `version`. A view whose tables cannot be told from its query (one that reads a table function, say) reuses its parent's only when every view's `sql` is the parent's and no table's data files changed. It makes the profile again when the parent's `values` follow another limit than its own
- A writer makes no profile for a table or view whose `profile` declaration is `false`. A writer's tool sets it for the tables people are not meant to read, such as the intermediate tables a build makes, so that profiling does not read them. A host may also make profiles only of what it shows
- Profiles are part of the version and do not change with it. They are deleted with the metadata files when the version is taken out of retention

## The frozen metadata

The metadata is the file `metadata.db` in the version's folder. It is a DuckLake metadata file in DuckDB's file format.

- It holds one snapshot, `ducklake.snapshot_id`, and only the bookkeeping rows that snapshot reads. Earlier snapshots, the rows that ended before it and the files scheduled for deletion are left out. A table rebuilt by a tool such as dbt is written to all new files, so earlier snapshots in one metadata share little with the last one and would only make the file larger.
- It holds no inlined rows. All data is in data files
- Every data file it names is under `data_dir`, recorded relative to the metadata's data path. A writer must check this; a host may check it too
- Its `data_path` is the absolute URL of the data folder where the writer published: the root plus `data_dir`. Readers override it (see [Reading](/spec/reading))
- A reader reads an earlier version by opening that version's metadata ([Reading](/spec/reading#an-earlier-version)), not by `AT (VERSION => n)` in another version's metadata

## Declarations

What FDL needs to know about a table, a view or the dataset beyond what DuckLake records is declared in [tables.json](#tablesjson).

| Declaration | On | Value | Used for |
| --- | --- | --- | --- |
| `primary_key` | a table | The names of the columns that identify a row, as a list | [Merging by cells](/spec/writing#cells-merged-by-primary-key) |
| `assets` | a table | The names of the columns that hold assets, as a list | [Assets](#assets) |
| `profile` | a table or a view | `false`: the writer makes no profile for it. Left out, it is `true` | [profiles/](#profiles) |
| `dependencies` | the version | Each other dataset the views' SQL reads, keyed by the name the SQL reads it under: `url`, the URL of its root, and `version`, the ID of the version the commit bound the views against. A reader reads that version. Without `version`, the version `HEAD` names is read | [Reading a view](/spec/reading#views-that-read-other-datasets) |

- A writer's tool sets declarations between the clone and the commit. How it keeps them until the commit is up to it
- A commit starts from the parent's declarations, so a declaration stays until a tool changes or removes it. A tool declares a dependency by its `url` only: each commit reads the version the dependency's `HEAD` names, binds the views against it, and writes its ID as `version`. When it cannot read the dependency, it keeps the `version` the parent recorded for the same `url`, and leaves `version` out when the parent recorded none. So a version's views read the data they were committed with, and the next commit moves to what the dependency holds then. A table or view is found by `schema.table`, so a table made again under the same name, as dbt's `CREATE OR REPLACE` does, keeps its declarations. A table or view that is dropped loses them
- A column renamed after a declaration named it keeps its old name in the parent's declaration. The writer follows the column's `id` and writes the new name. It follows `id` only while the table keeps its DuckLake table ID: a table made again under the same name gets new column IDs, and its declarations are matched by column name
- Every column a declaration names must be in the table at the snapshot, once. A column of `assets` must be `varchar`. A view has no `primary_key` or `assets`. A name in `dependencies` matches `[a-z_][a-z0-9_]*` and is not one DuckDB reserves (`memory`, `system`, `temp`); a reader attaches the version's own metadata under a name that is none of them. `url` of `dependencies` is an absolute URL ending in `/`, and `version`, when there, is a [version ID](#version-id). A writer refuses to freeze otherwise
- Declarations are part of the table: a table or view whose declarations changed has changed ([When nothing changed](/spec/writing#when-nothing-changed), [Merging](/spec/writing#tables))

FDL defines no declaration for what a dataset, a table or a column means to people, such as a title, a description, a license, a source or a guide. A writer may keep it as rows of ordinary tables in the metadata, such as in a schema of its own, and declare the columns that hold the sha256 of a document or an image as `assets`. FDL treats those tables as any other: a change to their rows makes a version, and they are merged like any table. A host may also keep it by its own means, and may refuse a push without it ([When a push is refused](/spec/writing#when-a-push-is-refused)).

### Assets

A table can point at files, such as the PDFs of a set of reports or the photos of a set of facilities, as Git LFS points at large files: the row holds the file's sha256, and the file is stored beside the data.

```sql
-- the table main.reports, declared with "assets": ["pdf_sha256"]
SELECT title, pdf_sha256, pdf_media_type FROM lake.main.reports;
```

- A column `assets` names holds, in each row, the lower-case hex sha256 of a file, or null. Its type is `varchar`
- Each file is stored at `assets/<sha256>` at the root (see [Layout](/spec/layout#paths)). The same content is stored once, and versions share it
- The file's name, size and media type are columns of the same table, as the writer names them. A writer should keep at least the media type, so a reader can tell how to show the file before it gets it
- To change a file, a writer stores the new content and puts its sha256 in the row. Older versions keep pointing at the old content
- A writer uploads a file with its media type as `Content-Type`. Every asset a version's rows hold must be in the dataset before the version is written
- A reader finds a file at `<root>assets/<sha256>`, wherever it reads the dataset from, and checks its sha256 before it shows it. A row whose file is missing is shown as broken
- A file that is not in the dataset, such as one on the publisher's site, is held as a URL in an ordinary column. It is not an asset, and FDL does not manage it
