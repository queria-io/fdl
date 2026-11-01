---
title: はじめかた
description: リポジトリを作り、DuckDB で最初のバージョンを書いて、読み返します。
---

このページでは、フォルダにリポジトリを作り、表を 1 つ書き込み、読み手と同じ方法で結果を読みます。同じコマンドは S3 や R2 でも動きます。フォルダの代わりに `s3://bucket/prefix/` を渡してください。

## インストール

```bash
pip install 'fdl-cli[s3]'
```

`fdl` コマンドと `fdl` Python パッケージが入ります。Python 3.10 以上が必要です。フォルダしか使わない場合は `[s3]` を外してください。

## リポジトリを作る

```bash
fdl init ./towns/
```

`init` が書くのは次の 2 つのファイルだけです。

```
towns/
  fdl.json      {"fdl": "0.1", "id": "5f0c…", "data_dir": "data/"}
  HEAD          ref: refs/heads/main
```

`HEAD` は既定のブランチ `main` を指します。ブランチそのものはまだありません。指す先のバージョンがないためです。

## 作業カタログをチェックアウトする

リポジトリには直接書き込みません。自分のディスクにある SQLite ファイルの DuckLake カタログ（作業カタログ）に書き、書き終えたらそれをバージョンにします。

```bash
fdl checkout ./towns/ work/lake.sqlite
```

最初のバージョンができる前は、`checkout` は空のカタログを作ります。その後は、`main` が指すバージョンのカタログを写します。作業カタログを ATTACH する文を表示します。

```sql
ATTACH 'ducklake:work/lake.sqlite' AS lake (
  DATA_PATH '/abs/path/towns/data/', OVERRIDE_DATA_PATH true,
  META_TYPE 'sqlite', DATA_INLINING_ROW_LIMIT 0,
  META_JOURNAL_MODE 'WAL', BUSY_TIMEOUT 5000
);
```

作業カタログの隣の `work/lake.sqlite.fdl.json` に、リポジトリ、ブランチ、チェックアウト元のバージョンが記録されます。

## 書く

DuckDB のクライアントなら何でも使えます。Parquet ファイルはリポジトリの `data/` フォルダに直接書かれます。

```sql
CREATE TABLE lake.main.towns AS
SELECT * FROM 'towns.csv';
```

`DATA_INLINING_ROW_LIMIT 0` を付けると、DuckLake はすべての行を Parquet に書きます。既定のままだと小さな表をカタログの中に持ちますが、バージョンは行を、読み手が取得できるデータファイルとして持つ必要があります。

## 説明を書く

バージョンにはそれぞれ、中身を説明する `dataset.json` を付けます。`fdl commit` を実行する場所に置いてください。

```json dataset.json
{
  "name": "towns",
  "title": "Towns",
  "tables": [{ "schema": "main", "name": "towns", "title": "Towns and their codes" }]
}
```

必須なのは `name` と `title` だけです。`fdl commit` がこのファイルをカタログと照らし合わせ、表ごとの行数と列ごとの型を書き足します。

## コミットする

```bash
fdl commit work/lake.sqlite -m "First towns" --author alice
```

`commit` は作業カタログを固めてバージョンにし、アップロードして、`main` をそこへ動かします。`refs/heads/main -> 019a3c4e-… (version 1)` と表示します。リポジトリは次のようになります。

```
towns/
  fdl.json
  HEAD
  refs/heads/main                       019a3c4e-…
  versions/019a3c4e-…/
    ducklake.duckdb
    dataset.json
    version.json
  data/main/towns/ducklake-….parquet
```

```bash
fdl log ./towns/
```

```
   1  019a3c4e-…  2026-11-01T09:00:00Z  alice
      First towns
```

## 読む

```bash
fdl url ./towns/
```

`main` が指すバージョンのカタログを表示します。読み取り専用で ATTACH し、データファイルの場所を DuckDB に渡してください。

```sql
ATTACH 'ducklake:towns/versions/019a3c4e-…/ducklake.duckdb' AS towns (
  DATA_PATH 'towns/data/', OVERRIDE_DATA_PATH true, READ_ONLY
);
SELECT count(*) FROM towns.main.towns;
```

バージョンのカタログは変わらないので、読み手はいつまでもキャッシュしてかまいません。変わるのは `HEAD` と `refs/` の下のファイルだけです。

## 2 つめのバージョンを作る

```bash
fdl checkout ./towns/ work/lake.sqlite
```

```sql
INSERT INTO lake.main.towns SELECT * FROM 'towns-2026.csv';
```

```bash
fdl commit work/lake.sqlite -m "Add 2026" --author alice
```

新しいバージョンは、最初のバージョンを親として記録します。最初の Parquet ファイルは両方のカタログが参照し、新しいファイルは 2 つめのカタログだけが参照します。

途中でほかの人がコミットしていたら、このコミットは断られます。新しいバージョンが、いまの `main` の上に積まれていないためです。チェックアウトし直して、変更をやり直してください。理由は[バージョンと ref](/ja/concepts/versions-and-refs) で説明しています。
