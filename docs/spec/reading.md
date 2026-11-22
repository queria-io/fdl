---
title: Reading
description: How a reader resolves a ref, checks a version and attaches its metadata, reads an earlier version, and what may be cached.
---

## Steps

A reader that does not know which ref to ask for may read `refs.json` to find the names (see [Refs](/spec/refs#refsjson)).

1. Read `HEAD`, or the ref asked for. If the content starts with `ref: `, read the ref it names
2. Read `versions/<ID>/version.json` for the ID found. Check that its `id` matches and that the reader knows its `fdl` major version
3. If `versions/<ID>/expired.json` exists, stop: the version is past retention or withdrawn
4. Open the file `metadata.path` names in `versions/<ID>/` (`metadata.db`), or a `metadata/` file the reader can open, read-only

```sql
ATTACH 'ducklake:https://example.org/lake/versions/019a3c4e-…/metadata.db' AS lake (
  DATA_PATH 'https://example.org/lake/tables/', OVERRIDE_DATA_PATH true, READ_ONLY
);
```

## An earlier version

A reader reads an earlier version by the same steps, with that version's ID: from a tag, from `parents`, or from a list of versions. Each version's metadata holds the dataset at its own snapshot, so going back to a version is opening its metadata, not travelling back in time inside another one.

```sql
ATTACH 'ducklake:https://example.org/lake/versions/019a2f10-…/metadata.db' AS lake_v11 (
  DATA_PATH 'https://example.org/lake/tables/', OVERRIDE_DATA_PATH true, READ_ONLY
);
```

- A reader must not read an earlier version by `AT (VERSION => n)` or `ducklake_snapshots()` in the metadata of a later version. A metadata file holds only its own snapshot ([The frozen metadata](/spec/versions#the-frozen-metadata)). Versions written by fdl-cli 0.31 and earlier also hold earlier snapshots, but a reader must not rely on them
- To compare two versions, a reader attaches both metadata files under different names
- A version past retention or withdrawn has no metadata to open (step 3)

## Views that read other datasets

A view's SQL may read tables of other datasets, under the names `datasets` of [tables.json](/spec/versions#tablesjson) gives. Before it reads such a view, a reader attaches each entry under its name, by the steps above: the version `version` names, or the version `main` holds when it has none.

```sql
ATTACH 'ducklake:https://example.org/census/versions/019a2f10-…/metadata.db' AS census (
  DATA_PATH 'https://example.org/census/tables/', OVERRIDE_DATA_PATH true, READ_ONLY
);
```

A reader that does not read the view need not attach them.

## Signatures

A version may carry `versions/<ID>/version.json.sig`: an SSH signature of the bytes of `version.json`, made with `ssh-keygen -Y sign -n fdl`. The signature covers the record, and the record covers the metadata through `metadata.sha256`.

```bash
ssh-keygen -Y verify -f allowed_signers -I alice@example.org -n fdl \
  -s version.json.sig < version.json
```

- Signing is optional. A reader that does not check signatures reads a signed version as any other
- A reader that checks verifies the signature against keys it trusts, gets the metadata and checks its sha256 against `metadata.sha256`
- Keys come from the origin, not from the dataset being read. `signers` in `fdl.json` only says which keys to expect

## Data path

The metadata's `data_path` is the absolute URL of the data folder where the writer published. A reader must pass `DATA_PATH '<root><data_dir>'` and `OVERRIDE_DATA_PATH true`, where `<root>` is wherever it reads the dataset from. The statement is then the same at the origin and at a mirror, and a dataset copied to another host is read without changing its metadata files.

## Checking the metadata

A metadata file is written by whoever can write the dataset, and DuckDB reads every file it names with the reader's credentials. Before a reader attaches a metadata file with credentials that reach more than the dataset, it should check that:

- the metadata's sha256 is `metadata.sha256` in `version.json`
- every data file it names is recorded relative to the data path, with no empty, `.` or `..` segment

A metadata file that names a file by an absolute path, or outside `data_dir`, makes the reader fetch whatever it points at.

Over HTTP, a reader sends its token only to the origin of the dataset, and only over `https` unless the host is the reader's own machine. A redirect to another origin goes without the token.

## Caching

- A version's files never change except as described in [Versions](/spec/versions), so its metadata may be cached forever
- `HEAD`, `refs/` and `refs.json` must not be cached
- `expired.json` may appear later. A cache of a version folder should be dropped when it does

Over HTTP, `HEAD` and `refs/` are served as `Content-Type: text/plain; charset=utf-8` with `Cache-Control: no-store`.

## A metadata file at a fixed URL

A host may serve the metadata of the version `HEAD` holds at `<root>/metadata.db`, for readers that do not know FDL. It must then send the version ID in an `FDL-Version` response header. This metadata is served, not stored: the store holds no metadata outside `versions/`. A host that served it at `<root>/ducklake.duckdb` before may keep serving it there too.
