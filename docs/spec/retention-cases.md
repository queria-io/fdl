---
title: Retention cases
description: Test cases for choosing the versions to take out of retention, which every implementation runs and agrees on.
---

[`cases/retention.json`](https://github.com/queria-io/fdl/blob/main/cases/retention.json) holds repositories and, for each, the versions a process that takes versions out of retention by `keep` and `since` ([Data files](/spec/data-files#choosing-the-versions-to-take-out)) takes out. An implementation of that choice runs every case and takes out exactly those.

```json
{
  "now": "2026-11-12T00:00:00Z",
  "grace_seconds": 86400,
  "record": { "fdl": "0.1", "seq": 1, "author": { "name": "example" }, "message": "", "metadata": {}, "ducklake": {} },
  "cases": [
    {
      "name": "keeps the newest on main and what a ref points at",
      "keep": 2,
      "since": null,
      "versions": [
        { "id": "<ID 1>", "parents": [], "created_at": "2026-11-01T00:00:00Z" },
        { "id": "<ID 2>", "parents": ["<ID 1>"], "created_at": "2026-11-02T00:00:00Z" },
        { "id": "<ID 3>", "parents": ["<ID 2>"], "created_at": "2026-11-03T00:00:00Z" },
        { "id": "<ID 4>", "parents": ["<ID 3>"], "created_at": "2026-11-04T00:00:00Z" }
      ],
      "expired": [],
      "refs": { "refs/heads/main": "<ID 4>", "refs/tags/first": "<ID 1>" },
      "take_out": ["<ID 2>"]
    }
  ]
}
```

| Key | Holds |
| --- | --- |
| `now`, `grace_seconds` | The time the process runs, and the grace period |
| `record` | The fields of `version.json` every version shares. Each version's `version.json` is this with its `id`, `parents` and `created_at` |
| `keep`, `since` | The host's choice. `since` is null when the host keeps no versions by time |
| `versions` | The versions in the repository |
| `expired` | The versions already past retention: they have `expired.json` |
| `refs` | The refs, each with the version it points at |
| `take_out` | The versions taken out |

A change to which versions are taken out comes with a case here, in the same change as this specification.
