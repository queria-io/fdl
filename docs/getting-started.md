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

## Check out a working catalog

You never write to the repository directly. You write to a working catalog, a DuckLake catalog in a SQLite file on your own disk, and turn it into a version when you are done.

```bash
fdl checkout ./towns/ work/lake.sqlite
```

Before the first version, `checkout` makes an empty catalog. Later it copies the catalog of the version `main` holds. It prints the statement to attach the working catalog with:

```sql
ATTACH 'ducklake:work/lake.sqlite' AS lake (
  DATA_PATH '/abs/path/towns/data/', OVERRIDE_DATA_PATH true,
  META_TYPE 'sqlite', DATA_INLINING_ROW_LIMIT 0,
  META_JOURNAL_MODE 'WAL', BUSY_TIMEOUT 5000
);
```

Beside the working catalog, `work/lake.sqlite.fdl.json` remembers the repository, the branch and the version the checkout was made from.

## Write

Use any DuckDB client. The Parquet files go straight into the repository's `data/` folder.

```sql
CREATE TABLE lake.main.towns AS
SELECT * FROM 'towns.csv';
```

`DATA_INLINING_ROW_LIMIT 0` makes DuckLake write every row to Parquet. Left at its default, DuckLake keeps small tables inside the catalog, and a version has to carry its rows in data files that a reader can fetch.

## Commit

```bash
fdl commit work/lake.sqlite -m "First towns" --author alice
```

`commit` freezes the working catalog into a version, uploads it and moves `main` to it. It prints `refs/heads/main -> 019a3c4e-… (version 1)`. The repository now holds:

```
towns/
  fdl.json
  HEAD
  refs/heads/main                       019a3c4e-…
  versions/019a3c4e-…/
    ducklake.duckdb
    version.json
  data/main/towns/ducklake-….parquet
```

```bash
fdl log ./towns/
```

```
   1  019a3c4e-…  2026-11-01T09:00:00Z  alice
      First towns
```

## Read

```bash
fdl url ./towns/
```

prints the catalog of the version `main` holds. Attach it read-only, and tell DuckDB where the data files are:

```sql
ATTACH 'ducklake:towns/versions/019a3c4e-…/ducklake.duckdb' AS towns (
  DATA_PATH 'towns/data/', OVERRIDE_DATA_PATH true, READ_ONLY
);
SELECT count(*) FROM towns.main.towns;
```

A version's catalog never changes, so a reader may cache it forever. Only `HEAD` and the files under `refs/` change.

## Make a second version

```bash
fdl checkout ./towns/ work/lake.sqlite
```

```sql
INSERT INTO lake.main.towns SELECT * FROM 'towns-2026.csv';
```

```bash
fdl commit work/lake.sqlite -m "Add 2026" --author alice
```

The new version lists the first as its parent. Both catalogs name the first Parquet file; only the second names the new one.

If someone else committed in between, the commit is refused, because the new version would not be built on what `main` holds now. Check out again and redo the change. [Versions and refs](/concepts/versions-and-refs) explains why.
