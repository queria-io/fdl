---
title: 配置
description: リポジトリのファイル、そのパス、fdl.json の中身。
---

## パス

パスはルートからの相対パスで、小文字、区切りは `/` です。ルートはサーバー上の Git の bare リポジトリにあたり、たとえば `s3://bucket/lake/` です。

```
<root>/
  fdl.json                         リポジトリの設定
  HEAD                             既定の ref: "ref: refs/heads/main"
  refs.json                        すべての ref と指すバージョン。一覧を取れない読み手のため
  refs/
    heads/<name>                   ブランチ。動く。読み手が既定で見るのは main
    tags/<name>                    タグ。一度作ったら動かさない
    requests/<number>              ホストが使う（承認待ちなど、任意）
  versions/<version ID>/
    version.json                   バージョンの記録。最後に書く
    version.json.sig               記録の署名（任意）
    ducklake.duckdb                固めたカタログ
    dataset.json                   バージョンの中身の説明
    files.json                     カタログから作った、表ごとのデータファイルの索引（任意）
    tree.json                      README など、バージョンのほかのファイルのパスの一覧（任意）
    profiles/<schema>/<table>.json データから測ったテーブルごとの要約（任意）
    catalogs/<format>.duckdb       別の DuckLake 形式のカタログ（任意）
    expired.json                   保持期間を過ぎた印（過ぎたときだけ）
    <others>                       ホストが足すファイル
  <data_dir><schema>/<table>/ducklake-<uuid>.parquet   データファイル（共有、変えない）
  assets/<sha256>                  テーブルの行やバージョンの tree.json が指すファイル（共有、変更しない）
```

`HEAD` と `refs/` の下のファイルには、Git と同じく拡張子を付けません。中身は UTF-8 のテキストです。

ルートには、`versions/` の外に DuckLake のカタログを置いてはいけません。作業カタログも決して置いてはいけません。どちらも、公開していない状態を読み手に渡してしまいます。

ルートにほかの名前のファイルがあってもかまいません。読み手はそれを無視しなければなりません。

## fdl.json

```json
{
  "fdl": "0.1",
  "id": "5f0c8a52-6d3e-4f8e-9b1a-2c7d4e9f0a13",
  "data_dir": "data/",
  "signers": ["alice@example.org ssh-ed25519 AAAAC3…"],
  "catalog_formats": [
    { "ducklake": "1.1" },
    { "ducklake": "1.0", "until": "2027-03-31" }
  ]
}
```

| 項目 | 必須 | 意味 |
| --- | --- | --- |
| `fdl` | must | この仕様のバージョン。読み手は知らないメジャーバージョンを読んではいけない |
| `id` | should | リポジトリを作ったときに書き、変えない UUID |
| `data_dir` | may | データファイルの場所。ルートからの相対で `/` で終わる。既定は `data/` |
| `catalog_formats` | may | 新しいバージョンで出す DuckLake 形式と、いつまで出すか |
| `signers` | may | バージョンに署名する公開鍵。SSH の `allowed_signers` ファイルの行の形 |

`fdl.json` は `If-None-Match: *` で作るので、リポジトリを 2 回作っても最初の設定が残ります。

### id

`id` があれば、リポジトリの写しが同じリポジトリだと分かります。ミラーはそのまま持ちます。ホストは、リポジトリを作るときに、自分のデータセットの ID をここに書いてもかまいません。`id` の無いリポジトリも読めます。

### data_dir

既定は `data/` です。データファイルが既に別のプレフィックスにあるリポジトリは、ファイルを動かさずに、そのプレフィックスをここに書きます。カタログが参照するデータファイルは、すべて `data_dir` の下になければなりません。

### signers

リポジトリの書き手が署名に使う鍵です（[読む](/ja/spec/reading#署名)を参照）。読み手は、ここに書いてあるという理由で鍵を信用してはいけません。ミラーは `fdl.json` も書き換えられるからです。この一覧は、どの鍵が来るはずかを示すだけで、信用する鍵は、読み手が元のホストから別の手段で受け取ります。

バージョンを残す期間はここに書きません。ホストが決めます。

### catalog_formats

各項目に DuckLake の形式と、任意で新しいバージョンに書く最後の日（`until`、ISO 8601 の日付）を書きます。この項目が無ければ、バージョンが持つのは `ducklake.duckdb` だけです。[バージョン](/ja/spec/versions#ほかの形式のカタログ)を参照してください。
