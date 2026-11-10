---
title: Stability
sidebar:
  order: 7
description: What stays the same across releases of FDL and fdl-cli, and what each depends on.
---

FDL is in beta. This page says what a repository written today can rely on, and how releases of `fdl-cli` change.

## The format

A repository written for FDL 0.1 stays readable by later releases of `fdl`. The `fdl` field of `fdl.json` and `version.json` names the specification a file was written for, and a reader refuses a major version it does not know.

These keep their paths and their meaning:

- `fdl.json`, `HEAD`, `refs/` and `refs.json`
- `version.json` and its required fields
- the frozen catalog, `ducklake.duckdb`
- `dataset.json`: `name`, `title`, `tables` and each table's `fields`
- `expired.json`
- data files and `assets/<sha256>`

The optional files of a version, `files.json`, `tree.json` and `profiles/`, are made from the catalog and the data, and a reader works without them. They may gain keys in later releases. A reader ignores keys it does not know, in every file.

## fdl-cli

`fdl-cli` is released as `0.x`. A minor release (0.21 to 0.22) may change a command, an option or a Python function, and its changelog says so under breaking changes. A patch release (0.21.0 to 0.21.1) only fixes. In automation, pin the minor release:

```bash
pip install 'fdl-cli[s3]>=0.21,<0.22'
```

## What it runs on

- Python 3.10 or later
- DuckDB 1.5.2 or later, 2.x included (on Python 3.11 or later), with the `ducklake` extension. Whichever DuckDB writes them, the catalogs `fdl` writes are DuckLake format 1.0 in DuckDB storage v1.5.0, so DuckDB 1.5 and later read them

## Reporting a problem

Open an issue at [github.com/queria-io/fdl](https://github.com/queria-io/fdl/issues). Report a vulnerability privately, as [SECURITY.md](https://github.com/queria-io/fdl/blob/main/SECURITY.md) says.
