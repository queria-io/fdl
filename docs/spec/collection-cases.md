---
title: Collection cases
description: Test cases for deleting shared objects, which every implementation runs and agrees on.
---

[`cases/collection.json`](https://github.com/queria-io/fdl/blob/main/cases/collection.json) holds repositories and, for each, the shared objects a process that deletes them ([Data files](/spec/data-files#when-a-shared-object-may-be-deleted)) deletes. An implementation that deletes shared objects runs every case and deletes exactly those. Stopping with an error counts as deleting nothing, so it meets a case only when that case deletes nothing.

```json
{
  "now": "2026-11-12T00:00:00Z",
  "grace_seconds": 86400,
  "cases": [
    {
      "name": "keeps every data file named, not only those at the snapshot",
      "files": { "fdl.json": {}, "versions/<ID>/version.json": {}, "versions/<ID>/files.json": {} },
      "objects": { "data/main/t/ducklake-a.parquet": "2026-11-01T00:00:00Z" },
      "deleted": ["data/main/t/ducklake-c.parquet"]
    }
  ]
}
```

| Key | Holds |
| --- | --- |
| `now`, `grace_seconds` | The time the process runs, and the grace period |
| `files` | The repository's files other than shared objects, by key. Each value is the file's JSON |
| `objects` | The objects under the data directory and `assets/`, by key, with the time each was placed. Their content does not matter |
| `deleted` | The keys deleted |

A change to what may be deleted comes with a case here, in the same change as this specification.
