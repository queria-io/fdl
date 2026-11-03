---
title: Python API
description: The fdl package - stores, refs, and the repository operations without a terminal.
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
| `fdl.repo` | the operations: `init`, `checkout`, `commit`, `push`, `log`, `expire`, `prune` |
| `fdl.refs` | reading and moving refs under their conditions |
| `fdl.version` | `Version`, the record in `version.json`, and new IDs |
| `fdl.catalog` | DuckLake catalogs in DuckDB and SQLite, and the copy between them |
| `fdl.layout` | where each file of a repository sits |
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

## fdl.repo

```python
repo.init(store, *, data_dir="data/") -> Config
```

Start a repository. An existing one is left as it is and its settings returned.

```python
repo.checkout(store, working, *, ref="HEAD") -> Version | None
```

Make a working catalog at `working` from the version `ref` holds. Returns that version, or `None` before the first one.

```python
repo.commit(working, out_dir, *, parent, data_path, author, message, inputs=(), annotations=None, dataset=None) -> Version
```

Freeze the working catalog into `out_dir/<ID>/`, with `dataset` (the parsed `dataset.json`) checked against the catalog and completed from it. `data_path` is `repo.data_url(store)`. Nothing is uploaded.

```python
repo.record(frozen, out_dir, *, parent, author, message, inputs=(), annotations=None, dataset=None) -> Version
```

Make a version around a catalog that is already frozen, for a writer that freezes it itself.

```python
repo.push(store, local, version, *, ref="refs/heads/main", move_ref=True) -> None
```

Upload a committed version, then move `ref` to it on condition. Raises `NotFastForward` when the ref moved, and `RepoError` when data files are missing. With `move_ref=False` it uploads and stops, for a host that moves refs itself.

```python
repo.log(store, name="HEAD") -> Iterator[Version]
repo.head(store, name="HEAD") -> Version | None
repo.is_ancestor(store, ancestor_id, descendant) -> bool
```

Walk the first-parent line, read the version a ref holds, and check whether a version is behind another.

```python
repo.expire(store, version_id, *, reason="retention") -> None
repo.prune(store, newest, *, keep=1, since=None, reason="retention") -> list[str]
```

Take one version out of retention, or every version behind `newest` that falls out of it. Versions a ref points at are kept.

```python
repo.tag(store, name, version_id) -> None
repo.delete_tag(store, name) -> None
repo.tags(store) -> dict[str, str]
```

Create `refs/tags/<name>` at a version, delete it, or list the tags with the versions they hold. A tag is never moved: `tag` raises `RepoError` when the name is already there at another version.

```python
repo.update_refs_index(store) -> None
```

Rewrite `refs.json` from the refs. `push`, `tag` and `delete_tag` call it after moving a ref. A 403 from a host that keeps `refs.json` itself is taken as done.

```python
repo.plan_withdrawal(store, version_ids) -> Withdrawal
repo.withdraw(store, version_ids, *, plan=None) -> Withdrawal
```

Withdraw versions that hold data that must not be published. `plan_withdrawal` changes nothing and returns the tags, the data files to delete, and the files kept because a kept version reads them. `withdraw` deletes the tags, the data files and the catalogs, in that order. Both raise `RepoError` while a branch or a request points at one of the versions.

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
