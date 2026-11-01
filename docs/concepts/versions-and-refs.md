---
title: Versions and refs
description: A version is a frozen catalog with a record; a ref is a name that points at one. How they relate to DuckLake snapshots and to Git commits.
---

## Versions

A version is one frozen DuckLake catalog and a record of how it came to be. It lives in its own folder and is never changed once written.

```
versions/019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c/
  version.json         the record: parents, author, message, checksums
  dataset.json         what it holds: tables, columns, license
  ducklake.duckdb      the frozen catalog
```

The record says:

- `id`: the version ID, a UUIDv7. IDs sort by creation time, and two writers never make the same one
- `seq`: the number shown to people, the parent's `seq` plus one
- `parents`: the version or versions it was built on. Empty for the first
- `author`, `message` and `created_at`
- `catalog`: the size and sha256 of the catalog file, so a reader can check what it fetched
- `ducklake`: the DuckLake format of the catalog and the snapshot it was frozen at

The ID is not a hash of the content, as a Git commit's is. The same catalog can be written as different bytes by two DuckDB releases, so a content hash would not be stable. The sha256 in `catalog` checks the content instead.

The full list of fields is in [Versions](/spec/versions) in the specification.

## Snapshots and versions

DuckLake makes a snapshot on every write: each `INSERT`, `CREATE TABLE` or `ALTER` in its own transaction. A day of work can make hundreds. A version is made when you decide the work is ready, and it takes the last of those snapshots.

A snapshot is to a version what a commit on a work branch is to the squashed commit that lands on `main`. The snapshots stay inside the catalog, with their `commit_message` and `commit_extra_info`, so the detail is not lost. `version.json` records the range of snapshots the version adds after its parent, but does not copy them.

## Refs

A ref is a file that holds a version ID and a newline. It is how a reader finds a version without knowing its ID.

```
refs/heads/main          019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c
refs/tags/2025-final     019a2f10-0c4d-7e88-a1b2-55c0d9e3f7aa
HEAD                     ref: refs/heads/main
```

| Kind | Path | Moves |
| --- | --- | --- |
| branch | `refs/heads/<name>` | yes, forward |
| tag | `refs/tags/<name>` | never |
| request | `refs/requests/<number>` | as the host decides |
| HEAD | `HEAD` | rarely |

`main` is the branch readers get unless they ask for another. `HEAD` names it, as in Git. A tag pins a version that readers can rely on, such as the final figures of a year. `refs/requests/` is for hosts: a version waiting for approval can be held there without being on `main`.

Refs are the only files in a repository that change. Everything else is written once.

## Moving a branch

A branch moves forward only. A new version can become `main` when its `parents` include the version `main` holds now. This is a fast-forward in Git's terms.

```
main → A
           Alice checks out A, commits B (parent A), pushes: main → B
           Bob checked out A too, commits C (parent A), pushes: refused
```

Bob's push is refused because C is not built on B. If it were accepted, B would vanish from the history of `main` with no one deciding that. Bob checks out B and makes his change again.

FDL does not merge rows. Merging two DuckLake catalogs would mean deciding which row wins when both sides changed it, and DuckLake gives tables no identity across catalogs that would make that safe. Redoing the change on the new version is usually a matter of running the same SQL again.

Moving a branch back, to undo a version, is allowed only when asked for explicitly. The version that was undone stays in the repository and in the history.

## History

`fdl log` follows the first parent from the version a ref holds back to the first one:

```
   3  019a4b…  2026-11-01T09:00:00Z  alice
      Correct the 2026 figures
   2  019a3c…  2026-10-15T09:00:00Z  bob
      Add 2026
   1  019a2f…  2026-10-01T09:00:00Z  alice
      First towns
```

Versions that were taken out of retention stay in the history. Their record is kept, only their catalog is gone. See [Retention](/internals/inside-a-repository#taking-versions-out-of-retention).
