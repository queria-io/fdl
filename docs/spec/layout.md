---
title: Layout
description: The files of a dataset, their paths, and the content of fdl.json.
---

## Paths

Paths are relative to the root, lower case, and separated by `/`. The root is what a bare Git repository is on a server, for example `s3://bucket/lake/`.

```
<root>/
  fdl.json                         dataset settings
  HEAD                             the default ref: "ref: refs/heads/main"
  refs.json                        every ref and the version it holds, for readers that cannot list
  refs/
    heads/<name>                   branches. They move; main is what readers see by default
    tags/<name>                    tags. Never moved once made
    requests/<number>              for the host's own use, such as pending approvals (optional)
  versions/<version ID>/
    version.json                   the version record, written last
    metadata.db                    the frozen metadata
    tables.json                    the tables and views, their data files and columns, and FDL's declarations
    profiles/<schema>/<table>.json a summary of each table, measured from the data (optional)
    expired.json                   the past-retention mark (only once expired)
    <others>                       files a host adds
  <data_dir><schema>/<table>/ducklake-<uuid>.parquet   data files (shared, immutable)
  assets/<sha256>                  files the rows of a table point at (shared, immutable)
```

`HEAD` and everything under `refs/` have no extension, as in Git. Their content is UTF-8 text.

The root must not hold a DuckLake metadata file outside `versions/`, and must never hold a working metadata file. Either would give readers a state that was never published.

Files with other names may sit at the root. Readers must ignore them.

A dataset's root must not be under another dataset's root. Deleting the outer dataset's shared objects would take the inner one's files for objects no version reaches.

## fdl.json

```json
{
  "fdl": "0.1",
  "data_dir": "tables/"
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `fdl` | must | The version of this specification. A reader must not read a major version it does not know |
| `data_dir` | may | Where data files are, relative to the root, ending in `/`. Default `tables/` |

`fdl.json` is created with `If-None-Match: *`, so starting a dataset twice leaves the first one's settings in place.

How long versions are kept is not declared here. It is the host's decision.

### data_dir

The default is `tables/`. A dataset whose data files already sit under another prefix declares that prefix here instead of moving them. Every data file a metadata file names must be under `data_dir`.

A `data_dir` is a folder under the root, written as one or more segments, each followed by `/`:

- Each segment is made of lower-case ASCII letters, digits, `_`, `-` and `.`, and is not dots alone
- The first segment is not `versions`, `refs` or `assets`

Readers and writers use the value as written. A dataset whose `data_dir` breaks these rules, or is not a string, is refused, never corrected (by adding a missing `/`, say): two implementations that corrected it differently would read and write different files. A host may reserve other top-level folders for its own files and refuse a `data_dir` under them. [`cases/data-dir.json`](https://github.com/queria-io/fdl/blob/main/cases/data-dir.json) lists values and what each reads as; every implementation that reads `fdl.json` runs them.

#### Changing data_dir

A host may move the data files of a dataset that already has versions. Metadata files name data files relative to `data_dir`, so the versions are not rewritten. The host does it in this order:

1. Copy every data file a live version reaches to the same path under the new `data_dir`
2. Check that each of them is there
3. Write `fdl.json` with the new `data_dir`

Until step 3, readers read the old place and find every file there. After it, they read the new place, which already holds every file. The host keeps the old place until no reader can still be using the `data_dir` it read before the change: at least the grace period, and longer if a reader may keep it longer. Then the host deletes the old place (see [Data files](/spec/data-files#files-outside-data_dir)).

A working metadata file checked out before step 3 writes its new data files to the old place, where no version will read them. A writer remembers the `data_dir` it checked out with and refuses to commit or push when `fdl.json` names another one; the build checks out again. A host may also refuse such a push.
