---
title: Getting started
description: Make a repository, write a first version with DuckDB, and read it back.
---

This page makes a repository in a folder, writes a table into it and reads the result as a reader would. The same commands work on S3 and R2: pass `s3://bucket/prefix/` instead of a folder.

## Install

```bash
pip install 'fdl-cli[s3]'
```

The package installs the `fdl` command and the `fdl` Python package. Python 3.10 or later is needed. Leave out `[s3]` if you only use folders.

## Make a repository

```bash
fdl init ./towns/
```

`init` writes two files and nothing else:

```
towns/
  fdl.json      {"fdl": "0.1", "id": "5f0c…", "data_dir": "data/"}
  HEAD          ref: refs/heads/main
```

`HEAD` names the default branch, `main`. The branch itself does not exist yet: there is no version for it to point at.

`fdl init` makes the repository where readers will find it, as `git init --bare` does on a server. Your own place to write is the working metadata, made next.

## Clone a working metadata file

You never write to the repository directly. You write to a working metadata file, a DuckLake metadata file in a SQLite file on your own disk, and turn it into a version when you are done.

```bash
mkdir work && cd work
fdl clone ../towns/ lake.sqlite
```

Before the first version, `clone` makes an empty metadata. Later it copies the metadata of the version `main` holds. It prints the statement to attach the working metadata with:

```sql
ATTACH 'ducklake:lake.sqlite' AS lake (
  DATA_PATH '/abs/path/towns/data/', OVERRIDE_DATA_PATH true,
  META_TYPE 'sqlite', DATA_INLINING_ROW_LIMIT 0,
  META_JOURNAL_MODE 'WAL', BUSY_TIMEOUT 5000
);
```

Beside the working metadata, `lake.sqlite.fdl.json` remembers the repository, the branch and the version the clone was made from.

## Write

Use any DuckDB client. The Parquet files go straight into the repository's `data/` folder.

```sql
CREATE TABLE lake.main.towns AS
SELECT * FROM '../towns.csv';
```

`DATA_INLINING_ROW_LIMIT 0` makes DuckLake write every row to Parquet. Left at its default, DuckLake keeps small tables inside the metadata, and a version has to carry its rows in data files that a reader can fetch.

## Declare

Tell FDL which columns identify a row. A merge matches rows by the primary key, and readers can look rows up by it:

```bash
fdl declare primary-key lake.sqlite main.towns code
```

Declarations stay with the working metadata until the commit, which checks them against the tables (see [Declarations](/spec/versions#declarations)). They are optional: a version without them holds the tables alone.

## Commit and push

```bash
fdl commit lake.sqlite -m "First towns" --author alice
fdl push lake.sqlite
```

`commit` freezes the working metadata into a version on your disk. `push` measures each table into a small summary (a profile) that readers can show without reading the data, uploads the version with them and moves `main` to it, and prints `refs/heads/main -> 019a3c4e-… (version 1)`. The repository now holds:

```
towns/
  fdl.json
  HEAD
  refs/heads/main                       019a3c4e-…
  versions/019a3c4e-…/
    metadata.db
    tables.json
    profiles/main/towns.json
    version.json
  tables/main/towns/ducklake-….parquet
```

```bash
fdl log ../towns/
```

```
   1  019a3c4e-…  2026-11-01T09:00:00Z  alice
      First towns
```

## Read

```bash
fdl url ../towns/
```

prints the metadata of the version `main` holds. Attach it read-only, and tell DuckDB where the data files are:

```sql
ATTACH 'ducklake:../towns/versions/019a3c4e-…/metadata.db' AS towns (
  DATA_PATH '../towns/data/', OVERRIDE_DATA_PATH true, READ_ONLY
);
SELECT count(*) FROM towns.main.towns;
```

A version's metadata never changes, so a reader may cache it forever. Only `HEAD` and the files under `refs/` change.

## Make a second version

```bash
fdl clone ../towns/ lake.sqlite
```

```sql
INSERT INTO lake.main.towns SELECT * FROM '../towns-2026.csv';
```

```bash
fdl commit lake.sqlite -m "Add 2026" --author alice
fdl push lake.sqlite
```

The new version lists the first as its parent. Both metadata files name the first Parquet file; only the second names the new one.

If someone else pushed in between, the push is refused, because the new version would not be built on what `main` holds now. Clone again and redo the change. [Versions and refs](/concepts/versions-and-refs) explains why.
