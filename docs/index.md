---
title: FDL
sidebar:
  label: Introduction
description: Versions of a DuckLake catalog on object storage, kept the way Git keeps commits.
---

FDL keeps the history of a [DuckLake](https://ducklake.select/) catalog in a bucket or a folder. Each version is a frozen copy of the catalog with a record of its parent, its author and a message. Refs such as `refs/heads/main` say which version readers get. The Parquet files are shared between versions and never rewritten.

```
s3://my-bucket/towns/
  fdl.json
  HEAD                              ref: refs/heads/main
  refs/heads/main                   019a3c4e-…
  versions/019a3c4e-…/
    version.json                    parents, author, message, sha256
    ducklake.duckdb                 the frozen catalog
  data/main/towns/ducklake-….parquet
```

A reader needs nothing but DuckDB: it reads `refs/heads/main`, then attaches the catalog of that version read-only, over HTTP or S3. A writer needs a store that can read, write and write on condition. There is no server.

## What FDL is for

DuckLake already keeps every change as a snapshot. Snapshots are made on every write, so they are the right unit for a database and the wrong one for people. FDL adds the unit people use when they publish data: "this is the 2025 release, made by Alice, built on the 2024 one". It gives that unit a stable ID, a place in a history, and a name that a reader can follow.

FDL is meant for data that is published, read by many and written by few:

- open data and statistics that are released in versions
- a dataset maintained by a team, where a change has to be reviewed before readers see it
- copies of the same dataset on several hosts (mirrors)

It is not a transactional database. Two people who push at once are settled by the store: the first push wins and the second is refused. The second builds again on the new version, or works on a branch of its own and merges it, rows matched by the table's primary key.

## FDL and Git

FDL keeps a catalog's versions the way Git keeps a project's commits. If you know Git, most of FDL maps onto it:

| Git | FDL |
| --- | --- |
| A bare repository on a server | A repository: a prefix of a bucket, or a folder |
| Commit | Version (`versions/<ID>/version.json`) |
| Tree | The frozen catalog (`ducklake.duckdb`) |
| Blob | Data file (Parquet), asset |
| Branch, tag, `HEAD` | `refs/heads/`, `refs/tags/`, `HEAD` |
| Working tree | Working catalog (SQLite on your disk) |
| `git init --bare` | `fdl init` |
| `git commit`, `git push` | `fdl commit`, `fdl push` |
| `git log`, `git tag` | `fdl log`, `fdl tag` |
| `git branch`, `git merge` | `fdl branch`, `fdl merge` |

A version has one parent, or two when it merges another branch. [From write to read](/internals/from-write-to-read) follows the files through a commit, step by step.

FDL is in beta. [Stability](/stability) says what stays the same across releases.

## How the documentation is organized

- [Getting started](/getting-started) makes a repository, writes a first version and reads it back
- [Concepts](/concepts/repositories) explains repositories, versions, refs and working catalogs
- [How it works](/internals/inside-a-repository) follows the files in a repository through a few commits
- [Guides](/guides/stores) cover stores, serving over HTTP, mirrors and building a host
- The [specification](/spec) is the normative description of the format. Tools are written against it
- The [reference](/reference/cli) lists the commands and the Python API
- [Stability](/stability) says what a repository written today can rely on

The `fdl` command and the `fdl` Python package are the reference implementation. They are published on PyPI as `fdl-cli` under the MIT license.

```bash
pip install 'fdl-cli[s3]'
```
