# Specification (fdl 0.1, draft)

FDL is a storage layout for keeping versions of a [DuckLake](https://ducklake.select/) catalog, modelled on how Git stores commits. It says which files go where in a store (S3, R2, or a folder) and how readers and writers handle them. No server is needed: the store has to read, write, and write on condition, nothing more.

This document decides:

- the layout of a repository, and the name and content of each file
- the fields of a version record (`version.json`)
- the rules for moving refs, and the order of writes
- when files may be deleted, and how a version is marked as past retention

It leaves to whoever hosts the repository:

- who can read it, and who may move `main` (approval)
- authentication, URL shapes, and retention defaults
- the content of the DuckLake catalog itself (that is DuckLake's specification)

"Must" is required; "may" is optional.

## Terms

| Term | Meaning |
| --- | --- |
| repository | Everything under one prefix (the root below) |
| version | One frozen catalog and its record. Never changed once written |
| ref | A name that points at a version. It holds a version ID and nothing else |
| working catalog | The writer's own DuckLake catalog, in SQLite. Never put in the repository |
| data file | A Parquet file DuckLake writes. Shared between versions |

A DuckLake snapshot is to a version what a small commit on a work branch is to the squash commit that lands on `main`. Snapshots are made on every write and stay internal; the unit shown to people is the version.

## Layout

Paths are relative to the root, lower case, separated by `/`. The root is what a bare Git repository on a server is (for example `s3://bucket/lake/`).

```
<root>/
  fdl.json                         repository settings (format version, data location)
  HEAD                             the default ref: "ref: refs/heads/main"
  refs/
    heads/<name>                   branches. They move; main is what readers see by default
    tags/<name>                    tags. Never moved once made
    requests/<number>              for the host's own use, such as pending approvals (optional)
  versions/<version ID>/
    version.json                   the version record (written last)
    ducklake.duckdb                the frozen catalog
    catalogs/<format>.duckdb       the catalog written out in another DuckLake format (optional)
    expired.json                   the past-retention mark (only once expired)
    <others>                       files a host adds (descriptions, previews)
  data/<schema>/<table>/ducklake-<uuid>.parquet   data files (shared, immutable)
  sources/<sha256>                 source files (optional, may be private)
```

`HEAD` and everything under `refs/` have no extension, as in Git. Their content is UTF-8 text.

### fdl.json

```json
{
  "fdl": "0.1",
  "data_dir": "data/",
  "catalog_formats": [
    { "ducklake": "1.1" },
    { "ducklake": "1.0", "until": "2027-03-31" }
  ]
}
```

- `fdl` is the version of this specification. A reader must not read a major version it does not know
- `data_dir` is where data files are. The default is `data/`. A repository whose files already sit under another name declares it here instead of moving them
- `catalog_formats` lists the DuckLake formats published and until when (see "When the DuckLake format moves"). Without it, only `ducklake.duckdb`
- Files with other names may sit at the root. Readers ignore them
- **No catalog outside a version, and no working catalog in the repository.** Either would hand out a state that was never published

## Versions

The files under `versions/<version ID>/`. Never changed once written, except:

- adding `expired.json` and deleting the catalogs when it falls out of retention
- adding `catalogs/<format>.duckdb` later (existing files are not changed)

### Version ID

- A UUIDv7 in lower case with hyphens (for example `019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c`). They sort by creation time, and writers do not collide without coordinating
- Not a content hash: the same catalog can be written as different bytes, so a hash would not be stable. Content is checked with `catalog.sha256` in `version.json`
- The number shown to people is `seq` (the parent's seq + 1). It is not unique across branches, so it is not the ID

### version.json

```json
{
  "fdl": "0.1",
  "id": "019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c",
  "seq": 12,
  "parents": ["019a2f10-0c4d-7e88-a1b2-55c0d9e3f7aa"],
  "created_at": "2026-10-30T09:00:00Z",
  "author": { "name": "example" },
  "message": "Add the final 2025 figures",
  "catalog": {
    "path": "ducklake.duckdb",
    "storage": "duckdb-v1.5.0",
    "bytes": 4194304,
    "sha256": "9f2c…"
  },
  "ducklake": {
    "format": "1.0",
    "snapshot_id": 345,
    "snapshots": [301, 345]
  },
  "changes": [
    { "table": "main.population", "kind": "add_column", "column": "note", "breaking": false }
  ],
  "inputs": [
    {
      "source_id": "example:population-2025",
      "sha256": "1b7e…",
      "retrieved_at": "2026-10-29T03:12:00Z",
      "url": "https://example.org/population-2025.csv"
    }
  ],
  "extensions": {}
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `fdl` | must | The specification version it was written for |
| `id` | must | The same ID as the directory. A reader must not read a version where they differ |
| `seq` | must | The number shown to people. 1 for the first |
| `parents` | must | Parent version IDs. Empty for the first, usually one, two for a merge |
| `created_at` | must | UTC, ISO 8601 |
| `author` | must | Who made it. `name` is required, `email` and others optional |
| `message` | must | What changed (the commit message) |
| `catalog` | must | The catalog's file name, size, sha256, and the file's storage format (`storage`) |
| `ducklake` | must | The DuckLake format and the frozen snapshot. `snapshots` is the range this version adds (first and last after the parent) |
| `changes` | may | Column differences from the parent. `breaking` says whether old SQL breaks (dropped, renamed, recreated) |
| `inputs` | may | Source files used. How `source_id` is assigned is up to the writer |
| `extensions` | may | Host-specific fields, keyed by the host's domain |

The description and SQL of each snapshot stay in the catalog (DuckLake's `commit_message` and `commit_extra_info`) and are not copied into `version.json`.

There are three format versions, each for a different thing. A reader can tell from `version.json` alone whether it can open a version.

| Field | Refers to | Changes when |
| --- | --- | --- |
| `fdl` | this specification (the layout and `version.json`) | this document changes |
| `ducklake.format` | the shape of DuckLake's tables (1.0, 1.1, …) | DuckLake's specification changes |
| `catalog.storage` | the storage format of the catalog file | DuckDB's storage format changes |

### The frozen catalog

- A DuckLake catalog in DuckDB's format, holding bookkeeping rows up to `snapshot_id` and none after
- No inlined rows: all data is in data files
- Every data file it names is under `data_dir`. A writer must check them all; a host may check too
- Until DuckLake can tag snapshots, a writer must not run `expire_snapshots` on its working catalog in a way that removes the snapshot a version was frozen at

### When the DuckLake format moves

A new format changes the catalog, not the data files, and catalogs are small. So one version may carry a catalog per format, and a reader picks the file it can open rather than switching DuckDB (a browser's WASM build cannot be swapped).

```
versions/<ID>/
  ducklake.duckdb            the catalog as made (the one in version.json)
  catalogs/1.0.duckdb        written out in format 1.0
  catalogs/1.1.duckdb        written out in format 1.1
```

- Every `catalogs/<format>.duckdb` names the same data files and returns the same rows
- A reader tries the formats it can open, newest first, then falls back to `ducklake.duckdb`
- A version that uses something an older format cannot express has no catalog in that format
- A format past its `until` is not written for new versions. Past versions may keep theirs
- Data files are written so that the oldest format declared in `fdl.json` can read them

## Refs

A ref holds a version ID and a newline (UTF-8, no other whitespace).

```
refs/heads/main      019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c\n
HEAD                 ref: refs/heads/main\n
```

| Path | Moves | Use |
| --- | --- | --- |
| `HEAD` | rarely | The default branch. A reader without a ref follows HEAD |
| `refs/heads/main` | yes | The version readers normally see |
| `refs/heads/<name>` | yes | Shared work in progress |
| `refs/tags/<name>` | no | A version to pin (for example `2025-final`). May be deleted |
| `refs/requests/<number>` | as the host decides | Pending approvals and the like. Same content as any ref |

Names match `[a-z0-9][a-z0-9._-]*`, up to 128 characters, and may be split into levels with `/` (for example `refs/heads/alice/fix-2025`).

### Moving refs

- A ref is only written on condition: created with `If-None-Match: *`, moved with `If-Match: <the ETag read>`. When the condition fails, nothing is written and the writer is told
- The version a ref points at must be fully written, `version.json` included. Writes go: data files, catalog, `version.json`, ref. A writer stopped half way has changed nothing a reader sees
- Branches only move forward by default: a new version whose `parents` do not include the current one is refused. Moving back (a rollback) is allowed only when asked for explicitly
- Tags are created, never moved
- A refused writer pulls again and builds on what is there now. Merging rows is outside this specification
- A host may keep writers from writing refs and move them itself after its own checks (an approval, say)

A store without conditional writes must not be pushed to: the last writer would silently replace someone else's version. It can still be read from.

## Data files and source files

### Data files

- Named as DuckLake names them (`<schema>/<table>/ducklake-<uuid>.parquet`). Delete files go in the same place
- Never overwritten. Several versions name the same file
- A writer may place data files before making a version. Until a version names them, readers do not see them
- Compacting small files (`merge_adjacent_files`) is fine at any time: it writes new files and deletes none

### When a data file may be deleted

Only when both hold:

1. No catalog of a live version (one without `expired.json`) names it
2. It was placed longer ago than a grace period (7 days by default). The grace period protects files a writer has placed but not yet made a version of

DuckLake's own cleanup (`cleanup_old_files`, `expire_snapshots`) only sees the working catalog, so it must not be used to delete from a repository. Deleting is for a process that sees the whole repository.

### Past retention

A version is taken out of retention in this order:

1. Put `versions/<ID>/expired.json` (`{"expired_at": "…", "reason": "retention"}`). A reader that sees it treats the version as past retention and does not open its catalog
2. Delete the version's catalogs (`ducklake.duckdb` and `catalogs/`)
3. After the grace period, delete the data files that meet the conditions above

`version.json` stays, so the history still says the version was there and what it was, and the graph stays whole. A version a ref points at is never taken out.

### Source files

- `sources/<sha256>`, named by the lower-case hex sha256 of the content. The same content is stored once
- Reached from the `sha256` in `inputs`. Whether to store them, and who may read them, is the host's call (they may be someone else's copyrighted work)

## Reading and writing

### Reading

1. Read `HEAD` (or the ref asked for). If it starts with `ref:`, read that ref
2. Read `versions/<ID>/version.json` for the ID found. Stop if `expired.json` is there
3. Open `versions/<ID>/ducklake.duckdb` (or a `catalogs/` file it can open) read-only

```sql
ATTACH 'ducklake:https://example.org/lake/versions/019a3c4e-…/ducklake.duckdb' AS lake (READ_ONLY);
```

- The catalog's `data_path` points where the writer published (the root plus `data_dir`). A reader of a mirror adds `DATA_PATH '<mirror root>/data/', OVERRIDE_DATA_PATH true`; without it DuckDB refuses the other location
- A version's catalog never changes, so it may be cached forever. **Only `HEAD` and `refs/` must not be cached**
- A host may serve the catalog of HEAD at `<root>/ducklake.duckdb` for readers that do not know FDL. It must then send the version ID in an `FDL-Version` response header
- Over HTTP, `HEAD` and `refs/` are served as `Content-Type: text/plain; charset=utf-8` with `Cache-Control: no-store`

### Writing (checkout, commit, push)

| Step | What happens |
| --- | --- |
| checkout | Make a working catalog (SQLite) from the catalog of the branch's version, and remember that version |
| commit | Freeze the working catalog's last snapshot into a new version, with the remembered version as its parent |
| push | Check the data files are in the repository, upload the catalog then `version.json`, then move the ref on condition. If refused, check out again and redo the work |

Turning a version's catalog into a working catalog copies DuckLake's bookkeeping tables into SQLite as they are; freezing does the reverse, dropping rows after the snapshot. Neither rewrites Parquet.

A push from a stale working catalog is refused on two checks:

1. The current version of the branch is not among the new version's `parents`
2. The branch is the same, but a data file the catalog names is missing. A file written and left unpushed longer than the grace period may have been deleted; rebuild in the working catalog and push again

## Open questions

- Whether a 1.0 catalog can be written out from a 1.1 working catalog, and whether a catalog can be written in the oldest declared storage format
- Whether the default `data_dir` should be `data/` or DuckLake's habit, `<catalog>.files/`
- Whether a catalog's `data_path` should be an absolute URL. Absolute reads as it is, but a mirror always needs `OVERRIDE_DATA_PATH`
- A list of refs and versions for HTTP readers that cannot list the store (what `info/refs` is to Git's dumb HTTP)
- Whether ref names allow characters beyond ASCII
- How to make a merge (a version with two parents)
- A compatibility test suite for readers and writers
- Withdrawal: removing past versions entirely, for example when personal data was published by mistake (`reason: withdrawn`, rewriting Parquet, and the fact that mirrors keep their copies)
- A repository ID: an immutable `id` in `fdl.json`, so a mirror can tell it holds the same dataset
- Signatures, for readers of a mirror: anyone can write a `version.json`
- How to declare retention defaults in `fdl.json` (versions to keep, days)

## Decided

- **Publishing only some tables** (2026-10-30): everything in one repository is readable by the same people. To mix, use two repositories. The public one's versions are made by copying chosen tables from the private one's versions, with the Parquet copied too, and `inputs` naming the source version. Putting a public catalog beside a private one in one repository is not done: serving would have to decide file by file, and the snapshot history would still name the private tables
- **One kind of working catalog: SQLite** (2026-10-30): on a laptop or in a host's container alike, check out, write, push. Concurrent pushes are settled by the conditional write on the ref
