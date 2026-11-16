---
title: Command line
description: The fdl commands, their options and when they refuse.
---

`<remote>` is a repository: `s3://bucket/prefix/`, `https://host/path/` or a folder. Credentials and the endpoint for S3 come from boto3's usual places (`AWS_*` environment variables, `AWS_ENDPOINT_URL` for R2 and other S3-compatible stores). A repository over HTTP takes a token from `FDL_TOKEN` or a credential helper (see [Tokens for HTTP](#tokens-for-http)).

## Tokens for HTTP

fdl keeps no tokens and has no login. A repository over HTTP has to be `https`, except on this machine (`localhost`). fdl sends the token only to the repository's origin, never after a redirect to another one, as `Authorization: Bearer`:

1. `FDL_TOKEN`, when set (for CI)
2. Otherwise what the credential helper set for the repository's URL in fdl's config answers. With no helper set, fdl sends no token

The config is `~/.config/fdl/config.toml` (`$XDG_CONFIG_HOME/fdl/config.toml`, or the file `FDL_CONFIG` names). A helper is set per URL prefix; the one with the longest matching path wins:

```toml
[credential."https://data.example.org"]
helper = "example-cli fdl-credential"
```

fdl runs `<helper> get`, writes `protocol=`, `host=` and `path=` lines and a blank line to its stdin, and reads the `password=` line it prints. A helper that prints nothing, or exits with an error, means no token. The host's own tool stores the token and writes its helper here.

When the host answers 401 to a token from the helper, fdl runs the helper once more and, if it answers a different token, sends the request again with it. A helper can hand out short-lived tokens this way. A token from `FDL_TOKEN` is not asked for again.

## fdl init

```bash
fdl init <remote> [--data-dir data/]
```

Writes `fdl.json`, with a new `id`, and `HEAD`. Running it on an existing repository changes nothing and prints its settings.

## fdl clone

```bash
fdl clone <remote> [<working.sqlite>] [--branch main] [--storage s3://bucket/prefix/]
```

Makes a working metadata file (DuckLake in SQLite) from the metadata of the version the branch holds, or an empty one before the first version. Without `<working.sqlite>`, it is named after the repository's last path segment in the current folder (`towns.sqlite` for `https://example.com/towns/`). `--branch` takes a branch name such as `main`, or a ref such as `refs/heads/main`. Beside it, `<working.sqlite>.fdl.json` remembers the repository, the branch and the version it was made from. It prints the `ATTACH` statement to write with.

For a repository over HTTP, new data files are written to `<working.sqlite>.data/` and uploaded by the push. Files already in the repository are read over HTTP where they are. Once a push has uploaded them, it makes the working metadata again from the new version and deletes the local copies, so the folder holds only what has not been pushed.

With `--storage`, the working metadata reads and writes the data files in the repository's own storage instead: `--storage` is the repository's root there, and new files go straight under its `data_dir`, so the push has none to upload. The keys come from the `AWS_*` variables (`AWS_ENDPOINT_URL` for R2 and other S3-compatible stores), or from a DuckDB secret of your own. The build that writes to the working metadata needs them too.

When the current folder holds no `dataset.json`, it also writes there what `fdl commit` reads: the version's `dataset.json`, without what the commit fills in from the catalog (row counts, column types, nulls), and the files its `tree.json` lists, such as `README.md`. Edit them and commit to change the description. When the folder already holds a description, it is left as it is and nothing is written.

Refused when the version is past retention, when its metadata's sha256 is not the one `version.json` records, when the metadata names a data file outside the data folder, or when a file it would write is already in the current folder.

## fdl config

```bash
fdl config author.name "Alice"
fdl config author.email alice@example.com
fdl config author.name
```

Sets who makes the versions you commit, kept in `~/.config/fdl/config.json` (under `$XDG_CONFIG_HOME` when it is set), apart from any repository. With no value, it prints the one set. `fdl commit`, `fdl revert` and `fdl merge` take the author from `--author`, else from the environment variables `FDL_AUTHOR_NAME` and `FDL_AUTHOR_EMAIL`, else from these settings, and are refused when none gives a name.

## fdl commit

```bash
fdl commit <working.sqlite> -m "What changed" [--author alice] [--dataset dataset.json] [--allow-empty] [--assets files/]
```

