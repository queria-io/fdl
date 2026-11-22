---
title: FDL tables
description: The fdl_ tables in the metadata - what a dataset, its tables and its columns mean, the primary keys, assets and catalogs FDL reads, the files of the dataset, and attributes for other readers.
---

What FDL needs to know about a dataset beyond the structure DuckLake records, and what the dataset, its tables and its columns mean to people, is kept in the metadata file as ordinary tables whose names start with `fdl_`. They sit beside DuckLake's own `ducklake_` tables, in the same database (the working metadata in SQLite, `metadata.db` in a version), and are not DuckLake tables: DuckLake does not list them, snapshot them or write data files for them. They play the part of `.gitattributes` in Git: kept with the repository and versioned with everything else, but read by the tools as settings, not as content.

- A writer's tool writes them in the working metadata, with fdl-cli (`fdl meta`) or with SQL on the metadata file. A change is there until the next commit, as a change to any table is
- Checking out copies them from the version's metadata into the working metadata, and freezing copies them back, as it does DuckLake's tables ([Writing](/spec/writing#working-metadata)). A version also has them as [fdl_tables.json](/spec/versions#fdl_tablesjson), for readers that do not open the metadata
- A change to their rows makes a version, and they are merged like any table, by the primary keys this page gives them ([Merging](/spec/writing#merging))
- Every table, and every column outside a table's primary key, is optional. A dataset without them has no primary keys and no assets, and nothing described
- A reader ignores `fdl_` tables and columns this page does not define

Each column has one of the types `VARCHAR`, `BOOLEAN`, `BIGINT` or `JSON`, which SQLite and DuckDB both hold. A `JSON` value is text holding a JSON value: a list, an object, or a string, as each column says. A `DATE` is `VARCHAR` text in ISO 8601 (`2025-04-01`).

`table` and `column` are SQL keywords; write them in double quotes (`"table"`).

## What FDL reads

| Where | Used for |
| --- | --- |
| `fdl_tables.primary_key` | [Merging by cells](/spec/writing#cells-merged-by-primary-key) |
| `fdl_columns.asset` | [Assets](/spec/versions#assets): which values are files to upload and to keep |
| `fdl_tables.profile` | [profiles/](/spec/versions#profiles): which tables and views get no profile |
| `fdl_catalogs` | Reading a view: a reader attaches each repository under its name first |
| `fdl_files` | The files of the dataset, kept as assets |

The other columns are defined here so that tools agree on their meaning. FDL itself does not read them.

## fdl_dataset

What the dataset as a whole means. At most one row, and no primary key: a merge compares it column by column, as one row.

| Column | Type | Meaning |
| --- | --- | --- |
| `title` | VARCHAR | Title |
| `description` | VARCHAR | Description, in Markdown |
| `language` | VARCHAR | Language of the words, a BCP 47 tag (`ja`, `en`) |
| `homepage` | VARCHAR | The publisher's page for the data |
| `issued` | VARCHAR | When the dataset was first published, as a date (`2025-04-01`) |
| `update_frequency` | VARCHAR | How often it is updated (`daily`, `monthly`, `irregular`, ...) |
| `licenses` | JSON | Licenses, a list of objects with `id`, `spdx`, `url` and `title`. `spdx` is an SPDX identifier when there is one |
| `sources` | JSON | Where the data comes from, a list of objects with `title` and `path` (a URL) |
| `contributors` | JSON | Who made or provides it, a list of objects with `title`, `path` and `roles` (a list of strings) |
| `temporal_coverage` | JSON | The period the data covers, an object with `start` and `end` |
| `spatial_coverage` | JSON | The places it covers, a list of codes (`JP`) |

The names follow [DCAT](https://www.w3.org/TR/vocab-dcat-3/).

## fdl_schemas

Primary key: `schema`.

| Column | Type | Meaning |
| --- | --- | --- |
| `schema` | VARCHAR | Schema name |
| `title` | VARCHAR | Title |
| `description` | VARCHAR | Description, in Markdown |

## fdl_tables

One row per table or view. Primary key: `schema`, `name`.

| Column | Type | Meaning |
| --- | --- | --- |
| `schema` | VARCHAR | Schema name |
| `name` | VARCHAR | Table or view name |
| `title` | VARCHAR | Title |
| `description` | VARCHAR | Description, in Markdown |
| `licenses`, `sources`, `temporal_coverage`, `spatial_coverage` | as in `fdl_dataset` | When they differ from the dataset's |
| `primary_key` | JSON | The columns that identify a row, a list of names. Tables only |
| `profile` | BOOLEAN | `false`: no profile is made for it. NULL is `true` |
| `sql` | VARCHAR | The SQL the table was made from, for a table built by a query. A view's SQL is in the metadata and is not repeated here |
| `depends_on` | JSON | The tables and views it reads, a list of `schema.name`, or `<repository URL>#schema.name` for another dataset |

## fdl_columns

One row per column described. Primary key: `schema`, `table`, `name`.

| Column | Type | Meaning |
| --- | --- | --- |
| `schema` | VARCHAR | Schema name |
| `table` | VARCHAR | Table or view name |
| `name` | VARCHAR | Column name |
| `title` | VARCHAR | Title |
| `description` | VARCHAR | Description, in Markdown |
| `asset` | BOOLEAN | `true`: each value is the sha256 of an [asset](/spec/versions#assets). Tables only |

## fdl_files

The files of the dataset that are not table data: its documents and the images they show. Primary key: `path`. `sha256` is an asset column, so each file is stored as an asset at `assets/<sha256>`.

| Column | Type | Meaning |
| --- | --- | --- |
| `path` | VARCHAR | Its path in the dataset, with `/` between folders. Not absolute, without `.` or `..` parts |
| `sha256` | VARCHAR | The lower-case hex sha256 of its content |
| `bytes` | BIGINT | Its size |
| `media_type` | VARCHAR | Its media type (`text/markdown`, `image/png`) |

FDL does not decide what a file is for. A host may show the files by their paths, as a Git host shows a folder's `README.md`.

## fdl_catalogs

The other repositories the dataset's views read, by the name they are attached under. Primary key: `name`.

| Column | Type | Meaning |
| --- | --- | --- |
| `name` | VARCHAR | The name the views' SQL reads the repository under |
| `url` | VARCHAR | The URL of the repository's root |

## fdl_attributes

Keys and values for one reader, as a `.gitattributes` line carries attributes Git does not know for the tools that do. Primary key: `schema`, `table`, `column`, `key`.

| Column | Type | Meaning |
| --- | --- | --- |
| `schema` | VARCHAR | Schema name, or `''` for the dataset |
| `table` | VARCHAR | Table or view name, or `''` for the dataset or a schema |
| `column` | VARCHAR | Column name, or `''` for a table, a view, a schema or the dataset |
| `key` | VARCHAR | Starts with the name of the reader it is for, then `-` (`example-theme`) |
| `value` | JSON | The value |

FDL reads none of them. One key per row, so that each key is a value of its own.

## Checks

A writer refuses to freeze the working metadata when:

- a row of `fdl_schemas`, `fdl_tables` or `fdl_columns` names a schema, table, view or column that is not there, or `fdl_attributes` does with a name that is not `''`
- a column of `primary_key` is not in the table, or is named twice
- a column with `asset` true is not `varchar`
- a row of `fdl_tables` for a view has a `primary_key`, or a row of `fdl_columns` for a view has `asset` true
- `fdl_dataset` has more than one row, or an `fdl_` table lacks a column of its primary key, or a `JSON` value is not JSON

The rows name schemas, tables and columns by name. A writer's tool that renames a schema, a table or a column, or makes one again under another name, writes the new name into the `fdl_` rows that name it, before it commits. A merge version is checked too ([Merging](/spec/writing#fdl-tables)).

The `fdl_` tables are not DuckLake tables, so no `fdl_` row names them. `sha256` of `fdl_files` is an asset column without a row in `fdl_columns`.
