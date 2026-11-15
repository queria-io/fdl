---
title: Data files
description: Rules for shared objects (data files and assets), when a shared object may be deleted, and how a version is taken out of retention.
---

## Shared objects

Data files and assets are shared objects: written once, never overwritten, and reached by any number of versions, as blobs are in Git. A version reaches them this way:

| Object | Path | A version reaches it when |
| --- | --- | --- |
| data file | `<data_dir><schema>/<table>/ducklake-<uuid>.parquet` | its metadata names it |
| asset | `assets/<sha256>` | a column its `dataset.json` marks as `asset` holds the sha256 in a row, or its `tree.json` lists it |

- A writer may place shared objects before making a version. Until a version reaches them, readers do not see them
- A writer that finds an object already there need not upload it again. It may still be deleted before the version is written, so a push whose version reaches a missing object is refused ([Writing](/spec/writing#when-a-push-is-refused))

### Data files

- Named as DuckLake names them. Delete files go in the same place
- Compacting small files (`ducklake_merge_adjacent_files`) is allowed at any time: it writes new files and deletes none

### Assets

- Named by the lower-case hex sha256 of the content, so the same content is stored once (see [Versions](/spec/versions#assets))

## When a shared object may be deleted

Only when both hold:

1. No live version reaches it. A live version is one without `expired.json`
2. It was placed longer ago than the grace period. The grace period protects objects a writer has placed but not yet made a version of

Only a file whose path has the form in the table above is a shared object: a data file named `ducklake-<name>.parquet` under the data directory, or `assets/` followed by 64 lower-case hex digits. A process that deletes shared objects must leave every other file alone.

A version's metadata names data files at every snapshot it keeps, not only at `ducklake.snapshot_id`, so the files in `tables` of `files.json` are not all it reaches. A process that cannot open the metadata uses `named` of `files.json`. When a live version has no `files.json`, or one without `named`, the process opens its metadata or deletes no data files.

DuckLake's own cleanup (`ducklake_cleanup_old_files`, `ducklake_expire_snapshots`) sees only one metadata file, so it must not be used to delete from a repository. Deleting is for a process that sees the whole repository.

## Files outside data_dir

A version reaches data files only under `data_dir` as `fdl.json` names it now. A file outside it, such as one left in the old place after [`data_dir` changed](/spec/layout#changing-data_dir), is reached by no version, whatever its name. A process that deletes shared objects looks only under `data_dir` and `assets/`, so it never deletes such a file. Deleting it is the host's own work: the host deletes the old place once no reader can still be reading there, by listing it and deleting key by key, after checking again that `fdl.json` names the new `data_dir`.

## Deleting safely

A deleted shared object cannot be brought back, and every version that reaches it can no longer be read. A process that deletes shared objects follows these rules:

1. Delete nothing it cannot account for. When it cannot tell what a live version reaches, because a `version.json`, `files.json` or `tree.json` cannot be read or parsed, or `files.json` has no `named` and the metadata cannot be opened, it deletes none of that kind: no data file when it cannot tell which data files the version reaches, no asset when it cannot tell which assets. Stopping without deleting anything also meets this. An object kept too long only costs storage; an object deleted too early breaks a version
2. Read the versions again right before deleting. A writer may make a version while the process decides. Objects a version it did not see when deciding reaches are kept
3. Delete the objects it decided on, one key at a time. It never deletes by prefix or a whole directory
4. Implementations of this page agree on the cases in [Collection cases](/spec/collection-cases): for each repository there, the objects to delete are the same

A process may move an object to a place readers never read from, such as a separate bucket, and delete it there after a set time, so that a mistake can be undone. A writer does not count an object there as present.

## Past retention

A version is taken out of retention in this order:

1. Put `versions/<ID>/expired.json`
2. Delete the version's metadata files, `metadata.db` and `metadata/`, and its `files.json` and `tree.json`
3. After the grace period, delete the shared objects that meet the conditions above

```json expired.json
{ "expired_at": "2026-12-01T00:00:00Z", "reason": "retention", "by": "nightly retention" }
```

- `by` may say who or what took the version out: a person, or a process such as a scheduled job. With it, the history shows who removed each version's data, as it shows who made it
- `version.json` stays, so the history still says the version was there and what it was, and the graph of parents stays whole
- A reader that sees `expired.json` treats the version as past retention and does not open its metadata, whatever the `reason`
- A version a ref points at must not be taken out

## Withdrawal

Withdrawal removes data that must not be published, such as personal data published by mistake. Unlike retention, it removes data from every version that holds it, live ones included, and does not wait for the grace period.

1. Make a new version without the data and move every branch that holds an affected version to it. This is a forward move: the new version's parent is the current one. Its metadata must not keep a snapshot that holds the data (`ducklake_expire_snapshots` in the working metadata), since a metadata file reaches every file it names
2. Delete the tags that point at affected versions
3. Delete the shared objects that hold the data, at once, such as a data file with personal data or a screenshot showing it: those an affected version reaches and no version kept reaches. Every version that reaches such an object is an affected version. An affected version already past retention has no metadata to say which objects it reached, so when there is one, every shared object no live version reaches is deleted at once too
4. For every affected version, put `expired.json` with `"reason": "withdrawn"` and `deleted`, and delete its metadata files

The shared objects go before the metadata files, so a withdrawal that stops half way can be run again: the metadata files still say which files hold the data.

```json expired.json
{
  "expired_at": "2026-12-01T00:00:00Z",
  "reason": "withdrawn",
  "deleted": {
    "data": ["main/towns/ducklake-019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c.parquet"],
    "assets": ["9f2c…"]
  }
}
```

- `deleted` names the shared objects step 3 deleted: data files by their path under `data_dir`, assets by their sha256. Names only, so it holds none of the data. Every affected version of one withdrawal carries the same list. A withdrawal run again after stopping half way keeps the names already there, since objects it deleted the first time may no longer be found
- A host refuses a push that places an object named in `deleted` of any version, and a process that copies versions between repositories copies none of them. Otherwise an old copy, such as a local clone, brings the data back
- A copy of the repository, such as a mirror or a fork, applies the withdrawal by the names in `deleted`: every live version in it that reaches one of them is an affected version, including versions made only in the copy, and the named objects go even where no version reaches them

- `version.json` stays, as with retention, so every repository holds its history back to the first version. If the record itself holds the data (in `author`, `message` or `inputs`), it is rewritten without it before `expired.json` is put: the value in `author` or `message` is replaced with `withdrawn`, and the entries of `inputs` that hold it are dropped. `id`, `seq`, `parents` and the other fields stay as they were
- Mirrors keep their own copies. A mirror that copies `expired.json` with `"reason": "withdrawn"` deletes the metadata files and the shared objects named in `deleted`. The origin cannot force this; the host tells the mirrors it knows of
