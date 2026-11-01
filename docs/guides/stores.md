---
title: Stores
description: Using a local folder, S3, R2 or another S3-compatible store as a repository, and what credentials the fdl command reads.
---

A repository's URL picks the store. `s3://bucket/prefix/` is S3 or anything that speaks its API. Anything else is a folder on disk.

## A folder

```bash
fdl init ./towns/
```

A folder is the quickest way to try FDL, and a good way to keep a repository next to the code that builds it. The folder store checks a ref's content before replacing it, but it does not lock the file, so two writers pushing at the same moment can both pass the check. Use a folder for one writer at a time, and object storage when several people or jobs push.

## S3

```bash
pip install 'fdl-cli[s3]'
fdl init s3://my-bucket/towns/
```

Credentials come from boto3's usual places: the `AWS_*` environment variables, `~/.aws/credentials`, or an instance role. The writer needs `GetObject`, `PutObject` and `ListBucket` on the prefix. `DeleteObject` is needed only by maintenance (`fdl expire`).

S3 supports `If-None-Match: *` and `If-Match` on `PutObject`, which is what moving a ref needs.

## R2 and other S3-compatible stores

Point boto3 at the store's endpoint:

```bash
export AWS_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
export AWS_ACCESS_KEY_ID=…
export AWS_SECRET_ACCESS_KEY=…
fdl init s3://my-bucket/towns/
```

Before writing to a store other than S3 or R2, check that it refuses a `PutObject` with `If-Match` when the ETag has changed. A store that ignores the header looks like it works and silently loses versions when two writers push at once.

## DuckDB's credentials

`fdl` and DuckDB read credentials separately. The writer's DuckDB session writes Parquet to the store, so it needs a secret too:

```sql
CREATE SECRET (TYPE s3, PROVIDER credential_chain);
```

For R2, add `ENDPOINT '<account>.r2.cloudflarestorage.com'` and `URL_STYLE 'path'`, or use `TYPE r2` with `ACCOUNT_ID`.

## Another store

The Python package works with any object that has `put`, `put_file`, `get`, `head`, `list` and `delete` with the conditions described in [Python API](/reference/python#stores). Pass it to the functions in `fdl.repo` in place of a built-in store.
