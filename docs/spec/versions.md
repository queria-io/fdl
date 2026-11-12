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
  "tree": {
    "path": "tree.json",
    "bytes": 214,
    "sha256": "3c5d…"
  },
  "diff": {
    "tables": { "added": [], "dropped": [] },
    "rows": [{ "table": "main.population", "from": 1741, "to": 1802 }],
    "columns": [
      { "table": "main.population", "kind": "add_column", "column": "note", "breaking": false }
    ],
    "data": ["main.population"],
    "descriptions": ["main.population.note"]
  },
  "inputs": [
    {
      "source_id": "example:population-2025",
      "sha256": "1b7e…",
      "retrieved_at": "2026-10-29T03:12:00Z",
      "url": "https://example.org/population-2025.csv"
    }
  ],
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
| `catalog` | must | The catalog's file name, size, sha256, and storage format (`storage`) |
| `dataset` | must | The `dataset.json` beside the catalog: its file name, size and sha256. Versions written before it was required lack it |
| `ducklake` | must | The DuckLake format, the snapshot frozen, and the range of snapshots this version adds after its parent (first and last) |
| `files` | may | The `files.json` beside the catalog, if written: its file name, size and sha256 ([files.json](#filesjson)) |
| `tree` | may | The `tree.json` beside the catalog, if the version holds other files: its file name, size and sha256 ([tree.json](#treejson)) |
| `diff` | may | What changed since the first parent, or in the first version ([diff](#diff)) |
| `inputs` | may | Source files used: where each came from, when, and its sha256. FDL does not store them; a writer that keeps them makes them assets of a table. How `source_id` is assigned is up to the writer |
| `annotations` | may | What the format does not interpret, keyed by the name of whoever reads it (a host, a tool) |

A reader must ignore fields it does not know.

The `path` of `catalog`, `dataset`, `files` and `tree` is a file name in the version's folder: it holds no `/` or `\` and is not `.` or `..`. A reader must refuse a record with any other.

A version has one parent, or two for a merge: the version of the branch it was made on, then the version merged into it (see [Merging](/spec/writing#merging)). The first version has none. A reader must accept a list of any length, follow the first entry for the history of a branch, and follow every entry to find whether one version descends from another.

The description and SQL of each snapshot stay in the catalog, in DuckLake's `commit_message` and `commit_extra_info`. They are not copied into `version.json`.

### diff

`diff` lists what changed between the version and its first parent, so that a reader can show a history from `version.json` alone. In the first version every table is added. `table` is `schema.table` in every key.

| Key | Holds |
| --- | --- |
| `tables` | `added` and `dropped`: the tables only one of the two holds |
| `rows` | `table`, `from`, `to`: the tables both hold whose row count differs |
| `columns` | The column differences, below |
| `data` | The tables both hold whose data files differ, even with the same rows |
| `descriptions` | What has another `title` or `description` in `dataset.json`: `dataset`, a table, or `schema.table.column` |

A writer compares the catalog at the parent's snapshot with the frozen one. It leaves `diff` out when the catalog no longer holds the parent's snapshot, and lists `descriptions` only when it had both `dataset.json`. A reader compares the two versions' `dataset.json` and `files.json` for a version without `diff`, or against a version other than its first parent.

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
  },
  "named": [
    "main/population/ducklake-019a3c4e-….parquet",
    "main/population/ducklake-019a3c4f-…-delete.parquet",
    "main/population/ducklake-0199f2a1-….parquet"
  ]
}
```

- `tables` holds every table alive at the snapshot, keyed `schema.table`. A table with no rows has an empty `files`
- `path` is relative to the repository's `data_dir`. Files are in the catalog's order
- `rows` and `bytes` are the row count and size the catalog records. `footer_bytes` is the size of the Parquet footer, when recorded
- `deletes` is the delete file for that data file, if rows were deleted from it
- `renamed_columns` is `true` when a column of the table was ever renamed. Data files written before the rename carry the old name
- `named` lists every data file and delete file the catalog names, at any of its snapshots, relative to `data_dir`. These are the data files the version reaches ([Data files](/spec/data-files)). It holds more than `tables` when the catalog keeps older snapshots

A reader that reads the Parquet files without DuckLake must not read a table whose files have `deletes`, or whose `renamed_columns` is `true`, unless it applies them as DuckLake does. Otherwise it returns deleted rows or misses renamed columns.

When a version is taken out of retention, `files.json` is deleted with its catalogs.

## tree.json

`tree.json` lists the files a version holds beside its tables, such as a README and the images it shows, by path. It plays the part of Git's tree: each path names the sha256 of a file, and the file is stored as an asset at `assets/<sha256>`. The tables are not in it; the catalog is their tree.

```json tree.json
{
  "entries": [
    { "path": "README.md", "sha256": "5e88…", "bytes": 3120 },
    { "path": "docs/images/towns.png", "sha256": "c9d2…", "bytes": 48211 }
  ]
}
```

- `entries` lists each file's path, sha256 (lower-case hex) and size, by path. Paths are relative, separated by `/`, have no empty, `.` or `..` segment, and are unique
- Files are stored as written. A relative link in a Markdown file is resolved against the file's own path, percent-decoded, and looked up in `entries`, as GitHub resolves a link in a README through the commit's tree. A link to a path not in `entries` is broken
- Readers commonly show `README.md` first. Which file to show, and where, is the reader's choice
- `description` in `dataset.json` stays a short plain text. A reader that does not read the tree still has it, and lists show it
- A writer keeps the files where it edits them, such as beside its YAML in a Git repository, and its tool writes `tree.json`. The writer does not write it by hand
- `tree.json` is part of the version and does not change with it. It is deleted with the catalogs when the version is taken out of retention

## profiles/

A profile summarizes one table at `ducklake.snapshot_id`: its row count, what each column holds, its first rows and, for a table with a geometry column, the extent and a sample of its shapes. A reader can show a table, or a host can index it for search, from this one small file, without opening the catalog or reading the data. It holds what was measured from the data; what a column means is the writer's, in `dataset.json`. Like `files.json`, it is made from the catalog and the data, which win where the two differ, and it is optional: a reader must work without it.

Each table's profile is at `versions/<ID>/profiles/<schema>/<table>.json`. A table whose schema or name is empty, `.`, `..` or holds `/` has none. Views have none.

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

- `rows` is the table's row count. `columns` lists every column in the catalog's order, with `name` and `type` (DuckLake's type name) as in `dataset.json`
- Every column but a geometry one has `null_count`. Numbers, text, dates and times have `distinct_count`, which may be estimated on a large table, and `values`
- `values` lists each value, as text, with the number of rows that hold it, most common first and then by value. When the column holds at most 2,000 distinct values, it lists them all and `distinct_count` is exact: a reader tells a whole list by its length matching `distinct_count`. Otherwise it lists the 10 most common. A host can search a whole list by value, such as a town's code or name
- Numbers have `min`, `max`, `mean`, `std`, the quartiles `q1`, `median` and `q3`, and a `histogram` of equal-width buckets between `min` and `max`. Dates and times have `min` and `max` as text, and dates and timestamps `span_days`. Booleans have `true_count` and `false_count`
- `head` holds the first rows the table returns, as JSON objects, leaving out geometry and columns whose values have no JSON form
- `geometry` describes the first geometry column, when the writer could read it: how many rows have a shape, the `bbox` of all of them (`[xmin, ymin, xmax, ymax]` in the column's coordinates), the geometry types present, and a GeoJSON `FeatureCollection` of a random sample of simplified shapes for drawing a map
- A writer may leave out any key it cannot compute. A reader ignores keys it does not know
- A writer reuses its parent's profile for a table whose data files (in `files.json`) and columns are the parent's, so a commit reads only the tables that changed. It makes the profile again when the parent's `values` follow another limit than its own
- Profiles are part of the version and do not change with it. They are deleted with the catalogs when the version is taken out of retention

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
  "catalogs": { "census": "https://example.org/census/" },
  "tables": [
    {
      "schema": "main",
      "name": "population",
      "title": "Population",
      "description": "One row per town, age group and sex",
      "primary_key": ["town_code", "age_group", "sex"],
      "depends_on": ["https://example.org/census/#main.towns"],
      "sql": "SELECT …",
      "rows": 120000,
      "fields": [
        { "name": "town_code", "title": "Town code", "description": "…", "type": "varchar", "nullable": false }
      ]
    }
  ],
  "annotations": {}
}
```

- `name` and `title` are required. Everything else may be left out
- A writer may keep its description as YAML (`dataset.yml`), and may keep each table in its own file (`<name>.table.yml`, one entry of `tables`). The version always holds the whole description as one JSON
- Each of `licenses` is an SPDX identifier, or an object with `id` and what the writer wants to add (`spdx`, `url`, `title`)
- `tables` lists every table of the catalog's last snapshot. The writer's tool adds the tables the writer did not describe, with what the catalog knows of them, and refuses a described table or column the catalog lacks
- `depends_on` lists the tables a table is made from. A table of this dataset is `schema.name`; a table of another dataset is the URL of its repository, `#` and `schema.name` (`https://example.org/census/#main.towns`). When a table has `sql` and no `depends_on`, the writer's tool reads it from the SQL: the tables it reads, with each catalog other than its own named through `catalogs`. What the SQL does not let it resolve is left out
- `catalogs` maps the name another dataset is attached under in the SQL to the URL of its repository (`{"census": "https://example.org/census/"}`). A reader can attach each under that name to read the views that use it
- A view of the catalog is listed after the tables with `"kind": "view"` and its `sql` from the catalog. The catalog records no columns for a view, so its `fields` are the writer's, without `type`, `nullable` or `rows`. `kind` is left out for a table
- From the catalog: each table's `rows`, and each field's `type` (DuckLake's type name) and `nullable`. Comments in the catalog (`COMMENT ON`) are used where the writer gave no `description`
- A reader ignores keys this specification does not define. A writer's tool may refuse them, to catch a misspelt key. What the format does not interpret goes under `annotations`, keyed by the name of whoever reads it, at the top, in a table or in a field. Writers keep it as written
- Versions written before `annotations` had its name carry it as `extensions`. A reader reads `extensions` as `annotations`
- What a license or a source says is the writer's claim. A host may check it before it publishes

### Assets

A table can point at files, such as the PDFs of a set of reports or the photos of a set of facilities, as Git LFS points at large files: the row holds the file's sha256, and the file is stored beside the data.

```json
{ "name": "sha256", "title": "PDF", "type": "varchar", "asset": true }
```

- A field with `"asset": true` holds, in each row, the lower-case hex sha256 of a file, or null. Its type is `varchar`
- Each file is stored at `assets/<sha256>` at the root (see [Layout](/spec/layout#paths)). The same content is stored once, and versions share it
- The file's name, size and media type are columns of the same table, as the writer names them. A writer should keep at least the media type, so a reader can tell how to show the file before it gets it
- To change a file, a writer stores the new content and puts its sha256 in the row. Older versions keep pointing at the old content
- A writer uploads a file with its media type as `Content-Type`. Every asset a version's rows hold must be in the repository before the version is written
- A reader finds a file at `<root>assets/<sha256>`, wherever it reads the repository from, and checks its sha256 before it shows it. A row whose file is missing is shown as broken
- A file that is not in the repository, such as one on the publisher's site, is held as a URL in an ordinary column. It is not an asset, and FDL does not manage it

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
