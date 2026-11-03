---
title: Command line
description: The fdl commands, their options and when they refuse.
---

`<remote>` is a repository: `s3://bucket/prefix/`, `https://host/path/` or a folder. Credentials and the endpoint for S3 come from boto3's usual places (`AWS_*` environment variables, `AWS_ENDPOINT_URL` for R2 and other S3-compatible stores). A repository over HTTP takes a token from `FDL_TOKEN` or a credential helper (see [Tokens for HTTP](#tokens-for-http)).

## Tokens for HTTP

fdl keeps no tokens and has no login. For a repository over HTTP it sends, as `Authorization: Bearer`:

1. `FDL_TOKEN`, when set (for CI)
2. Otherwise what the credential helper set for the repository's URL in fdl's config answers. With no helper set, fdl sends no token

The config is `~/.config/fdl/config.toml` (`$XDG_CONFIG_HOME/fdl/config.toml`, or the file `FDL_CONFIG` names). A helper is set per URL prefix; the one with the longest matching path wins:

```toml
[credential."https://data.example.org"]
helper = "example-cli fdl-credential"
```

fdl runs `<helper> get`, writes `protocol=`, `host=` and `path=` lines and a blank line to its stdin, and reads the `password=` line it prints. A helper that prints nothing, or exits with an error, means no token. The host's own tool stores the token and writes its helper here.

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

For a repository over HTTP, new data files are written to `<working.sqlite>.data/` and uploaded by the commit. Files already in the repository are read over HTTP where they are.

Refused when the version is past retention.

## fdl commit

```bash
fdl commit <working.sqlite> -m "What changed" --author alice [--dataset dataset.json] [--allow-empty]
```

Freezes the working catalog into a new version whose parent is the version it was checked out at, uploads it, and moves the branch on condition. It prints the branch, the new version ID and its `seq`.

Every version is described by a `dataset.json` (see [Versions](/spec/versions#datasetjson)), read from `--dataset` or else from `dataset.json` or `dataset.yml` in the current directory. Tables described in `*.table.yml` (or `*.table.json`) files below that folder are added to it; hidden folders are skipped. The command checks it against the catalog and fills in the column types, row counts and catalog comments.

When the version would hold what its parent holds (see [When nothing changed](/spec/writing#when-nothing-changed)), it prints `Nothing to commit`, leaves the branch where it is and exits 0. A rebuild that writes the same rows to new files counts as unchanged. `--allow-empty` makes the version anyway. If the rows cannot be read to tell, it warns and makes the version.

Refused when:

- there is no `dataset.json`, or it describes a table or column the catalog lacks, or has keys the format does not define
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
