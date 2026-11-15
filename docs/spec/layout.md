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
    metadata.db                    the frozen metadata
    dataset.json                   what the version holds, described
    files.json                     each table's data files, indexed from the metadata (optional)
    tree.json                      the other files of the version, such as a README, by path (optional)
    profiles/<schema>/<table>.json a summary of each table, measured from the data (optional)
    metadata/<format>.db           the metadata in another DuckLake format (optional)
    expired.json                   the past-retention mark (only once expired)
    <others>                       files a host adds
  <data_dir><schema>/<table>/ducklake-<uuid>.parquet   data files (shared, immutable)
  assets/<sha256>                  files the rows of a table or a version's tree.json point at (shared, immutable)
```

`HEAD` and everything under `refs/` have no extension, as in Git. Their content is UTF-8 text.

The root must not hold a DuckLake metadata file outside `versions/`, and must never hold a working metadata file. Either would give readers a state that was never published.

Files with other names may sit at the root. Readers must ignore them.

A repository's root must not be under another repository's root. Deleting the outer repository's shared objects would take the inner one's files for objects no version reaches.

## fdl.json

```json
{
  "fdl": "0.1",
  "id": "5f0c8a52-6d3e-4f8e-9b1a-2c7d4e9f0a13",
  "data_dir": "data/",
  "signers": ["alice@example.org ssh-ed25519 AAAAC3…"],
  "metadata_formats": [
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
| `metadata_formats` | may | The DuckLake formats published for new versions, and until when |
| `signers` | may | Public keys that sign versions, as lines of an SSH `allowed_signers` file |

`fdl.json` is created with `If-None-Match: *`, so starting a repository twice leaves the first one's settings in place.

### id

The `id` lets a copy of the repository tell that it holds the same one. Mirrors keep it as it is. A host may write its own ID for the dataset here when it starts the repository. A repository without an `id` is still read.

### data_dir

The default is `data/`. A repository whose data files already sit under another prefix declares that prefix here instead of moving them. Every data file a metadata file names must be under `data_dir`.

A `data_dir` is a folder under the root, written as one or more segments, each followed by `/`:

- Each segment is made of lower-case ASCII letters, digits, `_`, `-` and `.`, and is not dots alone
- The first segment is not `versions`, `refs` or `assets`

Readers and writers use the value as written. A repository whose `data_dir` breaks these rules, or is not a string, is refused, never corrected (by adding a missing `/`, say): two implementations that corrected it differently would read and write different files. A host may reserve other top-level folders for its own files and refuse a `data_dir` under them. [`cases/data-dir.json`](https://github.com/queria-io/fdl/blob/main/cases/data-dir.json) lists values and what each reads as; every implementation that reads `fdl.json` runs them.

#### Changing data_dir

A host may move the data files of a repository that already has versions. Metadata files name data files relative to `data_dir`, so the versions are not rewritten. The host does it in this order:

1. Copy every data file a live version reaches to the same path under the new `data_dir`
2. Check that each of them is there
3. Write `fdl.json` with the new `data_dir`

Until step 3, readers read the old place and find every file there. After it, they read the new place, which already holds every file. The host keeps the old place until no reader can still be using the `data_dir` it read before the change: at least the grace period, and longer if a reader may keep it longer. Then the host deletes the old place (see [Data files](/spec/data-files#files-outside-data_dir)).

A working metadata file checked out before step 3 writes its new data files to the old place, where no version will read them. A writer remembers the `data_dir` it checked out with and refuses to commit or push when `fdl.json` names another one; the build checks out again. A host may also refuse such a push.

### signers

The keys the repository's writers sign versions with (see [Reading](/spec/reading#signatures)). A reader must not trust a key because it is listed here: a mirror can change `fdl.json` too. The list tells a reader which keys to expect, and the reader gets the keys it trusts from the origin by other means.

How long versions are kept is not declared here. It is the host's decision.

### metadata_formats

Each entry names a DuckLake format and, optionally, the last day it is written for new versions (`until`, an ISO 8601 date). Without the field, versions carry only `metadata.db`. See [Versions](/spec/versions#metadata-in-other-formats).
