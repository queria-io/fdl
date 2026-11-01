---
title: Public and private data
description: Publishing some tables of a dataset and keeping the rest private, with two repositories.
---

Everything in one repository is readable by the same people. A host decides who can read a repository, not who can read a table in it.

To publish some tables and keep others private, use two repositories:

```
s3://private/towns/        every table. Only the team can read it
s3://public/towns/         the tables chosen for publication
```

## Making a public version

A version of the public repository is made from a version of the private one:

1. Check out the public repository
2. Copy the chosen tables from the private version into the working catalog. The Parquet files are copied into the public repository's data folder
3. Commit, and name the private version in `inputs`

```json
"inputs": [
  { "source_id": "fdl:019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c", "sha256": "9f2c…" }
]
```

`sha256` is the private version's catalog checksum. Readers of the public repository see that the version was made from another one, without being able to open it.

## Why not one repository

Putting a public catalog next to a private one in a single repository looks simpler, but:

- the server would have to decide file by file who can read which Parquet file, and get it right for every file of every version
- the private catalog's snapshot history names the private tables, their columns and their commit messages. A public catalog copied out of it would carry that history unless it was rewritten

Two repositories keep the rule simple. A reader can read everything in a repository or nothing in it.
