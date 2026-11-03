---
title: From write to read
description: One change followed from a working catalog to a reader, with the requests at each step, and what each part of FDL is in Git.
---

This page follows one change from the writer's machine to a reader, and names the request made at each step. The files each step leaves in the repository are shown in [Inside a repository](/internals/inside-a-repository), and the rules are in the [specification](/spec).

## The parts and their Git counterparts

FDL keeps the roles and names of Git, and stores things its own way. Anyone who knows how Git keeps commits can read a repository by this table.

| FDL | Git | Notes |
| --- | --- | --- |
| Repository (a prefix of a store) | Bare repository on a server | No server process is needed |
| `fdl.json` | `config` | Holds the format, the repository's `id` and `data_dir` |
| `versions/<ID>/version.json` | Commit | Parents, author, message, and the sha256 of the catalog |
| `versions/<ID>/ducklake.duckdb` | Tree | Which tables there are and which data files they are made of |
| Data file (Parquet) | Blob | Written once and never changed. One file can be named by many versions |
| `refs/heads/<name>` | Branch | Moves forward only, on condition |
| `refs/tags/<name>` | Tag | Created once, never moved |
| `HEAD` | `HEAD` | Names the default branch |
| `refs.json` | `info/refs` | Lets a reader over plain HTTP find refs by name |
| `versions/<ID>/files.json` | Reachability bitmap | An index made from the catalog. A reader can do without it |
| `version.json.sig` | Signed commit | An SSH signature made with `ssh-keygen -Y sign` |
| Working catalog (SQLite) | Working tree and index | Made from a version and turned into the next one |
| `POST <root>/?batch` | Git LFS batch API | Hands out URLs for uploading straight to storage |
| `expired.json` | None | Says why a version has no catalog any more |

Three things differ on purpose:

- A version's ID is a UUIDv7, not a hash of its content. Two DuckDB releases can write the same catalog as different bytes, so a content hash would change with the tool. The sha256 in `version.json` checks the content instead
- There are no packfiles. Each file is its own object in the store, so a reader fetches only the parts of a Parquet file it needs
- There is no merge. A version has one parent, and a writer whose push is refused builds again on the newer version

## Writing a version

### Checkout

`fdl checkout` reads `HEAD`, then the ref it names, then that version's `version.json`, and copies its catalog into a SQLite file on the writer's machine. This is the working catalog. If the version has `expired.json`, checkout stops.

The working catalog's data path depends on the store:

- A folder or S3: the repository's data folder. DuckDB writes new Parquet files straight into the repository
- HTTP: a folder on the writer's machine. Files already in the repository are referred to by their URL under `data_dir` and are read over HTTP

### Writing

The writer runs SQL on the working catalog with DuckDB, directly or through a tool such as dbt. Each statement makes a DuckLake snapshot in the working catalog. Nothing a reader can see changes yet: the new Parquet files, if they are already in the repository, are named by no version.

### Commit

`fdl commit` copies the working catalog into a DuckDB file, sets its `data_path` to the repository's data folder, makes `dataset.json` from it, and writes `version.json` with a new ID and the previous version as its parent. If the tables hold the same rows as the parent's, there is no commit. The new version exists only on the writer's machine until it is pushed.

### Push

A push writes files in the order that keeps readers safe: whatever a file points at is written before it.

1. The new data files the repository lacks
2. `versions/<ID>/ducklake.duckdb`
3. `versions/<ID>/dataset.json` and `files.json`
4. `versions/<ID>/version.json`
5. The ref, with `If-Match: <ETag read before step 1>`, or `If-None-Match: *` for the first version
6. `refs.json`, rewritten from a fresh read of `HEAD` and every ref

Readers start from a ref, and the ref is written in step 5. A push that stops before then leaves files that no ref names, and readers never see them. If another writer moved the ref after step 1, the ETag no longer matches and the store refuses step 5. That writer checks out again and builds on the new version.

`refs.json` is written after the ref, so it may lag behind for a moment. That is why a reader that needs the current version reads the ref itself.

### Push over HTTP

Over HTTP, every key is a path under the repository's URL, and the host answers `GET`, `PUT` and the rest for it (see [Pushing over HTTP](/spec/http)). The order is the same as above. Two steps change:

- Before step 1, the writer sends `POST <root>/?batch` with the keys and sizes of the files it is about to upload. For each file, the host answers with an upload URL, says the file is already there, or answers 413 to have it sent through the host. Files with a URL go straight to storage with `PUT`, and the host is not in the path of the data. A host without batches answers 404, 405 or 501, and every file goes through the host
- In step 5, the host checks the push before it moves the ref: the version is complete, its parent is the version the ref holds now, the catalog matches `version.json`, and every data file the catalog names is in the repository. It answers 409 with the reason when a check fails. A host that moves refs itself also writes `refs.json`, and answers 403 when the writer tries to

A host can also keep writers from moving refs at all. The writer then uploads the version and stops after step 4, and the host moves the ref later, for example after someone approves the change.

## Reading a version

A reader needs only `GET`. It does not have to know who wrote the repository or whether a host is in front of it.

1. Read `HEAD`, or the ref asked for. `HEAD` holds `ref: refs/heads/main`, so read `refs/heads/main` next. A ref holds a version ID
2. Read `versions/<ID>/version.json`. Check that its `id` is the ID asked for and that the reader knows the major version in `fdl`
3. Check for `versions/<ID>/expired.json`. If it is there, stop: the version is past retention or withdrawn, and its catalog is gone
4. Attach the catalog read-only, with the repository's data folder as the data path

```sql
ATTACH 'ducklake:https://example.org/lake/versions/019a3c4e-…/ducklake.duckdb' AS lake (
  DATA_PATH 'https://example.org/lake/data/', OVERRIDE_DATA_PATH true, READ_ONLY
);
```

From here DuckDB reads the catalog and fetches the parts of the Parquet files a query needs. `fdl url` does steps 1 to 3 and prints the catalog's URL for step 4.

The reader passes the data path itself rather than using the one in the catalog. The catalog holds the URL where the writer published, and a mirror serves the same files somewhere else. With the data path given by the reader, the same statement works at the origin and at any mirror.

A version's catalog never changes, so it can be cached for good. `HEAD`, `refs/` and `refs.json` are served with `Cache-Control: no-store`, so a reader always sees where a ref points now.

### Readers that do not know FDL

A host may serve the catalog of the version `HEAD` holds at `<root>/ducklake.duckdb`. A reader that knows only DuckLake can then attach that one URL. The host sends the version ID in the `FDL-Version` header, so that a reader can find out which version it got. The store holds no file at that key: the host serves the catalog from `versions/`.

## Taking a version away

`fdl expire` takes old versions out of retention. It puts `expired.json` first, then deletes the catalog, and leaves `version.json`, so `fdl log` still shows the version and its place in the history. Data files that no live version names are deleted later, after a grace period, because a writer may be about to commit a version that names them.

Withdrawing removes data that must not be published, such as personal data published by mistake, from every version that holds it. The writer first makes a new version without the data and moves each affected branch forward to it, and deletes the tags that point at affected versions. Then the data files that hold the data are deleted at once, with no grace period, and each affected version gets `expired.json` with `"reason": "withdrawn"` and loses its catalog. The steps are in [Data files](/spec/data-files#withdrawal).

A reader that reaches an expired or withdrawn version stops at step 3 of reading and reports the reason.
