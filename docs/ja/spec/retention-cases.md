---
title: 保持のテストケース
description: 保持期間を終わらせるバージョンを選ぶ処理のテストケース。すべての実装が実行し、同じ結果になります。
---

[`cases/retention.json`](https://github.com/queria-io/fdl/blob/main/cases/retention.json) には、リポジトリと、それぞれで `keep` と `since` によって保持期間を終わらせる処理（[データファイル](/ja/spec/data-files#保持期間を終わらせるバージョンの選び方)）が保持期間を終わらせるバージョンが入っています。この処理の実装は、すべてのケースを実行し、ちょうどそれらの保持期間を終わらせます。

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
      "versions": [{ "id": "<ID>", "parents": [], "created_at": "2026-11-01T00:00:00Z" }],
      "expired": [],
      "refs": { "refs/heads/main": "<ID>", "refs/tags/first": "<ID>" },
      "take_out": ["<ID>"]
    }
  ]
}
```

| キー | 内容 |
| --- | --- |
| `now`、`grace_seconds` | 処理を実行する時刻と猶予期間 |
| `record` | すべてのバージョンに共通する `version.json` の項目。各バージョンの `version.json` は、これに `id`・`parents`・`created_at` を加えたもの |
| `keep`、`since` | ホストの決め方。時刻でバージョンを残さないときは `since` が null |
| `versions` | リポジトリのバージョン |
| `expired` | すでに保持期間を終えたバージョン。`expired.json` がある |
| `refs` | ref と、それぞれが指すバージョン |
| `take_out` | 保持期間を終わらせるバージョン |

保持期間を終わらせるバージョンを変えるときは、この仕様と同じ変更でここにケースを追加します。
