---
title: Layout
description: The files of a repository, their paths, and the content of fdl.json.
---

## Paths

Paths are relative to the root, lower case, and separated by `/`. The root is what a bare Git repository is on a server, for example `s3://bucket/lake/`.

```
<root>/
  fdl.json                         repository settings
  HEAD                             the default ref: "ref: refs/heads/main"
  refs.json                        every ref and the version it holds, for readers that cannot list
  refs/
    heads/<name>                   branches. They move; main is what readers see by default
    tags/<name>                    tags. Never moved once made
    requests/<number>              for the host's own use, such as pending approvals (optional)
  versions/<version ID>/
    version.json                   the version record, written last
    version.json.sig               a signature of the record (optional)
    ducklake.duckdb                the frozen catalog
    catalogs/<format>.duckdb       the catalog in another DuckLake format (optional)
    expired.json                   the past-retention mark (only once expired)
    <others>                       files a host adds, such as descriptions and previews
  <data_dir><schema>/<table>/ducklake-<uuid>.parquet   data files (shared, immutable)
  sources/<sha256>                 source files (optional, may be private)
```

`HEAD` and everything under `refs/` have no extension, as in Git. Their content is UTF-8 text.

The root must not hold a DuckLake catalog outside `versions/`, and must never hold a working catalog. Either would give readers a state that was never published.

Files with other names may sit at the root. Readers must ignore them.

## fdl.json

```json
{
  "fdl": "0.1",
  "id": "5f0c8a52-6d3e-4f8e-9b1a-2c7d4e9f0a13",
  "data_dir": "data/",
  "signers": ["alice@example.org ssh-ed25519 AAAAC3…"],
  "catalog_formats": [
    { "ducklake": "1.1" },
    { "ducklake": "1.0", "until": "2027-03-31" }
  ]
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `fdl` | must | The version of this specification. A reader must not read a major version it does not know |
| `id` | should | A UUID written when the repository is started and never changed |
| `data_dir` | may | Where data files are, relative to the root, ending in `/`. Default `data/` |
| `catalog_formats` | may | The DuckLake formats published for new versions, and until when |
| `signers` | may | Public keys that sign versions, as lines of an SSH `allowed_signers` file |

`fdl.json` is created with `If-None-Match: *`, so starting a repository twice leaves the first one's settings in place.

### id

The `id` lets a copy of the repository tell that it holds the same one. Mirrors keep it as it is. A host may write its own ID for the dataset here when it starts the repository. A repository without an `id` is still read.

### data_dir

The default is `data/`. A repository whose data files already sit under another prefix declares that prefix here instead of moving them. Every data file a catalog names must be under `data_dir`.

### signers

The keys the repository's writers sign versions with (see [Reading](/spec/reading#signatures)). A reader must not trust a key because it is listed here: a mirror can change `fdl.json` too. The list tells a reader which keys to expect, and the reader gets the keys it trusts from the origin by other means.

How long versions are kept is not declared here. It is the host's decision.

### catalog_formats

Each entry names a DuckLake format and, optionally, the last day it is written for new versions (`until`, an ISO 8601 date). Without the field, versions carry only `ducklake.duckdb`. See [Versions](/spec/versions#catalogs-in-other-formats).
