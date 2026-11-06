---
title: Data files
description: Rules for data files, attachments and source files, when a data file may be deleted, and how a version is taken out of retention.
---

## Data files

- Named as DuckLake names them: `<data_dir><schema>/<table>/ducklake-<uuid>.parquet`. Delete files go in the same place
- Never overwritten. Several versions may name the same file
- A writer may place data files before making a version. Until a version names them, readers do not see them
- Compacting small files (`ducklake_merge_adjacent_files`) is allowed at any time: it writes new files and deletes none

## When a data file may be deleted

Only when both hold:

1. No catalog of a live version names it. A live version is one without `expired.json`
2. It was placed longer ago than the grace period. The grace period protects files a writer has placed but not yet made a version of

DuckLake's own cleanup (`ducklake_cleanup_old_files`, `ducklake_expire_snapshots`) sees only one catalog, so it must not be used to delete from a repository. Deleting is for a process that sees the whole repository.

## Past retention

A version is taken out of retention in this order:

1. Put `versions/<ID>/expired.json`
2. Delete the version's catalogs, `ducklake.duckdb` and `catalogs/`, and its `files.json`
3. After the grace period, delete the data files that meet the conditions above, and the attachments that meet theirs ([Attachments](#attachments))

```json expired.json
{ "expired_at": "2026-12-01T00:00:00Z", "reason": "retention" }
```

- `version.json` stays, so the history still says the version was there and what it was, and the graph of parents stays whole
- A reader that sees `expired.json` treats the version as past retention and does not open its catalog, whatever the `reason`
- A version a ref points at must not be taken out

## Withdrawal

Withdrawal removes data that must not be published, such as personal data published by mistake. Unlike retention, it removes data from every version that holds it, live ones included, and does not wait for the grace period.

1. Make a new version without the data and move every branch that holds an affected version to it. This is a forward move: the new version's parent is the current one
2. Delete the tags that point at affected versions
3. Delete the data files that hold the data, at once: those the affected versions' catalogs name and no version kept reads at its snapshot, and the attachments that hold it, such as a screenshot showing personal data
4. For every affected version, put `expired.json` with `"reason": "withdrawn"` and delete its catalogs

The data files go before the catalogs, so a withdrawal that stops half way can be run again: the catalogs still say which files hold the data.

```json expired.json
{ "expired_at": "2026-12-01T00:00:00Z", "reason": "withdrawn" }
```

- `version.json` stays, as with retention. If the record itself holds the data (in `message` or `inputs`), it is deleted too, and the history shows a missing parent at that point
- Mirrors keep their own copies. A mirror that copies `expired.json` with `"reason": "withdrawn"` deletes the catalogs, the data files and the attachments the origin deleted. The origin cannot force this; the host tells the mirrors it knows of

## Attachments

- `attachments/<sha256>`, named by the lower-case hex sha256 of the content, and listed in a version's `dataset.json` (see [Versions](/spec/versions#documents-and-attachments))
- Never overwritten. Several versions may list the same file
- Deleted on the same conditions as a data file: no `dataset.json` of a live version lists it, and it was placed longer ago than the grace period
- A writer that finds an attachment already there does not upload it again. The file may still be deleted before the writer's `dataset.json` is written, so a push whose `dataset.json` lists a missing attachment is refused ([Writing](/spec/writing#when-a-push-is-refused))
- Withdrawal deletes an attachment at once, like a data file, when it holds data that must not be published. Every version whose `dataset.json` lists it is an affected version

## Source files

- `sources/<sha256>`, named by the lower-case hex sha256 of the content. The same content is stored once
- Reached from the `sha256` in a version's `inputs`
- Whether to store them, and who may read them, is the host's decision. They may be someone else's copyrighted work
