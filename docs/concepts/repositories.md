---
title: Repositories
description: What a repository is, what it needs from the store it lives in, and what it leaves to the host.
---

A repository is everything under one prefix of a store: `s3://bucket/lake/` on S3, R2 or another S3-compatible store, or a folder on disk. It is what a bare Git repository is on a server. There is no index file and no process that owns it. Every reader and writer works on the same files, under the rules in the [specification](/spec).

## What a store has to do

| Operation | Used by | For |
| --- | --- | --- |
| read an object | readers and writers | refs, `version.json`, catalogs, data files |
| write an object | writers | data files, catalogs, `version.json` |
| write on condition | writers | refs: create only if absent, replace only if unchanged |
| list a prefix | maintenance | finding data files that no version names |
| delete | maintenance | catalogs of versions past retention, and unused data files |

Conditional writes are what make two writers safe. A ref is created with `If-None-Match: *` and moved with `If-Match: <the ETag that was read>`. If someone else moved it in between, the store refuses the write and nothing changes. S3 and R2 support this. The folder store checks the condition without a lock, so it suits one writer at a time (see [Stores](/guides/stores)). A store without conditional writes can still be read from, but must not be pushed to: the last writer would silently replace someone else's version.

Readers only read. A static HTTP server in front of the repository is enough for them.

## What is in a repository

```
<root>/
  fdl.json                 settings: format version, ID, where data files are
  HEAD                     the default branch
  refs/heads/<name>        branches
  refs/tags/<name>         tags
  versions/<ID>/           one folder per version
  data/                    Parquet files, shared by all versions
  assets/<sha256>          files a version points at, such as PDFs, images and its README
```

The repository holds no catalog outside a version folder, and never a working catalog. Either would hand readers a state that was never published.

`fdl.json` carries an `id`, a UUID written once by `fdl init`. Copies of the repository on other hosts keep the same `id`, so a tool can tell that two URLs hold the same repository. A service that hosts repositories may write its own ID for the dataset there.

## What FDL leaves to the host

FDL describes files. It does not say who may read them, who may move `main`, or what a URL looks like. Those belong to whoever hosts the repository:

- access control. Everything in one repository is readable by the same people. To publish part of a dataset, make a second repository (see [Public and private data](/guides/public-and-private))
- approval. A host may keep writers from moving refs and move them itself after a review (see [Building a host](/guides/building-a-host))
- authentication, URL shapes and how long versions are kept

Nothing in the format names a host or a service. A repository can move from one host to another by copying its files.
