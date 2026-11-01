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

The name after `refs/heads/`, `refs/tags/` or `refs/requests/` is up to 128 characters. It may be split into levels with `/`, for example `refs/heads/alice/fix-2025`, and each level matches `[a-z0-9][a-z0-9._-]*`.

## Moving refs

- A ref must be written on condition: created with `If-None-Match: *`, moved with `If-Match: <the ETag read>`. When the condition fails, nothing is written and the writer is told
- The version a ref points at must be fully written, `version.json` included
- A branch moves only forward by default: a version whose `parents` do not include the version the branch holds now is refused. Moving back is allowed only when asked for explicitly
- A tag is created, never moved
- A refused writer checks out again and builds on what is there now. Merging rows is outside this specification
- A host may keep writers from writing refs and move them itself after its own checks

A store without conditional writes must not be pushed to: the last writer would silently replace someone else's version. It may still be read from.

A writer that sends a ref write again after losing the answer to the first one may find the condition refused by its own first attempt. If the ref now holds the version it was moving to, the move succeeded.
