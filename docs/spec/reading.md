---
title: Reading
description: How a reader resolves a ref, checks a version and attaches its catalog, and what may be cached.
---

## Steps

A reader that does not know which ref to ask for may read `refs.json` to find the names (see [Refs](/spec/refs#refsjson)).

1. Read `HEAD`, or the ref asked for. If the content starts with `ref: `, read the ref it names
2. Read `versions/<ID>/version.json` for the ID found. Check that its `id` matches and that the reader knows its `fdl` major version
3. If `versions/<ID>/expired.json` exists, stop: the version is past retention or withdrawn
4. Open `versions/<ID>/ducklake.duckdb`, or a `catalogs/` file the reader can open, read-only

```sql
ATTACH 'ducklake:https://example.org/lake/versions/019a3c4e-…/ducklake.duckdb' AS lake (
  DATA_PATH 'https://example.org/lake/data/', OVERRIDE_DATA_PATH true, READ_ONLY
);
```

## Signatures

A version may carry `versions/<ID>/version.json.sig`: an SSH signature of the bytes of `version.json`, made with `ssh-keygen -Y sign -n fdl`. The signature covers the record, and the record covers the catalog through `catalog.sha256`.

```bash
ssh-keygen -Y verify -f allowed_signers -I alice@example.org -n fdl \
  -s version.json.sig < version.json
```

- Signing is optional. A reader that does not check signatures reads a signed version as any other
- A reader that checks verifies the signature against keys it trusts, gets the catalog and checks its sha256 against `catalog.sha256`
- Keys come from the origin, not from the repository being read. `signers` in `fdl.json` only says which keys to expect

## Data path

The catalog's `data_path` is the absolute URL of the data folder where the writer published. A reader must pass `DATA_PATH '<root><data_dir>'` and `OVERRIDE_DATA_PATH true`, where `<root>` is wherever it reads the repository from. The statement is then the same at the origin and at a mirror, and a repository copied to another host is read without changing its catalogs.

## Caching

- A version's files never change except as described in [Versions](/spec/versions), so its catalog may be cached forever
- `HEAD`, `refs/` and `refs.json` must not be cached
- `expired.json` may appear later. A cache of a version folder should be dropped when it does

Over HTTP, `HEAD` and `refs/` are served as `Content-Type: text/plain; charset=utf-8` with `Cache-Control: no-store`.

## A catalog at a fixed URL

A host may serve the catalog of the version `HEAD` holds at `<root>/ducklake.duckdb`, for readers that do not know FDL. It must then send the version ID in an `FDL-Version` response header. This catalog is served, not stored: the store holds no catalog outside `versions/`.
