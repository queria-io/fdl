# fdl

[![PyPI](https://img.shields.io/pypi/v/fdl-cli)](https://pypi.org/project/fdl-cli/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://pypi.org/project/fdl-cli/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Versions of a [DuckLake](https://ducklake.select/) catalog on object storage, kept the way Git keeps commits.

FDL is a storage layout and a tool for it. Each version is a frozen catalog with a record of its parent, author and message; refs such as `refs/heads/main` say which version readers get. Data files are shared between versions and never rewritten. All it needs is a store that can read, write and write on condition: a folder, S3, R2 or anything S3-compatible. There is no server.

```
<root>/
  fdl.json
  HEAD                              ref: refs/heads/main
  refs/heads/main                   019a3c4e-…
  versions/019a3c4e-…/
    version.json                    parents, author, message, sha256
    ducklake.duckdb                 the frozen catalog
  data/main/towns/ducklake-….parquet
```

The documentation and the specification are at [fdl.queria.io](https://fdl.queria.io).

## Quick start

```bash
pip install 'fdl-cli[s3]'

fdl init s3://my-bucket/towns/
fdl checkout s3://my-bucket/towns/ work/lake.sqlite
```

The package is `fdl-cli` on PyPI. It installs the `fdl` command and the `fdl` Python package (`import fdl`).

`fdl init` makes the repository in the store itself, as `git init --bare` does on a server. Your own copy to write to is the working catalog that `checkout` makes, a SQLite file.

Write to the working catalog with DuckDB, attached the way `checkout` prints:

```sql
ATTACH 'ducklake:work/lake.sqlite' AS lake (
  DATA_PATH 's3://my-bucket/towns/data/', OVERRIDE_DATA_PATH true,
  META_TYPE 'sqlite', DATA_INLINING_ROW_LIMIT 0, META_JOURNAL_MODE 'WAL', BUSY_TIMEOUT 5000
);
CREATE TABLE lake.main.towns AS SELECT * FROM 'towns.csv';
```

Rows have to go to Parquet, not into the catalog (`DATA_INLINING_ROW_LIMIT 0`): a version is read over HTTP, and inlined rows would not be in it.

Describe what the version holds in `dataset.yml`, next to where you run `fdl commit`. Only `name` and `title` are required; `fdl commit` fills in each table's columns and row count from the catalog:

```yaml
name: towns
title: Towns
```

Then make a version and move `main` to it:

```bash
fdl commit work/lake.sqlite -m "First towns" --author alice
fdl log s3://my-bucket/towns/
```

Anyone who can read the bucket can attach it:

```bash
fdl url s3://my-bucket/towns/
```

```sql
ATTACH 'ducklake:s3://my-bucket/towns/versions/019a3c4e-…/ducklake.duckdb' AS towns (
  DATA_PATH 's3://my-bucket/towns/data/', OVERRIDE_DATA_PATH true, READ_ONLY
);
```

Credentials and the endpoint come from boto3's usual places (`AWS_*` environment variables, `AWS_ENDPOINT_URL` for R2 and other S3-compatible stores). A folder works as a repository too: pass its path instead of an `s3://` URL.

## Commands

| Command | What it does |
| --- | --- |
| `fdl init <remote>` | Start a repository (`fdl.json` and `HEAD`) |
| `fdl checkout <remote> <working>` | Make a working catalog from the version `main` holds |
| `fdl commit <working> -m … --author …` | Freeze the working catalog into a version and push it |
| `fdl log <remote>` | List versions from `main` back to the first |
| `fdl status <working>` | What the working catalog changed since the checkout |
| `fdl diff <remote> [version]` | What a version changed since its parent: tables, rows, columns, data files, descriptions |
| `fdl show <remote> [version]` | A version's record, its tables and its changes |
| `fdl url <remote> [--attach]` | Print the catalog URL of the version `main` holds, or the whole `ATTACH` statement |
| `fdl tag <remote> [name] [version]` | List tags, tag a version, or delete a tag (`-d`) |
| `fdl expire <remote> --keep N` | Mark older versions as past retention and delete their catalogs |
| `fdl withdraw <remote> <version>…` | Remove versions that hold data that must not be published |
| `fdl assets <folder>` | List files for an asset column (path, sha256, bytes, media type) |

A push that is not built on what `main` holds now is refused: check out again and redo the work.

## Python

```python
from fdl import repo
from fdl.stores import open_store

store = open_store("s3://my-bucket/towns/")
for version in repo.log(store):
    print(version.seq, version.id, version.message)
```

`fdl.repo` has the operations without a terminal (`init`, `checkout`, `commit`, `push`, `log`, `expire`, `prune`), for tools and services that build on FDL.

## Earlier releases

Releases up to 0.11 were a different tool for managing a single frozen catalog, published as `frozen-ducklake`. Their code is at the [`legacy`](https://github.com/queria-io/fdl/tree/legacy) branch.

## License

MIT
