---
title: Serving over HTTP
description: Letting readers attach a repository from a plain HTTP server or a CDN, and the caching each kind of file allows.
---

Readers only read files, so a repository can be served by any HTTP server, a bucket's public URL or a CDN in front of one. DuckDB fetches the catalog and the Parquet files with range requests.

## What a reader fetches

1. `HEAD`, or the ref it was asked for
2. `refs/heads/main`, if `HEAD` pointed there
3. `versions/<ID>/version.json`, to check the version is not expired and that it can open the format
4. `versions/<ID>/ducklake.duckdb`, with range requests
5. the Parquet files the query needs, with range requests

```sql
ATTACH 'ducklake:https://data.example.org/towns/versions/019a3c4e-…/ducklake.duckdb' AS towns (
  DATA_PATH 'https://data.example.org/towns/data/', OVERRIDE_DATA_PATH true, READ_ONLY
);
```

## Headers

| Path | `Cache-Control` | `Content-Type` |
| --- | --- | --- |
| `HEAD`, `refs/**` | `no-store` | `text/plain; charset=utf-8` |
| `refs.json` | `no-store` | `application/json` |
| `versions/**` | `public, max-age=31536000, immutable` | as the file |
| `data/**` | `public, max-age=31536000, immutable` | `application/octet-stream` |
| `fdl.json` | a short `max-age` | `application/json` |

A version folder and a data file never change once written, so they can be cached for as long as the CDN allows. Only `HEAD` and the refs change, and a cached ref sends readers to an old version without telling them. `expired.json` can appear in a version folder later, so a host that expires versions should purge `versions/<ID>/` from its CDN when it does.

The server must answer range requests (`Accept-Ranges: bytes`, `206 Partial Content`). DuckDB falls back to whole-file downloads without them, which is slow for large Parquet files.

## Listing

Readers do not list. A reader that knows a ref name gets everything else by following IDs, and one that does not reads `refs.json` for the names. Leave directory listings off; nothing needs them.

## Serving the latest catalog at a fixed URL

Some readers do not know FDL and expect one catalog URL. A host may serve the catalog of the version `HEAD` holds at `<root>/ducklake.duckdb`. It must then send the version ID in an `FDL-Version` response header, so a reader can tell which version it got, and must not let it be cached longer than the ref.

This is a convenience for readers, not part of the repository: the store holds no catalog outside `versions/`.

## CORS

For DuckDB WASM in a browser, allow `GET` and `HEAD` from the pages that read, and expose `Content-Range`, `Content-Length`, `ETag` and `FDL-Version`.
