---
title: Writing
description: Clone, commit and push - the working metadata, the order of writes, and when a push is refused.
---

## Working metadata

A writer writes to a working metadata file: a DuckLake metadata file in SQLite, outside the dataset. There is one kind of working metadata, whether it is on a laptop or in a host's container.

- It must be attached with `DATA_INLINING_ROW_LIMIT 0`, so that every row goes to a data file
- Its data path is the dataset's data folder (`<root><data_dir>`), so new data files land in the dataset. A dataset over HTTP is the exception: new data files go to a folder on the writer's machine and the push uploads them (see [Pushing over HTTP](/spec/http#writing-to-it))
- `ducklake_expire_snapshots` and `ducklake_cleanup_old_files` must not be run on it

## Steps

| Step | What happens |
| --- | --- |
| clone | Make a working metadata file from the metadata of the branch's version, and remember that version. Before the first version, make an empty one |
| commit | Freeze the working metadata's last snapshot into a new version, with the remembered version as its parent, and write its `tables.json` with the parent's [declarations](/spec/versions#declarations) and the changes the writer's tool made to them |
| push | Check the data files, measure each table into a profile (optional), upload the metadata, `tables.json` and the profiles, then `version.json`, then move the ref on condition |

A writer may keep a committed version on its own disk and push it later, as Git keeps commits until `git push`. Until the push, nothing in the dataset changes, apart from data files the working metadata wrote there. No version names those yet, so they are deleted after the grace period if the version is never pushed.

Checking out copies DuckLake's bookkeeping tables from the frozen metadata into SQLite as they are. Freezing does the reverse, keeping only the last snapshot and the rows it reads. Neither rewrites a data file.

When freezing, a writer must:

- refuse a working metadata file that holds inlined rows
- keep only the last snapshot: leave out earlier snapshots, the rows that ended before it and the files scheduled for deletion (see [The frozen metadata](/spec/versions#the-frozen-metadata))
- set the frozen metadata's `data_path` to the absolute URL of `<root><data_dir>`
- refuse if any data file is recorded with an absolute path, or outside `data_dir`
- give each live table that has table statistics but no column statistics a column statistic for each column of its live data files, with the null and NaN flags of those files and no minimum or maximum; a table with no live data file gets one for each of its leaf columns, with no nulls
- refuse frozen metadata that DuckLake cannot attach and list the tables of, or that still has a table with table statistics but no column statistics

## When nothing changed

A writer should not make a version that holds what its parent holds, as Git makes no commit when the tree is the parent's. A version holds the same as its parent when:

- the last snapshot is the parent's (`ducklake.snapshot_id`), or
- the same tables and views are there with the same declarations, `dependencies` is the same, and each table is there with the same columns and types and the same rows, counted as a multiset (order does not matter)

Only tables, views, their declarations and `dependencies` are compared. Words kept outside them, such as DuckLake's `comment`, make no version on their own; words kept as rows of a table make one when the rows change ([Declarations](/spec/versions#declarations)). A table rebuilt from the same input is written to new data files, so comparing files or metadata bytes is not enough. A writer compares the files first and reads the rows only of tables whose files differ. A writer that cannot read the rows makes the version. A writer may make one anyway when asked, as `git commit --allow-empty` does.

## Reverting

A writer that brings back what an earlier version held makes a new version, as `git revert` makes a new commit. It does not move a branch back: a branch moves only forward by default (see [Refs](/spec/refs)), and readers may already hold the versions in between.

The new version's parent is the version the working metadata was cloned at, so pushing it is refused if the branch has moved since, as for any commit. Its tables and views are those of the earlier version at its snapshot, with the same columns, rows and declarations, and its `dependencies` are the earlier version's. The working metadata gets there in a new snapshot: tables the earlier version did not hold are dropped, and tables changed since are written again from their rows at the earlier snapshot. The writer reads the earlier version's tables from that version's own metadata, as it reads theirs when merging.

## Branches

A branch is made by creating `refs/heads/<name>` at a version (see [Refs](/spec/refs)). Work on it is cloned, committed and pushed as on `main`. Its versions are not on `main`'s history until they are merged.

## Merging

Merging brings what another branch changed into the branch being written. The writer works out the result from three versions:

- ours: the version the working metadata was cloned at, on the branch being written
- theirs: the version of the other branch
- the base: a common ancestor of ours and theirs that no other common ancestor descends from, following every entry of `parents`. When there is more than one, the writer refuses to merge

When the base is theirs, there is nothing to merge. When the base is ours, the branch is moved to theirs, with no new version. Otherwise the writer makes a merge version: its `parents` are ours, then theirs, and pushing it is refused if the branch has moved since, as for any commit. The base's metadata must still be there, with its snapshot; a writer refuses to merge from a base past retention.

### Tables

A table is the same table in two versions when it has the same `schema.table`. Each table is compared in the three versions, and two versions hold the same table when the columns, their types, the [declarations](/spec/versions#declarations) and the rows (counted as a multiset) are the same:

| ours and theirs, against the base | The merge version holds |
| --- | --- |
| Neither changed it, or both changed it the same way | That table |
| Only one changed it, added it or dropped it | That side's table, or none if it dropped it |
| One dropped it and the other changed it | A conflict |
| Both changed it, or both added it, differently | Cells merged by primary key or rows merged without one, below, or a conflict |

A view is compared by its SQL and its declarations. When both sides changed a view differently, its SQL and each declaration are merged as values: one only one side changed takes that side's change, and one both changed differently is a conflict.

`dependencies` is merged by name in the same way: an entry only one side added, changed or removed takes that side's change, and an entry both changed differently is a conflict.

Comments (DuckLake's `comment`) are not compared, but the merge version keeps them. Each comment of a table, a view or a column is merged as a value: a comment only one side changed takes that side's change, wherever the table itself comes from. When both sides changed it differently, the merge version keeps ours and the writer warns.

### Cells merged by primary key

When both sides changed a table differently, the writer merges it cell by cell. A cell is the value one row holds in one column, and the row is found by the values of its primary key. The table must have the same `primary_key` in ours, theirs and the base, or the base must not hold the table. A table whose `primary_key` differs between them, or that has one on only some of them, is a conflict.

- Each declaration other than `primary_key` is merged as a value, as for a view
- Columns the base holds are found by their `id` in `tables.json`, so a rename is a change to that column. This holds only while the table has the same DuckLake table ID in the base and the side; a table made again under the same name is matched by column name. Columns added since the base are found by name. A column only one side added, renamed, dropped or changed the type of takes that side's change. A column both sides changed differently, or one side dropped and the other changed a cell of, is a conflict
- A cell one side changed in a column the other side changed the type of is cast to the new type. A cell whose value does not cast is a conflict
- A row only one side added or deleted takes that side's change. A row one side deleted and the other changed a cell of is a conflict. A row both sides added is kept when the two hold the same values, and is a conflict otherwise
- In a row both sides kept, a cell only one side changed takes that side's value, and a cell both changed to different values is a conflict. Two `NULL`s are the same value
- In a column only one side added, the rows only the other side added hold the column's default, or `NULL`. When the column is `NOT NULL` and has no default, the table is a conflict

A table where a key holds `NULL` or appears more than once, in any of the three versions, is a conflict. A key made up for the merge (a row number, say) is not a primary key: it does not say which rows are the same row.

### Rows merged without a primary key

When both sides changed a table that has no `primary_key` in ours, theirs or the base differently, the writer merges its rows as a multiset. A row is the values of all its columns, and the writer counts how many times each row appears in the base, ours and theirs.

- Each declaration is merged as a value, as for a view. The columns and their types must be the same in ours and theirs; a table whose columns differ between the two is a conflict
- For each row, when only one side's count differs from the base, the merge version holds that side's count. When both differ from the base by the same count, it holds that count. When both differ and the counts are not the same, the row is a conflict
- A changed value is a row deleted and a row added. When both sides change the same row differently, both delete the old row and each adds its own, so the merge version holds both new rows

A writer may warn when it merges a table without a primary key this way.

### Conflicts

A merge with any conflict makes no version. The writer lists each conflict, by table and, for cells, by the values of the primary key and the column, and changes nothing in the dataset. The person merging changes one of the branches so that the two no longer conflict, and merges again.

### The merge version

The working metadata is cloned from ours and gets the result in a new snapshot. Data files may be written anew or, for a table taken whole from theirs, named as theirs names them. `diff` compares the merge version with ours, its first parent.

## Order of writes

1. Data files and assets
2. `versions/<ID>/metadata.db`
3. `versions/<ID>/tables.json` and `versions/<ID>/profiles/`
4. `versions/<ID>/version.json`
5. The ref, on condition

A writer stopped part way has changed nothing a reader sees. The files it left are not named by any ref, and data files among them are deleted after the grace period.

Files of a version are written once. A writer that finds one already there with the same content goes on; with different content, it stops.

## When a push is refused

A push from stale working metadata is refused on two checks:

1. The new version does not descend from the version the branch holds now. Someone else pushed first
2. A data file `tables.json` names is missing from the dataset. A host may check the assets the rows hold as well. A file that no live version named for longer than the grace period may have been deleted, even if the writer found it there

A host also refuses a push whose `tables.json` does not match `version.json`. A host that opens the metadata may also refuse one whose `tables.json` does not list the data files and delete files the metadata names ([Pushing over HTTP](/spec/http#requests)).

A writer also refuses to commit or push when `fdl.json` names another `data_dir` than the one it checked out with ([Changing data_dir](/spec/layout#changing-data_dir)).

A host may also refuse a push on rules of its own, such as a table without `primary_key`, or a dataset without a license or a description the host requires. The format does not define those rules; the host says why it refused. The writer changes what the host asked for, commits again and pushes.

In every case the writer checks out again, redoes the work and pushes again. After the first check, it may instead merge the branch into its work. Nothing in the dataset needs to be cleaned up: the refused version is not named by any ref.