Freezes the working metadata into a new version whose parent is the version it was cloned at (or last pushed), and keeps it in `<working.sqlite>.pending/` until `fdl push` sends it. Nothing in the repository changes. It prints the new version ID and its `seq`. Committing again before pushing replaces the version not pushed yet.

Every version is described by a `dataset.json` (see [Versions](/spec/versions#datasetjson)), read from `--dataset` or else from the current directory. It is read as JSON only. The command checks it against the metadata and fills in the column types, nulls and row counts. Comments in the metadata (`COMMENT ON`) are not read, and keys the format does not define are kept as written.

The command puts every other file in the folder of `dataset.json` and below it in the version's tree, leaving out hidden files and folders, and each working metadata `fdl clone` made in that folder (the one committed and any other, found by its `.fdl.json`) with the files `fdl` and SQLite keep beside it (`<working.sqlite>.fdl.json`, `.pending/`, `.data/`, `-wal`), and the `--assets` folder when it is there. Keep the description in a folder of its own, so that nothing else goes in: it writes `tree.json` with their paths from that folder and the push uploads those the repository lacks to `objects/<sha256>`, as written (see [tree.json](/spec/versions#treejson)).

Columns marked `asset: true` hold the sha256 of files (see [Assets](/spec/versions#assets)). The command reads the values those columns hold, and the push uploads the files the repository lacks from the `--assets` folder to `assets/<sha256>`, by their content: file names in the folder do not matter.

When the version would hold what its parent holds (see [When nothing changed](/spec/writing#when-nothing-changed)), it prints `Nothing to commit`, makes nothing and exits 0. A rebuild that writes the same rows to new files counts as unchanged. `--allow-empty` makes the version anyway. If the rows cannot be read to tell, it warns and makes the version.

Refused when:

- there is no `dataset.json`, it has no `name`, or it describes a table or column the metadata lacks
- the working metadata holds inlined rows
- an asset column is not `varchar`, or holds a value that is not a sha256

## fdl push

```bash
fdl push <working.sqlite> [--no-profile]
```

Uploads the version `fdl commit` made and moves the branch to it on condition (see [Order of writes](/spec/writing#order-of-writes)). It prints the branch, the version ID and its `seq`. After a fast-forward `fdl merge`, it moves the branch to the version merged, uploading nothing. With nothing committed, it prints `Nothing to push`.

Before uploading, it writes a profile of each table and view (see [profiles/](/spec/versions#profiles)) and uploads the profiles before `version.json`, so they are there when the branch moves. It reads only the tables whose data files or columns changed since the parent; the others keep the parent's profile. A view is profiled again, by running its query, whenever a table's data files or a view's query changed. A table it cannot read gets none, with a warning. When the working metadata changed after the commit, it profiles nothing and warns. `--no-profile` skips profiling; `fdl profile` adds the profiles later.

Data files the working metadata wrote into the repository are there before the push, but no version names them until it. Files never pushed are deleted after the grace period.

Refused when:

- the branch has moved since the clone, so the version does not descend from what the branch holds now. Clone again and redo the work
- a data file the metadata names is not in the repository
- an asset column points at a file that is neither in the repository nor in `--assets`
- a data file is recorded with an absolute path outside the data directory

A refused push leaves the committed version in place, so it can be sent again once the cause is fixed.

## fdl assets

```bash
fdl assets <folder> > files.csv
```

Prints every file below the folder (hidden ones left out) as CSV, with `path`, `sha256`, `bytes` and `media_type`. Read it into the table that points at the files, for example with DuckDB's `read_csv`, mark its `sha256` column `asset: true`, and commit with `--assets <folder>`. The files are hashed once here; nothing reads them in SQL.

## fdl log

```bash
fdl log <remote> [--ref HEAD]
```

Versions from the one the ref holds back to the first, by first parent. Each shows `seq`, ID, time, author and message. Versions past retention are marked `(expired)`.

## fdl status

```bash
fdl status <working.sqlite> [--json]
```

What the working metadata changed since it was cloned, in the same kinds as `fdl diff`. `Nothing to commit` when it holds what the version it came from holds. The words of `dataset.json` are not compared until the commit. When a version is committed and not pushed yet, it is named first and the changes are counted from it; `--json` gives its ID as `pending`. After a fast-forward `fdl merge`, it says that `fdl push` will move the branch.

## fdl diff

```bash
fdl diff <remote> [<version>] [--against <version>] [--json]
```

What changed in a version (default: the one `HEAD` holds) since its parent, or since the version `--against` names. A version is a version ID or a ref. Each line is one change:

```
+ table main.roads
- table main.old_towns
~ rows main.towns: 1,741 -> 1,802
+ column main.towns.note
- column main.towns.area  (breaking)
~ column main.towns.name -> town  (breaking)
~ column main.towns.id: int32 -> int64
~ data main.towns
~ dataset.json
```

Against its parent, the changes come from the version's `diff` (see [diff](/spec/versions#diff)). Otherwise, or for a version written without one, rows come from `dataset.json`, data files from `files.json`, and columns are compared by name, which does not see a rename. `~ data` means the table's data files differ, even when the rows are the same. `~ dataset.json` means what the writer wrote in `dataset.json` changed, apart from what the commit fills in from the metadata. `--json` prints the same as an object with `tables` (`added`, `dropped`), `rows`, `columns`, `data` and `dataset`. Versions written by fdl-cli 0.31 and earlier show `~ description` lines from their `descriptions` instead.

## fdl show

```bash
fdl show <remote> [<version>] [--json]
```

A version's number, ID, parent, author, time and message, its tables with their rows, and what changed since its parent.

## fdl url

```bash
fdl url <remote> [--ref HEAD] [--attach] [--alias lake]
```

Prints the URL of the metadata of the version the ref holds, to `ATTACH` read-only. With `--attach`, prints the whole `ATTACH` statement with the data path (and, over HTTP with a token, the `CREATE SECRET` before it). A version past retention or withdrawn is refused, even where its metadata is still served.

## fdl tag

```bash
fdl tag <remote>                       # list the tags
fdl tag <remote> <name> [<version>]    # tag a version (default: the one HEAD holds)
fdl tag <remote> <name> --delete
```

`<version>` is a version ID or a ref such as `refs/heads/main`. A tag is created once and never moved: tagging a name that is already there at another version is refused. A version past retention cannot be tagged. Creating or deleting a tag rewrites `refs.json`, as `fdl push` does after moving a branch.

## fdl branch

```bash
fdl branch <remote>                       # list the branches
fdl branch <remote> <name> [<version>]    # start a branch at a version (default: the one HEAD holds)
fdl branch <remote> <name> --delete
```

`<version>` is a version ID or a ref such as `refs/heads/main`. Creating a branch that is already there at another version is refused; at the same version, nothing changes. A version past retention cannot start a branch. The branch `HEAD` names cannot be deleted. Check a branch out with `fdl clone --branch <name>`. Creating or deleting a branch rewrites `refs.json`.

## fdl revert

```bash
fdl revert <version> <working.sqlite> [--author <name>] [-m <message>]
fdl push <working.sqlite>
```

Writes the working metadata back to what `<version>` held (its tables and views) and commits a new version with `<version>`'s `dataset.json` and `tree.json`, as `fdl commit` does. Changes in the working metadata not committed are lost. `fdl push` then moves the branch forward, so the versions in between stay in its history and can still be read. A table unchanged since `<version>` keeps its data files; any other is written again from its rows there. `<version>` is a version ID or a ref, and must be in the first-parent history of the version the working metadata was cloned at (a version only a merge brought in is not), and in retention. When that version already holds what `<version>` held, nothing is made.

To take out data that must not be published, use `fdl withdraw`: a revert leaves the versions in between, and their data files, as they are.

## fdl merge

```bash
fdl merge <theirs> <working.sqlite> [--author <name>] [-m <message>]
fdl push <working.sqlite>
```

Merges another branch into the branch the working metadata was cloned from (see [Merging](/spec/writing#merging)). `<theirs>` is a ref such as `refs/heads/feature`, or a version ID. The base is the common ancestor of the two versions.

- When `<theirs>` is already in the history of the working metadata's version, it prints `Already up to date` and makes nothing
- When the working metadata's version is in the history of `<theirs>`, the working metadata is made again from `<theirs>` and no version is made. `fdl push` then moves the branch there (a fast-forward)
- Otherwise it writes the merge into the working metadata and commits a merge version, whose `parents` are the working metadata's version and `<theirs>`. `fdl push` uploads it as it does a commit. The message defaults to `Merge <theirs>`

A table only `<theirs>` changed, added or dropped since the base comes over as `<theirs>` holds it, written to new data files. Rows of a table both sides changed are merged by the `primary_key` both declare in `dataset.json`: a row only `<theirs>` added, changed or deleted takes that change. `dataset.json` and `tree.json` are merged key by key; the row counts, types and nulls are written again from the merged metadata. The command lists the tables that came over and how many rows `<theirs>` added, changed or deleted. `fdl commit` reads the description from your local files, so when `<theirs>` changed `dataset.json` or `tree.json`, the command warns: bring those changes into your local files before the next commit.

When anything conflicts, the command lists each conflict, by table and, for rows, by the values of the primary key, exits with 1 and changes nothing. A conflict is:

- a table both sides changed differently, without a primary key both declare (a view is compared by its SQL, as such a table)
- a table one side dropped and the other changed
- a table both sides changed whose columns or types differ between the two, or changed on both sides
- a row both sides changed differently, or a primary key that holds `NULL` or repeats
- a value of `dataset.json` or `tree.json` both sides changed differently

Change one of the branches so that the two no longer conflict, and merge again.

Refused when the working metadata has changes not committed, when a version is committed and not pushed, when the two versions have more than one common ancestor that no other descends from, and when the base or `<theirs>` is past retention.

## fdl withdraw

```bash
fdl withdraw <remote> [<version>...] [--objects <file>] [--json] [--yes] [--by <name>] [-m <message>]
```

Removes versions that hold data that must not be published, such as personal data published by mistake (see [Withdrawal](/spec/data-files#withdrawal)). First commit a version without the data, so that no branch points at an affected version. The command then deletes the tags that point at the versions, the data files and assets the versions reach that no kept version reaches, and their metadata files, and puts `expired.json` with `"reason": "withdrawn"` and the names of the deleted objects in `deleted`. `version.json` stays. When one of the versions is already past retention, its metadata is gone, so every data file and asset no live version reaches is deleted at once, without waiting for the grace period.

A version reaches the assets its `tree.json` lists and those its asset columns hold.

Without `--yes` it only prints what it would delete. Files a kept version still reaches are listed as kept; if they hold the data, withdraw that version too. Refused while a branch or a request points at one of the versions.

`--objects` applies a withdrawal made in another repository, in a copy of it such as a mirror. The file is that repository's `expired.json`, or just its `deleted`. Every live version that reaches one of the named objects is withdrawn too, with the versions given, and the named objects still there are deleted even where no version reaches them.

`--json` prints the plan as one JSON object (`versions`, `tags`, `deleted`, `still_read`, `blocked`). Refs other than tags that point at the versions are listed in `blocked` instead of failing, for a host that moves them before running it with `--yes`.

## fdl profile

```bash
fdl profile <remote> [--ref refs/heads/main]
```

Profiles the tables and views of the version the branch or tag holds that have no [profile](/spec/versions#profiles), and puts the profiles beside the version. `fdl push` profiles the version it uploads; this is for a version pushed without them: made before fdl wrote profiles, pushed with `--no-profile`, or whose tables could not be read then. Profiles already there are left as they are. Refused for a version past retention, which has no metadata.

## fdl expire

```bash
fdl expire <remote> [--keep 1] [--since 2026-10-01T00:00:00Z] [--grace 7d] [--by <name>] [-m <message>]
```

Takes versions out of retention: puts `expired.json` and deletes their metadata files. Kept are the newest `--keep` on each branch's first-parent line, the versions refs point at, their merge bases with main, and the versions made at or after `--since` or within `--grace`. Other versions, such as the parents a merge brought in other than its first, are taken out ([Choosing the versions to take out](/spec/data-files#choosing-the-versions-to-take-out)). Nothing is taken out before main has a version. Data files are not deleted; `fdl gc` deletes them. `--by` records who takes the versions out in `expired.json`, and `--message` why, as do the same options of `fdl withdraw`.

## fdl gc

```bash
fdl gc <remote> [--grace 7d] [--yes]
```

Deletes the data files and assets no live version reaches and that were written longer ago than `--grace` (`7d`, `24h` or `30m`), as [When a shared object may be deleted](/spec/data-files#when-a-shared-object-may-be-deleted) allows. These are the files only versions past retention reached, and files working metadata files wrote and never pushed. Without `--yes`, it only lists them.

It reads `files.json` of every live version, and the metadata of a version whose rows hold assets. Refused for a repository over HTTP, where deleting is the host's, and when a live version has neither `files.json` nor a metadata file.
