---
title: Python API
description: The fdl package - stores, refs, and the dataset operations without a terminal.
---

```python
from fdl import repo, refs
from fdl.stores import open_store

store = open_store("s3://my-bucket/towns/")
for version in repo.log(store):
    print(version.seq, version.id, version.message)
```

| Module | Holds |
| --- | --- |
| `fdl.repo` | the operations: `init`, `clone`, `commit`, `push`, `log`, `expire`, `prune` |
| `fdl.declared` | the [declarations](/spec/versions#declarations) of a version: primary keys, asset columns, profiles and dependencies, their checks and their merge |
| `fdl.diff` | what changed between two versions (`diff.versions`) or in a working metadata file (`diff.working`) |
| `fdl.refs` | reading and moving refs under their conditions |
| `fdl.version` | `Version`, the record in `version.json`, and new IDs |
| `fdl.catalog` | DuckLake metadata files in DuckDB and SQLite, and the copy between them |
| `fdl.layout` | where each file of a dataset sits |
| `fdl.store`, `fdl.stores` | what a store has to do, and the folder and S3 stores |

## Stores

`open_store(url)` returns a folder store, an S3 store, or an HTTP store for a host that takes [pushes over HTTP](/spec/http). Any object with these methods works in their place:

| Method | Does |
| --- | --- |
| `put(key, data, *, content_type, if_match, if_none_match)` | Write and return the ETag. A failed condition raises an error whose `status` is 412 |
| `put_file(key, path, …)` | The same, from a file |
| `get(key)` | Read. A missing object raises an error whose `status` is 404 |
| `head(key)` | The object's headers (with `ETag`), or `None` |
| `list(prefix)` | Keys under a prefix |
| `delete(keys)` | Remove. A key already gone is not an error |
| `put_files(files, *, content_type)` | Optional. Write `(key, path)` pairs that are written once. The HTTP store asks the host for upload URLs ([batch](/spec/http#uploading-straight-to-storage)) and sends the files straight to storage |

## fdl.repo

```python
repo.init(store, *, data_dir="tables/") -> Config
```

Start a dataset. An existing one is left as it is and its settings returned.

```python
repo.clone(store, working, *, ref="HEAD", local_data=None, version=None) -> Version | None
```

Make a working metadata file at `working` from the version `ref` holds, or from `version` when given. Returns that version, or `None` before the first one. A store over HTTP needs `local_data`, the folder new data files go to. Refuses a metadata file whose sha256 is not the one `version.json` records, or that names a data file outside the data folder.

```python
repo.commit(working, out_dir, *, parent, data_path, author, message, allow_empty=True, working_data=None, setup=(), facts=None, inherited=None, merged=None) -> Version
```

Freeze the working metadata into `out_dir/<ID>/` and write its `tables.json` with `facts`, the declarations (`declared.Facts`), after checking them against its tables and views; raises `declared.DeclarationError` with the problems found. `data_path` is `repo.data_url(store)`. `inherited` are the parent's declarations (`repo.declarations(store, parent)`), for telling whether anything changed and for the diff. Nothing is uploaded.

```python
repo.record(frozen, out_dir, *, parent, author, message, described=None, inherited=None, merged=None) -> Version
```

Make a version around a metadata file that is already frozen, for a writer that freezes it itself.

```python
repo.declarations(store, version) -> declared.Facts
repo.tables_of(store, version) -> set[str]
```

What a pushed version declares, and its tables and views, from its `tables.json`.

```python
repo.push(store, local, version, *, ref="refs/heads/main", move_ref=True) -> None
```

Upload a committed version, then move `ref` to it on condition. Raises `NotFastForward` when the ref moved, and `RepoError` when data files are missing or `tables.json` does not match `version.json` and the metadata. With `move_ref=False` it uploads and stops, for a host that moves refs itself.

```python
repo.log(store, name="HEAD") -> Iterator[Version]
repo.head(store, name="HEAD") -> Version | None
repo.is_ancestor(store, ancestor_id, descendant) -> bool
```

Walk the first-parent line, read the version a ref holds, and check whether a version is behind another, through every parent.

```python
repo.merge_bases(store, ours, theirs) -> list[Version]
repo.forward(store, ref, version) -> None
```

The common ancestors of two versions that no other common ancestor descends from: a merge needs exactly one. `forward` moves a branch to a version already in the dataset that descends from where it is, for a merge whose base is the branch's own version.

```python
merge.primary_keys(base, ours, theirs) -> dict[tuple[str, str], list[str] | None]
merge.tables(working, base, theirs, *, keys, dataset_data, data_path=None, setup=(), write=True) -> Plan
```

The pieces of `fdl merge`. `primary_keys` gives each table's primary key from what each version declares and holds (`(repo.declarations(store, version), repo.tables_of(store, version))`): `None` for one whose primary key differs between the three, which is a conflict. A table it does not list has no primary key, and its rows are merged as a multiset. `tables` writes into the working metadata, in one snapshot, what the frozen metadata `theirs` changed since `base`, or nothing when there is a conflict; `Plan` lists the conflicts and what came over. `repo.commit` and `repo.record` take `merged=<theirs>` to make the merge version.

```python
repo.readable(store, name="HEAD") -> Version
```

The version a ref holds, checked the way a reader must before opening its metadata. Raises `RepoError` before the first version, for a `version.json` of a format this does not read, and for a version past retention or withdrawn. `fdl url` uses it.

```python
repo.expire(store, version_id, *, reason="retention") -> None
repo.plan_retention(store, *, keep=1, since=None, grace=timedelta(days=7), now=None) -> list[str]
repo.prune(store, *, keep=1, since=None, grace=timedelta(days=7), reason="retention", by=None) -> list[str]
```

Take one version out of retention, or every version `plan_retention` chooses ([Choosing the versions to take out](/spec/data-files#choosing-the-versions-to-take-out)). `plan_retention` only lists them. Versions a ref points at are kept.

```python
repo.tag(store, name, version_id) -> None
repo.delete_tag(store, name) -> None
repo.tags(store) -> dict[str, str]
```

Create `refs/tags/<name>` at a version, delete it, or list the tags with the versions they hold. A tag is never moved: `tag` raises `RepoError` when the name is already there at another version.

```python
repo.branch(store, name, version_id) -> None
repo.delete_branch(store, name) -> None
repo.branches(store) -> dict[str, str]
```

The same for `refs/heads/<name>`. `delete_branch` keeps the branch `HEAD` names.

```python
repo.update_refs_index(store) -> None
```

Rewrite `refs.json` from the refs. `push`, `tag` and `delete_tag` call it after moving a ref. A 403 from a host that keeps `refs.json` itself is taken as done.

```python
repo.plan_withdrawal(store, version_ids, *, objects=None, refuse_refs=True) -> Withdrawal
repo.withdraw(store, version_ids, *, plan=None, by=None) -> Withdrawal
```

Withdraw versions that hold data that must not be published. `plan_withdrawal` changes nothing and returns the tags, the data files to delete, and the files kept because a kept version reads them. `withdraw` deletes the tags, the data files and the metadata files, in that order. Both raise `RepoError` while a branch or a request points at one of the versions; with `refuse_refs=False`, `plan_withdrawal` lists them in `blocked` instead and `withdraw` raises. `objects` (`{"data": [...], "assets": [...]}`, as `deleted` of `expired.json`) applies a withdrawal made in the dataset this one was copied from: every live version that reaches one of them is affected too.

## fdl.declared

```python
declared.Facts(primary_keys={}, assets={}, dependencies={}, unprofiled=frozenset())
declared.from_index(index) -> Facts
declared.check(facts, found) -> list[str]
declared.merged(base, ours, theirs) -> tuple[Facts, list[str]]
declared.changed(old, new, names) -> list[str]
declared.dependencies_changed(old, new) -> list[str]
```

`Facts` holds the declarations of a version: `primary_keys` and `assets` by `schema.table`, `dependencies` by name (`{"url": ..., "version": ...}`, where `version` is the version the commit bound the views against), and `unprofiled`, the tables and views whose `profile` is `false`. `declared()` gives them as a working metadata keeps them: each dependency with its `url` only. `of(name)` gives what it declares of one table or view as `tables.json` writes it, and `to_json` and `from_json` keep it in a file. `from_index` reads them from a `tables.json`. `check` returns what the [rules](/spec/versions#declarations) refuse, given `found_in(working, data_path=...)`, the tables and views of a working metadata. `merged` merges three ways and returns the conflicts. `changed` and `dependencies_changed` give the names a diff lists.

## fdl.diff

```python
diff.versions(store, old, new) -> dict
diff.working(working, base) -> dict
diff.lines(result) -> list[str]
```

`versions` compares two versions (`old` is `None` before the first one) from the newer one's `diff` when the older is its parent, and from their `tables.json` otherwise. `working` compares a working metadata file with `base`, the version it was cloned at. Both return `tables` (`added`, `dropped`), `rows`, `columns` and `data`, as `fdl diff --json` prints them; `versions` also returns `declarations` and `dependencies`. `lines` gives the lines `fdl diff` prints.

## fdl.refs

```python
refs.read(store, name) -> Ref | None
refs.write(store, name, version_id, *, expected) -> str
```

`read` returns the ref with the ETag a move has to name. `write` creates the ref when `expected` is `None` and moves it otherwise, and raises `RefConflict` when the condition fails.

```python
refs.listed(store) -> dict[str, str]
refs.write_index(store) -> dict
```

`listed` reads every branch and tag afresh. `write_index` writes `refs.json` from them and `HEAD`.
