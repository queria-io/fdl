---
title: Reading without DuckDB
description: Reading a table's Parquet files from JavaScript with files.json, in Workers, browsers and Node.
---

A reader that cannot run DuckDB, such as a Cloudflare Worker or a web page, can still read a version: `files.json` lists each table's data files, and the Parquet files can be read by range. [`@queria-io/fdl-js`](https://www.npmjs.com/package/@queria-io/fdl-js) does this in JavaScript.

```bash
npm install @queria-io/fdl-js
```

```ts
import { httpStore, open, openParquet, readWhere, tableFiles } from "@queria-io/fdl-js";

const store = httpStore("https://data.example.org/towns/");
const version = await open(store); // HEAD, a branch, a tag or a version ID
const [file] = tableFiles(version, "main.towns") ?? [];
const rows = await readWhere(await openParquet(store, file), "town_code", "131016");
```

`readWhere` reads only the row groups whose min/max can hold the value, so a table sorted by the column it is read by costs a few small requests.

## What it can read

It follows [Reading](/spec/reading) up to the catalog and then reads `files.json` in its place:

- A version without `files.json` cannot be read. Writers before fdl-cli 0.16 did not make one
- A table whose files have `deletes`, or whose `renamed_columns` is `true`, is refused (see [files.json](/spec/versions#filesjson))
- A version with `expired.json` is refused

Tables written once and replaced whole by each version, as `CREATE OR REPLACE TABLE` does, have neither deletes nor renamed columns.

## Stores

| Store | Reads from |
| --- | --- |
| `httpStore(root)` | A URL, with `Range` requests |
| `r2Store(bucket, prefix)` | An R2 binding in a Cloudflare Worker |
| `fileStore(dir)` from `@queria-io/fdl-js/node` | A local folder |

Refs move, so keep the version a reader opened for a few minutes at most. Data files never change, so their Parquet footers can be cached forever.
