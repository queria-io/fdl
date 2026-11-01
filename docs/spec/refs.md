---
title: Refs
description: The content of HEAD and refs, the names they may take, and the conditions for creating and moving them.
---

## Content

A ref holds a version ID and a newline, in UTF-8, with no other whitespace. `HEAD` holds `ref: ` and the path of a branch.

```
refs/heads/main      019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c\n
HEAD                 ref: refs/heads/main\n
```

| Path | Moves | Use |
| --- | --- | --- |
| `HEAD` | rarely | The default branch. A reader that names no ref follows `HEAD` |
| `refs/heads/main` | yes | The version readers normally see |
| `refs/heads/<name>` | yes | Shared work in progress |
| `refs/tags/<name>` | no | A version to pin, for example `2025-final`. May be deleted |
| `refs/requests/<number>` | as the host decides | Pending approvals and the like |

## Names

The name after `refs/heads/`, `refs/tags/` or `refs/requests/` is up to 128 characters. It may be split into levels with `/`, for example `refs/heads/alice/fix-2025`, and each level matches `[a-z0-9][a-z0-9._-]*`. Names are ASCII only, so that a ref's path is the same in every store and URL. A name in another script is kept elsewhere, such as in a host's own database, and mapped to an ASCII name.

## Moving refs

- A ref must be written on condition: created with `If-None-Match: *`, moved with `If-Match: <the ETag read>`. When the condition fails, nothing is written and the writer is told
- The version a ref points at must be fully written, `version.json` included
- A branch moves only forward by default: a version whose `parents` do not include the version the branch holds now is refused. Moving back is allowed only when asked for explicitly
- A tag is created, never moved
- A refused writer checks out again and builds on what is there now. FDL 0.1 does not merge: there is no way to make a version with two parents
- A host may keep writers from writing refs and move them itself after its own checks

A store without conditional writes must not be pushed to: the last writer would silently replace someone else's version. It may still be read from.

## refs.json

A reader over plain HTTP cannot list the store, so it cannot find branches and tags it does not know by name. `refs.json` at the root lists them, as Git's `info/refs` does for its dumb HTTP protocol.

```json refs.json
{
  "head": "refs/heads/main",
  "refs": {
    "refs/heads/main": "019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c",
    "refs/tags/2025-final": "019a2f10-0c4d-7e88-a1b2-55c0d9e3f7aa"
  }
}
```

- Whoever moves, creates or deletes a ref rewrites `refs.json` afterwards, from a fresh read of `HEAD` and every ref. A host that moves refs itself does this; so does a writer that moves its own
- `refs/requests/` are left out
- `refs.json` is a list for finding names, not the source of truth. It may lag behind the refs. A reader that needs the current version of a ref reads the ref itself
- It is served like a ref: not cached

## Retried writes

A writer that sends a ref write again after losing the answer to the first one may find the condition refused by its own first attempt. If the ref now holds the version it was moving to, the move succeeded.
