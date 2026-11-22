---
title: 収集のテストケース
description: 共有オブジェクトを消す処理のテストケース。すべての実装が実行し、同じ結果になります。
---

[`cases/collection.json`](https://github.com/queria-io/fdl/blob/main/cases/collection.json) には、データセットと、それぞれで共有オブジェクトを消す処理（[データファイル](/ja/spec/data-files#共有オブジェクトを消してよいとき)）が消すオブジェクトが入っています。共有オブジェクトを消す実装は、すべてのケースを実行し、ちょうどそれらを消します。エラーで止まったときは何も消さなかったものとして扱うので、何も消さないケースでだけ一致します。

```json
{
  "now": "2026-11-12T00:00:00Z",
  "grace_seconds": 86400,
  "cases": [
    {
      "name": "keeps the data files a live version's tables.json lists",
      "files": { "fdl.json": {}, "versions/<ID>/version.json": {}, "versions/<ID>/tables.json": {} },
      "objects": { "tables/main/t/ducklake-a.parquet": "2026-11-01T00:00:00Z" },
      "deleted": ["tables/main/t/ducklake-c.parquet"]
    }
  ]
}
```

| キー | 中身 |
| --- | --- |
| `now`、`grace_seconds` | 処理を実行する時刻と、猶予期間 |
| `files` | 共有オブジェクト以外のデータセットのファイル。キーごとに、ファイルの JSON |
| `objects` | データディレクトリと `assets/` の下のオブジェクトと、処理が触れてはいけないその外のファイル。キーごとに、置いた時刻。中身は問いません |
| `metadata` | メタデータのファイルのテーブル。メタデータのキーごとに、各テーブルの列の型と行。ケースは、アセットの列を持つテーブルなど、必要なテーブルだけを持ちます |
| `deleted` | 消すキー |

消してよいものを変えるときは、この仕様と同じ変更でここにケースを追加します。
