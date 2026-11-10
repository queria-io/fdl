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

`fdl checkout` のあと `CREATE TABLE` を流すと、DuckLake は Parquet をリポジトリに書きます。

```
fdl.json
HEAD
data/main/towns/ducklake-0199f1….parquet
```

どの ref もバージョンもこのファイルを参照していないので、読み手は届きません。書き手が途中でやめると、ファイルは残り、猶予期間のあと保守が消します。

## 最初のコミット

1〜4 は `fdl commit` が書き手の手元で行い、残りは `fdl push` が行います。次の順に進みます。

1. 作業カタログを SQLite から DuckDB のファイルへ写し、最後のスナップショットまでを残す
2. カタログの `data_path` をリポジトリのデータフォルダにし、ほかの場所を絶対パスで指すファイルがあれば断る
3. `dataset.json` をカタログと照らし合わせ、行数と列の型を書き足す
4. 新しい UUIDv7 を ID にして、隣に `version.json` を書く
5. カタログが参照するデータファイルがすべてリポジトリにあるか確かめる
6. `versions/<ID>/ducklake.duckdb` をアップロードする
7. `versions/<ID>/dataset.json` をアップロードする
8. `versions/<ID>/version.json` をアップロードする
9. `refs/heads/main` を `If-None-Match: *` で作る

```
fdl.json
HEAD
refs/heads/main                                   019a2f10-…
versions/019a2f10-…/ducklake.duckdb
versions/019a2f10-…/dataset.json
versions/019a2f10-…/version.json
data/main/towns/ducklake-0199f1….parquet
```

読み手の安全はこの順番で守られます。読み手は ref から読み始め、ref は最後に書かれます。push が 6、7、8 の段階で止まっても、リポジトリに残るのは、どの ref も指していないバージョンのフォルダです。読み手には見えず、何も変わりません。

`version.json` をカタログと `dataset.json` のあとに書くのも同じ理由です。`versions/` の一覧を取るツールは、`version.json` の無いフォルダを書きかけとみなせます。

## 2 回目のコミット

Bob が `main` をチェックアウトし、列を 1 つ足してコミットします。

```
refs/heads/main                                   019a3c4e-…
versions/019a2f10-…/…
versions/019a3c4e-…/ducklake.duckdb
versions/019a3c4e-…/dataset.json
versions/019a3c4e-…/version.json
data/main/towns/ducklake-0199f1….parquet          両方のバージョンが参照
data/main/towns/ducklake-019a3b….parquet          2 つめだけが参照
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
  "catalog": { "path": "ducklake.duckdb", "storage": "duckdb-v1.5.0", "bytes": 4194304, "sha256": "9f2c…" },
  "dataset": { "path": "dataset.json", "bytes": 2048, "sha256": "4c1d…" },
  "ducklake": { "format": "1.0", "snapshot_id": 12, "snapshots": [8, 12] }
}
```

`main` は `If-Match: <push を始めたときに読んだ ETag>` で動かします。その間に Alice が動かしていれば ETag が合わず、置き場は書き込みを断ります。Bob のバージョンのフォルダは参照されないまま残り、Bob はチェックアウトし直します。

最初の Parquet ファイルは写しません。両方のカタログが同じパスで参照しています。データファイルを決して上書きしてはいけないのはこのためです。

## タグ

```text refs/tags/2025-final
019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c
```

タグは `If-None-Match: *` で作り、動かしません。`refs/tags/2025-final` を ATTACH した読み手は、`main` がどうなっても、タグがある限り同じカタログを読めます。ref が指すバージョンは、保持期間の処理で消されません。

## 保持期間を過ぎたバージョン

カタログは小さくても、毎日作り直すデータセットは、どれかのバージョンが参照する Parquet ファイルをすべて持ち続けます。`fdl expire --keep 30` は、新しい 30 個より古いバージョンの保持期間を終わらせます。それぞれ次の 3 段階を踏みます。

1. `versions/<ID>/expired.json` を置く
2. `versions/<ID>/ducklake.duckdb` と `catalogs/` を消す
3. 猶予期間のあと、まだ有効なバージョンが参照していないデータファイルを消す

```
versions/019a2f10-…/version.json
versions/019a2f10-…/expired.json      {"expired_at": "2026-12-01T00:00:00Z", "reason": "retention"}
```

`version.json` は残るので、`fdl log` には引き続きそのバージョンが出て、親のつながりも切れません。保持期間を過ぎたバージョンを求められた読み手は、`expired.json` を見て止まり、もう無いカタログを取りに行きません。

印を先に書くのは、カタログも説明も無いバージョンを読み手に見せないためです。データファイルを最後に、猶予期間を置いてから消すのは、書き手がファイルを置いて、それを参照するバージョンをこれからコミットするところかもしれないからです。

`fdl expire` がするのは最初の 2 段階です。1 つのファイルを多くのバージョンが参照しうるので、データファイルを消すには、リポジトリの一覧を取り、有効なカタログをすべて読める処理が要ります。
