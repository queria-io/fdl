---
title: Building a host
description: Using the fdl Python package in a service that stores repositories for others, approves changes and moves refs itself.
---

A host is a service that keeps repositories for other people: it decides who can read and write them, and often reviews a change before readers see it. The `fdl` package is written for this. A host calls it the way a code hosting service calls Git. Nothing in the package knows about any host.

## Writers that cannot move refs

A host that reviews changes keeps writers from writing refs. The writer uploads a version and stops:

```python
from fdl import repo
from fdl.stores import open_store

store = open_store("s3://lake/towns/")
version = repo.commit(working, out, parent=base, data_path=repo.data_url(store),
                      author={"name": "alice"}, message="Add 2026")
repo.push(store, out / version.id, version, move_ref=False)
```

The version is in the repository but no ref points at it, so readers do not see it. The host can hold it under `refs/requests/<number>` while it waits for approval. A request ref is an ordinary ref: it holds a version ID and a newline.

## Approving

On approval, the host moves `main` itself, on the same condition a writer would:

```python
from fdl import refs

current = refs.read(store, "refs/heads/main")
refs.write(store, "refs/heads/main", version.id, expected=current)
```

The host checks that the version is built on what `main` holds now (`repo.is_ancestor`). If `main` moved since the request was made, the request is out of date and the writer builds again.

## Writing on behalf of users

A scheduled import or a form in a web app writes the same way a person does: check out into a SQLite file in a container, write with DuckDB, commit. Two jobs that commit at once are settled by the conditional write on the ref. The loser checks out again and reruns its SQL.

## Keeping the host out of the format

What a host adds goes outside the fields FDL defines:

- extra files in a version folder (a preview), which readers ignore
- `annotations` in `version.json` and `dataset.json`, keyed by the host's name
- its own ID for the dataset in `fdl.json`'s `id`, written at `init` and never changed

Access rules, approvals and billing stay in the host's own database. A repository copied off the host still reads correctly, with nothing in it that only the host understands.

## Retention

`repo.prune` takes versions out of retention and `repo.expire` takes out one. Deleting data files that no live version names is left to the host, which can list the whole repository and read every live catalog. See [Data files](/spec/data-files) for when a file may be deleted.
