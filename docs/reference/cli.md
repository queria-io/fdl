---
title: Command line
description: The fdl commands, their options and when they refuse.
---

`<remote>` is a repository: `s3://bucket/prefix/` or a folder. Credentials and the endpoint for S3 come from boto3's usual places (`AWS_*` environment variables, `AWS_ENDPOINT_URL` for R2 and other S3-compatible stores).

## fdl init

```bash
fdl init <remote> [--data-dir data/]
```

Writes `fdl.json`, with a new `id`, and `HEAD`. Running it on an existing repository changes nothing and prints its settings.

## fdl checkout

```bash
fdl checkout <remote> <working.sqlite> [--ref refs/heads/main]
```

Makes a working catalog (DuckLake in SQLite) from the catalog of the version the branch holds, or an empty one before the first version. Beside it, `<working.sqlite>.fdl.json` remembers the repository, the branch and the version it was made from. It prints the `ATTACH` statement to write with.

Refused when the version is past retention.

## fdl commit

```bash
fdl commit <working.sqlite> -m "What changed" --author alice
```

Freezes the working catalog into a new version whose parent is the version it was checked out at, uploads it, and moves the branch on condition. It prints the branch, the new version ID and its `seq`.

Refused when:

- the branch has moved since the checkout. Check out again and redo the work
- the working catalog holds inlined rows
- a data file the catalog names is not in the repository
- a data file is recorded with an absolute path outside the data directory

## fdl log

```bash
fdl log <remote> [--ref HEAD]
```

Versions from the one the ref holds back to the first, by first parent. Each shows `seq`, ID, time, author and message. Versions past retention are marked `(expired)`.

## fdl url

```bash
fdl url <remote> [--ref HEAD]
```

Prints the URL of the catalog of the version the ref holds, to `ATTACH` read-only.

## fdl expire

```bash
fdl expire <remote> [--keep 1] [--since 2026-10-01T00:00:00Z] [--ref refs/heads/main]
```

Takes versions behind the newest `--keep` on the branch's first-parent line out of retention: puts `expired.json` and deletes their catalogs. Versions made at or after `--since`, and any a ref points at, are kept. Data files are not deleted.
