---
title: Public and private data
description: Publishing some tables and keeping the rest private, with two datasets.
---

Everything in one dataset is readable by the same people. A host decides who can read a dataset, not who can read a table in it.

To publish some tables and keep others private, use two datasets:

```
s3://private/towns/        every table. Only the team can read it
s3://public/towns/         the tables chosen for publication
```

## Making a public version

A version of the public dataset is made from a version of the private one:

1. Clone the public dataset
2. Copy the chosen tables from the private version into the working metadata. The Parquet files are copied into the public dataset's data folder
3. Commit, and name the private version in trailers at the end of the message, as Git does

```
Publish the 2025 figures

Made-From: 019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c
Made-From-Sha256: 9f2c…
```

`Made-From-Sha256` is the private version's metadata checksum. Readers of the public dataset see that the version was made from another one, without being able to open it.

## Why not one dataset

Putting a public metadata file next to a private one in a single dataset looks simpler, but:

- the server would have to decide file by file who can read which Parquet file, and get it right for every file of every version
- the private metadata's snapshot history names the private tables, their columns and their commit messages. A public metadata file copied out of it would carry that history unless it was rewritten

Two datasets keep the rule simple. A reader can read everything in a dataset or nothing in it.
