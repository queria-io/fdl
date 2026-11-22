---
title: The _fdl schema
description: The tables of the _fdl schema in the metadata - what a dataset, its tables and its columns mean, the primary keys, assets and catalogs FDL reads, the files of the dataset, and attributes for other readers.
---

What FDL needs to know about a dataset beyond the structure DuckLake records, and what the dataset, its tables and its columns mean to people, is kept in the metadata as ordinary DuckLake tables in the schema `_fdl`. It plays the part of `.gitattributes` in Git: a file inside the repository, versioned with everything else, that says how the tools handle the rest.

- `_fdl` is written like any other schema, with SQL, by whatever tool the writer uses, fdl-cli among them. A change to it is in the working metadata until the next commit, as a change to any table is
- A change to its rows makes a version, and its tables are merged like any table ([Writing](/spec/writing)). Each table's primary key is fixed by this page
- Every table of `_fdl`, and every column outside a table's primary key, is optional. A dataset without `_fdl` has no primary keys and no assets, and nothing described
- A reader ignores tables of `_fdl` and columns of its tables that this page does not define

`table` and `column` are DuckDB keywords; write them in double quotes in SQL (`"table"`).

## What FDL reads

| Where | Used for |
| --- | --- |
| `_fdl.tables.primary_key` | [Merging by rows](/spec/writing#rows-merged-by-primary-key) |
| `_fdl.columns.asset` | [Assets](/spec/versions#assets): which values are files to upload and to keep |
| `_fdl.tables.profile` | [profiles/](/spec/versions#profiles): which tables and views get no profile |
| `_fdl.catalogs` | Reading a view: a reader attaches each repository under its name first |
| `_fdl.files` | The files of the dataset, kept as assets |

The other columns are defined here so that tools agree on their meaning. FDL itself does not read them.

## _fdl.dataset

What the dataset as a whole means. At most one row, and no primary key: when both sides of a merge change the row, it is a conflict.

| Column | Type | Meaning |
| --- | --- | --- |
| `title` | VARCHAR | Title |
| `description` | VARCHAR | Description, in Markdown |
| `language` | VARCHAR | Language of the words, a BCP 47 tag (`ja`, `en`) |
| `homepage` | VARCHAR | The publisher's page for the data |
| `issued` | DATE | When the dataset was first published |
| `update_frequency` | VARCHAR | How often it is updated (`daily`, `monthly`, `irregular`, ...) |
| `licenses` | STRUCT(id VARCHAR, spdx VARCHAR, url VARCHAR, title VARCHAR)[] | Licenses. `spdx` is an SPDX identifier when there is one |
| `sources` | STRUCT(title VARCHAR, path VARCHAR)[] | Where the data comes from. `path` is a URL |
| `contributors` | STRUCT(title VARCHAR, path VARCHAR, roles VARCHAR[])[] | Who made or provides it |
| `temporal_coverage` | STRUCT(start VARCHAR, "end" VARCHAR) | The period the data covers |
| `spatial_coverage` | VARCHAR[] | The places it covers, as codes (`JP`) |

The names follow [DCAT](https://www.w3.org/TR/vocab-dcat-3/).

## _fdl.schemas

Primary key: `schema`.

| Column | Type | Meaning |
| --- | --- | --- |
| `schema` | VARCHAR | Schema name |
| `title` | VARCHAR | Title |
| `description` | VARCHAR | Description, in Markdown |

## _fdl.tables

One row per table or view. Primary key: `schema`, `name`.

| Column | Type | Meaning |
| --- | --- | --- |
| `schema` | VARCHAR | Schema name |
| `name` | VARCHAR | Table or view name |
| `title` | VARCHAR | Title |
| `description` | VARCHAR | Description, in Markdown |
| `licenses`, `sources`, `temporal_coverage`, `spatial_coverage` | as in `_fdl.dataset` | When they differ from the dataset's |
| `primary_key` | VARCHAR[] | The columns that identify a row. Tables only |
| `profile` | BOOLEAN | `false`: no profile is made for it. NULL is `true` |
| `sql` | VARCHAR | The SQL the table was made from, for a table built by a query. A view's SQL is in the metadata and is not repeated here |
| `depends_on` | VARCHAR[] | The tables and views it reads: `schema.name`, or `<repository URL>#schema.name` for another dataset |

## _fdl.columns

One row per column described. Primary key: `schema`, `table`, `name`.

| Column | Type | Meaning |
| --- | --- | --- |
| `schema` | VARCHAR | Schema name |
| `table` | VARCHAR | Table or view name |
| `name` | VARCHAR | Column name |
| `title` | VARCHAR | Title |
| `description` | VARCHAR | Description, in Markdown |
| `asset` | BOOLEAN | `true`: each value is the sha256 of an [asset](/spec/versions#assets). Tables only |

## _fdl.files

The files of the dataset that are not table data: its documents and the images they show. Primary key: `path`. `sha256` is an asset column, so each file is stored as an asset at `assets/<sha256>`.

| Column | Type | Meaning |
| --- | --- | --- |
| `path` | VARCHAR | Its path in the dataset, with `/` between folders. Not absolute, without `.` or `..` parts |
| `sha256` | VARCHAR | The lower-case hex sha256 of its content |
| `bytes` | BIGINT | Its size |
| `media_type` | VARCHAR | Its media type (`text/markdown`, `image/png`) |

FDL does not decide what a file is for. A host may show the files by their paths, as a Git host shows a folder's `README.md`.

## _fdl.catalogs

The other repositories the dataset's views read, by the name they are attached under. Primary key: `name`.

| Column | Type | Meaning |
| --- | --- | --- |
| `name` | VARCHAR | The name the views' SQL reads the repository under |
| `url` | VARCHAR | The URL of the repository's root |

## _fdl.attributes

Keys and values for one reader, as a `.gitattributes` line carries attributes Git does not know for the tools that do. Primary key: `schema`, `table`, `column`, `key`.

| Column | Type | Meaning |
| --- | --- | --- |
| `schema` | VARCHAR | Schema name, or `''` for the dataset |
| `table` | VARCHAR | Table or view name, or `''` for the dataset or a schema |
| `column` | VARCHAR | Column name, or `''` for a table, a view, a schema or the dataset |
| `key` | VARCHAR | Starts with the name of the reader it is for, then `-` (`example-theme`) |
| `value` | JSON | The value |

FDL reads none of them. One key per row, so that two changes to different keys of the same table merge.

## Checks

A writer refuses to freeze the working metadata when:

- a row of `_fdl.schemas`, `_fdl.tables` or `_fdl.columns` names a schema, table, view or column that is not there, or `_fdl.attributes` does with a name that is not `''`
- a column of `primary_key` is not in the table, or is named twice
- a column with `asset` true is not `varchar`
- a row of `_fdl.tables` for a view has a `primary_key`, or a row of `_fdl.columns` for a view has `asset` true
- `_fdl.dataset` has more than one row, or a table of `_fdl` lacks a column of its primary key

The rows name schemas, tables and columns by name. A writer's tool that renames a schema, a table or a column, or makes one again under another name, writes the new name into the rows of `_fdl` that name it, before it commits. A merge version is checked the same way: a merge whose version fails these checks, such as one where one side dropped a table and the other added rows of `_fdl.columns` for it, is a conflict.

The tables of `_fdl` describe themselves only as this page does: a writer adds no rows for them. `sha256` of `_fdl.files` is an asset column without a row in `_fdl.columns`. They get no profile.
