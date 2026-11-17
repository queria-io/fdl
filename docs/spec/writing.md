---
title: Writing
description: Clone, commit and push - the working metadata, the order of writes, and when a push is refused.
---

## Working metadata

A writer writes to a working metadata file: a DuckLake metadata file in SQLite, outside the repository. There is one kind of working metadata, whether it is on a laptop or in a host's container.

- It must be attached with `DATA_INLINING_ROW_LIMIT 0`, so that every row goes to a data file
- Its data path is the repository's data folder (`<root><data_dir>`), so new data files land in the repository. A repository over HTTP is the exception: new data files go to a folder on the writer's machine and the push uploads them (see [Pushing over HTTP](/spec/http#writing-to-it))
- `ducklake_expire_snapshots` and `ducklake_cleanup_old_files` must not be run on it

## Steps

| Step | What happens |
| --- | --- |
| clone | Make a working metadata file from the metadata of the branch's version, and remember that version. Before the first version, make an empty one |
| commit | Set the [tags](/spec/versions#tags) of the tables and views, and freeze the working metadata's last snapshot into a new version, with the remembered version as its parent |
| push | Check the data files, measure each table into a profile (optional), upload the metadata, `files.json` and the profiles, then `version.json`, then move the ref on condition |

A writer may keep a committed version on its own disk and push it later, as Git keeps commits until `git push`. Until the push, nothing in the repository changes, apart from data files the working metadata wrote there. No version names those yet, so they are deleted after the grace period if the version is never pushed.

Checking out copies DuckLake's bookkeeping tables from the frozen metadata into SQLite as they are. Freezing does the reverse, keeping only the last snapshot and the rows it reads. Neither rewrites a data file.

When freezing, a writer must:

- refuse a working metadata file that holds inlined rows
- keep only the last snapshot: leave out earlier snapshots, the rows that ended before it and the files scheduled for deletion (see [The frozen metadata](/spec/versions#the-frozen-metadata))
- set the frozen metadata's `data_path` to the absolute URL of `<root><data_dir>`
- refuse if any data file is recorded with an absolute path, or outside `data_dir`

## When nothing changed

A writer should not make a version that holds what its parent holds, as Git makes no commit when the tree is the parent's. A version holds the same as its parent when:

- the last snapshot is the parent's (`ducklake.snapshot_id`), or
- the same tables and views are there with the same tags, each table with the same columns and types and the same rows, counted as a multiset (order does not matter)

A version never holds only a change of words: what a table or a column means to people is not part of it ([Tags](/spec/versions#tags)). A table rebuilt from the same input is written to new data files, so comparing files or metadata bytes is not enough. A writer compares the files first and reads the rows only of tables whose files differ. A writer that cannot read the rows makes the version. A writer may make one anyway when asked, as `git commit --allow-empty` does.

## Reverting

A writer that brings back what an earlier version held makes a new version, as `git revert` makes a new commit. It does not move a branch back: a branch moves only forward by default (see [Refs](/spec/refs)), and readers may already hold the versions in between.

The new version's parent is the version the working metadata was cloned at, so pushing it is refused if the branch has moved since, as for any commit. Its tables and views are those of the earlier version at its snapshot, with the same columns, rows and tags. The working metadata gets there in a new snapshot: tables the earlier version did not hold are dropped, and tables changed since are written again from their rows at the earlier snapshot. The writer reads the earlier version's tables from that version's own metadata, as it reads theirs when merging.

## Branches

A branch is made by creating `refs/heads/<name>` at a version (see [Refs](/spec/refs)). Work on it is cloned, committed and pushed as on `main`. Its versions are not on `main`'s history until they are merged.

## Merging

Merging brings what another branch changed into the branch being written. The writer works out the result from three versions:

- ours: the version the working metadata was cloned at, on the branch being written
- theirs: the version of the other branch
- the base: a common ancestor of ours and theirs that no other common ancestor descends from, following every entry of `parents`. When there is more than one, the writer refuses to merge

When the base is theirs, there is nothing to merge. When the base is ours, the branch is moved to theirs, with no new version. Otherwise the writer makes a merge version: its `parents` are ours, then theirs, and pushing it is refused if the branch has moved since, as for any commit. The base's metadata must still be there, with its snapshot; a writer refuses to merge from a base past retention.

### Tables

A table is the same table in two versions when it has the same `schema.table`. Each table is compared in the three versions, and two versions hold the same table when the columns, their types, the tags and the rows (counted as a multiset) are the same:

| ours and theirs, against the base | The merge version holds |
| --- | --- |
| Neither changed it, or both changed it the same way | That table |
| Only one changed it, added it or dropped it | That side's table, or none if it dropped it |
| One dropped it and the other changed it | A conflict |
| Both changed it, or both added it, differently | Rows merged by primary key, below, or a conflict |

A view is compared by its SQL and its tags, as a table without a primary key.

### Rows merged by primary key

Two changes to the same table are merged row by row when ours and theirs have the same tags for it, `fdl.primary_key` among them, and the base has the same `fdl.primary_key`, or did not hold the table. The columns and their types must be the same in ours and theirs; a table whose columns changed on both sides is a conflict. A base written before tags reads them from its `dataset.json` ([Files of earlier versions](/spec/versions#files-of-earlier-versions)).

Each row is found by the values of its primary key, and each key is compared as a table is above: a row only one side added, changed or deleted takes that side's change, and a key both sides changed differently is a conflict. A table where a key holds `NULL` or appears more than once, in any of the three versions, is a conflict.

A table without a primary key is not merged by rows, even when the two sides changed different rows. A writer may warn when a table has no primary key. A key made up for the merge (a row number, say) is not a primary key: it does not say which rows are the same row.

### Conflicts

A merge with any conflict makes no version. The writer lists each conflict, by table and, for rows, by the values of the primary key, and changes nothing in the repository. The person merging changes one of the branches so that the two no longer conflict, and merges again.

### The merge version

The working metadata is cloned from ours and gets the result in a new snapshot. Data files may be written anew or, for a table taken whole from theirs, named as theirs names them. `diff` compares the merge version with ours, its first parent.

## Order of writes

1. Data files and assets
2. `versions/<ID>/metadata.db` (and any `metadata/`)
3. `versions/<ID>/files.json` and `versions/<ID>/profiles/`
4. `versions/<ID>/version.json`
5. The ref, on condition

A writer stopped part way has changed nothing a reader sees. The files it left are not named by any ref, and data files among them are deleted after the grace period.

Files of a version are written once. A writer that finds one already there with the same content goes on; with different content, it stops.

## When a push is refused

A push from stale working metadata is refused on two checks:

1. The new version does not descend from the version the branch holds now. Someone else pushed first
2. A data file the metadata names, or a file its `tree.json` lists when it has one ([Files of earlier versions](/spec/versions#files-of-earlier-versions)), is missing from the repository. A host may check the assets the rows hold as well. A file that no live version named for longer than the grace period may have been deleted, even if the writer found it there

A writer also refuses to commit or push when `fdl.json` names another `data_dir` than the one it checked out with ([Changing data_dir](/spec/layout#changing-data_dir)).

A host may also refuse a push on rules of its own, such as a table without `fdl.primary_key`, or a dataset whose license or description the host has not received by its own means. The format does not define those rules; the host says why it refused. The writer changes what the host asked for, commits again and pushes.

In every case the writer checks out again, redoes the work and pushes again. After the first check, it may instead merge the branch into its work. Nothing in the repository needs to be cleaned up: the refused version is not named by any ref.
