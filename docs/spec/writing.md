---
title: Writing
description: Checkout, commit and push - the working catalog, the order of writes, and when a push is refused.
---

## Working catalog

A writer writes to a working catalog: a DuckLake catalog in SQLite, outside the repository. There is one kind of working catalog, whether it is on a laptop or in a host's container.

- It must be attached with `DATA_INLINING_ROW_LIMIT 0`, so that every row goes to a data file
- Its data path is the repository's data folder (`<root><data_dir>`), so new data files land in the repository. A repository over HTTP is the exception: new data files go to a folder on the writer's machine and the push uploads them (see [Pushing over HTTP](/spec/http#writing-to-it))
- `ducklake_expire_snapshots` and `ducklake_cleanup_old_files` must not be run on it

## Steps

| Step | What happens |
| --- | --- |
| checkout | Make a working catalog from the catalog of the branch's version, and remember that version. Before the first version, make an empty one |
| commit | Freeze the working catalog's last snapshot into a new version, with the remembered version as its parent, and describe it in `dataset.json` |
| push | Check the data files, measure each table into a profile (optional), upload the catalog, `dataset.json`, `files.json`, `tree.json` and the profiles, then `version.json`, then move the ref on condition |

A writer may keep a committed version on its own disk and push it later, as Git keeps commits until `git push`. Until the push, nothing in the repository changes, apart from data files the working catalog wrote there. No version names those yet, so they are deleted after the grace period if the version is never pushed.

Checking out copies DuckLake's bookkeeping tables from the frozen catalog into SQLite as they are. Freezing does the reverse, keeping the rows up to the last snapshot. Neither rewrites a data file.

When freezing, a writer must:

- refuse a working catalog that holds inlined rows
- set the frozen catalog's `data_path` to the absolute URL of `<root><data_dir>`
- refuse if any data file is recorded with an absolute path, or outside `data_dir`

## When nothing changed

A writer should not make a version that holds what its parent holds, as Git makes no commit when the tree is the parent's. A version holds the same as its parent when:

- the last snapshot is the parent's (`ducklake.snapshot_id`), or
- the same tables and views are there, each table with the same columns and types and the same rows, counted as a multiset (order does not matter), and `dataset.json` and `tree.json` have the parent's sha256 (or neither version has a `tree.json`)

A table rebuilt from the same input is written to new data files, so comparing files or catalog bytes is not enough. A writer compares the files first and reads the rows only of tables whose files differ. A writer that cannot read the rows makes the version. A writer may make one anyway when asked, as `git commit --allow-empty` does.

## Reverting

A writer that brings back what an earlier version held makes a new version, as `git revert` makes a new commit. It does not move a branch back: a branch moves only forward by default (see [Refs](/spec/refs)), and readers may already hold the versions in between.

The new version's parent is the version the working catalog was checked out at, so pushing it is refused if the branch has moved since, as for any commit. Its tables and views are those of the earlier version at its snapshot, with the same columns and rows, and its `dataset.json` and `tree.json` are the earlier version's. The working catalog gets there in a new snapshot: tables the earlier version did not hold are dropped, and tables changed since are written again from their rows at the earlier snapshot. The earlier snapshot must still be in the catalog.

## Branches

A branch is made by creating `refs/heads/<name>` at a version (see [Refs](/spec/refs)). Work on it is checked out, committed and pushed as on `main`. Its versions are not on `main`'s history until they are merged.

## Merging

Merging brings what another branch changed into the branch being written. The writer works out the result from three versions:

- ours: the version the working catalog was checked out at, on the branch being written
- theirs: the version of the other branch
- the base: a common ancestor of ours and theirs that no other common ancestor descends from, following every entry of `parents`. When there is more than one, the writer refuses to merge

When the base is theirs, there is nothing to merge. When the base is ours, the branch is moved to theirs, with no new version. Otherwise the writer makes a merge version: its `parents` are ours, then theirs, and pushing it is refused if the branch has moved since, as for any commit. The base's catalog must still be there, with its snapshot; a writer refuses to merge from a base past retention.

### Tables

A table is the same table in two versions when it has the same `schema.table`. Each table is compared in the three versions, and two versions hold the same table when the columns, their types and the rows (counted as a multiset) are the same:

| ours and theirs, against the base | The merge version holds |
| --- | --- |
| Neither changed it, or both changed it the same way | That table |
| Only one changed it, added it or dropped it | That side's table, or none if it dropped it |
| One dropped it and the other changed it | A conflict |
| Both changed it, or both added it, differently | Rows merged by primary key, below, or a conflict |

A view is compared by its SQL, as a table without a primary key.

### Rows merged by primary key

Two changes to the same table are merged row by row when ours and theirs declare the same `primary_key` for it in `dataset.json`, and the base does as well, or did not hold the table. The columns and their types must be the same in ours and theirs; a table whose columns changed on both sides is a conflict.

Each row is found by the values of its primary key, and each key is compared as a table is above: a row only one side added, changed or deleted takes that side's change, and a key both sides changed differently is a conflict. A table where a key holds `NULL` or appears more than once, in any of the three versions, is a conflict.

A table without a primary key is not merged by rows, even when the two sides changed different rows. A writer may warn when a table has no primary key. A key made up for the merge (a row number, say) is not a primary key: it does not say which rows are the same row.

### Descriptions

`dataset.json` and `tree.json` are merged key by key. An object is compared member by member, and the entries of `tables` are matched by `schema` and `name`, and those of `fields` by `name`. Any other value (a string, a number, a list) is taken whole: when only one side changed it, that side's value; when both changed it differently, a conflict. A file of `tree.json` is matched by its path.

The values made from the catalog (`rows`, and each field's `type` and `nullable`) are written again from the merge version's catalog, not merged.

### Conflicts

A merge with any conflict makes no version. The writer lists each conflict, by table and, for rows, by the values of the primary key, and changes nothing in the repository. The person merging changes one of the branches so that the two no longer conflict, and merges again.

### The merge version

The working catalog is checked out from ours and gets the result in a new snapshot. Data files may be written anew or, for a table taken whole from theirs, named as theirs names them. `diff` compares the merge version with ours, its first parent.

## Order of writes

1. Data files and assets
2. `versions/<ID>/ducklake.duckdb` (and any `catalogs/`)
3. `versions/<ID>/dataset.json`, `versions/<ID>/files.json`, `versions/<ID>/tree.json` and `versions/<ID>/profiles/`
4. `versions/<ID>/version.json`
5. The ref, on condition

A writer stopped part way has changed nothing a reader sees. The files it left are not named by any ref, and data files among them are deleted after the grace period.

Files of a version are written once. A writer that finds one already there with the same content goes on; with different content, it stops.

## When a push is refused

A push from a stale working catalog is refused on two checks:

1. The new version does not descend from the version the branch holds now. Someone else pushed first
2. A data file the catalog names, or a file `tree.json` lists, is missing from the repository. A host may check the assets the rows hold as well. A file that no live version named for longer than the grace period may have been deleted, even if the writer found it there

In both cases the writer checks out again, redoes the work and pushes again. After the first, it may instead merge the branch into its work. Nothing in the repository needs to be cleaned up: the refused version is not named by any ref.
