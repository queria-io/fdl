---
title: Pushing over HTTP
description: The requests a host answers so that writers can push without keys to its storage.
---

A host can take pushes over HTTP, so a writer needs no keys to its storage. The repository's root is a URL such as `https://data.example.org/towns/`, and every key in the layout is a path under it.

## Requests

| Request | Does | Answers |
| --- | --- | --- |
| `GET`, `HEAD` `<root>/<key>` | Read an object | 200 with an `ETag`, or 404 |
| `PUT` `<root>/<key>` | Write an object, with `If-None-Match: *` or `If-Match: <ETag>` | 200 or 201 with the new `ETag`; 412 when the condition fails |
| `GET` `<root>/?list&prefix=<p>[&after=<key>]` | List keys under a prefix | `{"keys": [...], "next": <key or null>}`; ask again with `after` while `next` is set |
| `POST` `<root>/<key>?uploads` | Start an upload in parts, with the same conditions as `PUT` | `{"upload": "<id>"}` |
| `PUT` `<root>/<key>?upload=<id>&part=<n>` | Send part `n` (from 1) | `{"etag": "<part tag>"}` |
| `POST` `<root>/<key>?upload=<id>` | Finish it with `{"parts": [{"part": n, "etag": "…"}]}` in order | As `PUT` |

- Writes carry `Authorization: Bearer <token>`. 401 without a usable token, 403 for one that may not write here
- Parts other than the last are the same size. A writer sends a file in parts when it is larger than one request may be (64 MB by default)
- A host decides which keys it takes. It may refuse writes to `HEAD`, `fdl.json` and anything outside the layout (but must answer `HEAD` requests, which a writer needs for a ref's ETag), and take `versions/` and data files only with `If-None-Match: *`
- Moving a ref is where a host checks a push: the version is there, it is a fast-forward, the catalog matches `version.json`, and every data file it names is in the repository. A refusal is 409 with the reason in the body
- There is no `DELETE`. Retention and deletion are the host's

## Writing to it

DuckDB cannot write data files to an HTTP URL, so a working catalog of such a repository keeps two places:

- Files already in the repository stay where they are. Checking out turns their references into absolute URLs under the repository's `data_dir`, so they are read over HTTP
- New files are written to a folder on the writer's machine, the working catalog's data path

Committing turns references under the repository's `data_dir` back into relative ones, and the new files are relative already. Pushing uploads the new files the repository lacks before the catalog.
