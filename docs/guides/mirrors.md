---
title: Mirrors
description: Copying a repository to another host, keeping the copy up to date, and reading from it.
---

A mirror is a copy of a repository on another host. Since versions and data files never change, mirroring is copying files that are not there yet and then the refs.

## Copying

Copy in the same order a push writes, so a reader of the mirror never follows a ref to a version that is not all there:

1. data files under `data/` and assets under `assets/` that the mirror lacks
2. version folders under `versions/` that the mirror lacks, `version.json` last in each
3. `fdl.json`, if it changed
4. `refs/`, `HEAD` and `refs.json`

```bash
aws s3 sync s3://origin/towns/data/     s3://mirror/towns/data/
aws s3 sync s3://origin/towns/assets/   s3://mirror/towns/assets/
aws s3 sync s3://origin/towns/versions/ s3://mirror/towns/versions/
aws s3 cp   s3://origin/towns/fdl.json  s3://mirror/towns/fdl.json
aws s3 sync s3://origin/towns/refs/     s3://mirror/towns/refs/ --delete
aws s3 cp   s3://origin/towns/HEAD      s3://mirror/towns/HEAD
aws s3 cp   s3://origin/towns/refs.json s3://mirror/towns/refs.json
```

A mirror keeps the origin's `id` in `fdl.json`. That is how a tool can tell the two URLs hold the same repository.

A mirror is for reading. Pushing to both the origin and a mirror would give them different histories under the same `id`.

## Reading from a mirror

The catalog records where the writer published its data files: the origin's data folder, as an absolute URL. A reader of the mirror gives DuckDB the mirror's data folder instead:

```sql
ATTACH 'ducklake:https://mirror.example.org/towns/versions/019a3c4e-…/ducklake.duckdb' AS towns (
  DATA_PATH 'https://mirror.example.org/towns/data/', OVERRIDE_DATA_PATH true, READ_ONLY
);
```

Without `OVERRIDE_DATA_PATH true`, DuckDB refuses to use a data path other than the one the catalog records. Readers always pass both options, at the origin too, so the same statement works anywhere with only the root changed.

## Checking what was copied

`version.json` records the size and sha256 of the catalog. A mirror can check each catalog after copying it. Parquet files are named by a UUID and carry their own checksums, and DuckLake records each file's size in the catalog.

Anyone can write a `version.json`, so a checksum shows the copy matches the record, not that the record came from the origin. A version signed by the origin can be checked on a mirror: see [Signatures](/spec/reading#signatures).

## Expired versions and withdrawn data

When the origin takes a version out of retention, the mirror sees `expired.json` appear and the catalog disappear on the next copy. Copy `expired.json` and delete the catalogs the origin deleted. A mirror that keeps them serves versions its origin no longer does.

An `expired.json` with `"reason": "withdrawn"` means the origin removed data that must not be published. Delete the catalogs, the data files and the assets the origin deleted, without waiting.
