---
title: リポジトリの中身
description: init からタグと保持期間の処理までの各段階で、リポジトリにどのファイルがどの順に書かれるか。
---

このページでは、1 つのリポジトリを最初から追い、各段階でどのファイルがどの順に現れるかを示します。それぞれの段階の決まりは[仕様](/ja/spec)にあります。このページは、その決まりが実際にどう働くかを見せます。

## init のあと

```
fdl.json
HEAD
```

```json fdl.json
{
  "fdl": "0.1",
  "id": "5f0c8a52-6d3e-4f8e-9b1a-2c7d4e9f0a13",
  "data_dir": "data/"
}
```

```text HEAD
ref: refs/heads/main
```

`fdl.json` は `If-None-Match: *` で書くので、`init` を 2 回流しても、既にあるリポジトリの `id` は置き換わりません。`HEAD` は `main` を指しますが、`main` はまだありません。この時点で `HEAD` をたどった読み手は、バージョンが無いことを知らされます。

## 書いている間

`fdl clone` のあと `CREATE TABLE` を流すと、DuckLake は Parquet をリポジトリに書きます。

```
fdl.json
HEAD
tables/main/towns/ducklake-0199f1….parquet
```

どの ref もバージョンもこのファイルを参照していないので、読み手は届きません。書き手が途中でやめると、ファイルは残り、猶予期間のあと保守が消します。

## 最初のコミット

1〜4 は `fdl commit` が書き手の手元で行い、残りは `fdl push` が行います。次の順に進みます。

1. 作業メタデータを SQLite から DuckDB のファイルへ写し、最後のスナップショットまでを残す
2. メタデータの `data_path` をリポジトリのデータフォルダにし、ほかの場所を絶対パスで指すファイルがあれば断る
3. 宣言をテーブルと照らし合わせて確認し、`tables.json`（テーブルごとのデータファイルと列、ビューごとの SQL と列、宣言（主キー・アセット列・profile・ビューが読む別のデータセット））を書く
4. 新しい UUIDv7 を ID にして、隣に `version.json` を書く
5. `tables.json` がメタデータの参照するデータファイルを並べていること、それがすべてリポジトリにあることを確かめる
6. `versions/<ID>/metadata.db` をアップロードする
7. `versions/<ID>/tables.json` をアップロードする
8. `versions/<ID>/version.json` をアップロードする
9. `refs/heads/main` を `If-None-Match: *` で作る

```
fdl.json
HEAD
refs/heads/main                                   019a2f10-…
versions/019a2f10-…/metadata.db
versions/019a2f10-…/tables.json
versions/019a2f10-…/version.json
tables/main/towns/ducklake-0199f1….parquet
```

読み手の安全はこの順番で守られます。読み手は ref から読み始め、ref は最後に書かれます。push が 6、7、8 の段階で止まっても、リポジトリに残るのは、どの ref も指していないバージョンのフォルダです。読み手には見えず、何も変わりません。

`version.json` をメタデータと `tables.json` のあとに書くのも同じ理由です。`versions/` の一覧を取るツールは、`version.json` の無いフォルダを書きかけとみなせます。

## 2 回目のコミット

Bob が `main` をクローンし、列を 1 つ足してコミットします。

```
refs/heads/main                                   019a3c4e-…
versions/019a2f10-…/…
versions/019a3c4e-…/metadata.db
versions/019a3c4e-…/tables.json
versions/019a3c4e-…/version.json
tables/main/towns/ducklake-0199f1….parquet          両方のバージョンが参照
tables/main/towns/ducklake-019a3b….parquet          2 つめだけが参照
```

```json versions/019a3c4e-…/version.json
{
  "fdl": "0.1",
  "id": "019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c",
  "seq": 2,
  "parents": ["019a2f10-0c4d-7e88-a1b2-55c0d9e3f7aa"],
  "created_at": "2026-10-15T09:00:00Z",
  "author": { "name": "bob" },
  "message": "Add a note column",
  "metadata": { "path": "metadata.db", "storage": "duckdb-v1.5.0", "bytes": 4194304, "sha256": "9f2c…" },
  "ducklake": { "format": "1.0", "snapshot_id": 12, "snapshots": [8, 12] },
  "tables": { "path": "tables.json", "bytes": 2048, "sha256": "4c1d…" }
}
```

`main` は `If-Match: <push を始めたときに読んだ ETag>` で動かします。その間に Alice が動かしていれば ETag が合わず、置き場は書き込みを断ります。Bob のバージョンのフォルダは参照されないまま残り、Bob はクローンし直します。

最初の Parquet ファイルは写しません。両方のメタデータが同じパスで参照しています。データファイルを決して上書きしてはいけないのはこのためです。

## タグ

```text refs/tags/2025-final
019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c
```

タグは `If-None-Match: *` で作り、動かしません。`refs/tags/2025-final` を ATTACH した読み手は、`main` がどうなっても、タグがある限り同じメタデータを読めます。ref が指すバージョンは、保持期間の処理で消されません。

## 保持期間を過ぎたバージョン

メタデータは小さくても、毎日作り直すデータセットは、どれかのバージョンが参照する Parquet ファイルをすべて持ち続けます。`fdl expire --keep 30` は、各ブランチの新しい 30 個より古いバージョンの保持期間を終わらせます。ref が指すバージョンと、その main との共通祖先は残します。それぞれ次の 3 段階を踏みます。

1. `versions/<ID>/expired.json` を置く
2. `versions/<ID>/metadata.db`・`tables.json`・`profiles/` を消す
3. 猶予期間のあと、まだ有効なバージョンが参照していないデータファイルを消す

```
versions/019a2f10-…/version.json
versions/019a2f10-…/expired.json      {"expired_at": "2026-12-01T00:00:00Z", "reason": "retention"}
```

`version.json` は残るので、`fdl log` には引き続きそのバージョンが出て、親のつながりも切れません。保持期間を過ぎたバージョンを求められた読み手は、`expired.json` を見て止まり、もう無いメタデータを取りに行きません。

印を先に書くのは、メタデータも説明も無いバージョンを読み手に見せないためです。データファイルを最後に、猶予期間を置いてから消すのは、書き手がファイルを置いて、それを参照するバージョンをこれからコミットするところかもしれないからです。

`fdl expire` がするのは最初の 2 段階で、3 段階目は `fdl gc` がします。1 つのファイルを多くのバージョンが参照しうるので、データファイルを消すには、リポジトリの一覧を取り、有効なバージョンの `tables.json` をすべて読める処理が要ります。
