---
title: HTTP で配信する
description: 普通の HTTP サーバーや CDN からリポジトリを ATTACH してもらう方法と、ファイルの種類ごとのキャッシュ。
---

読み手はファイルを読むだけなので、リポジトリはどんな HTTP サーバーからでも配信できます。バケットの公開 URL でも、その前に置いた CDN でもかまいません。DuckDB は、カタログと Parquet ファイルを範囲リクエストで取得します。

## 読み手が取得するもの

1. `HEAD` か、指定された ref
2. `HEAD` が指していれば `refs/heads/main`
3. `versions/<ID>/version.json`。保持期間を過ぎていないか、形式を開けるかを確かめる
4. `versions/<ID>/ducklake.duckdb`。範囲リクエストで
5. クエリに要る Parquet ファイル。範囲リクエストで

```sql
ATTACH 'ducklake:https://data.example.org/towns/versions/019a3c4e-…/ducklake.duckdb' AS towns (
  DATA_PATH 'https://data.example.org/towns/data/', OVERRIDE_DATA_PATH true, READ_ONLY
);
```

## ヘッダー

| パス | `Cache-Control` | `Content-Type` |
| --- | --- | --- |
| `HEAD`、`refs/**` | `no-store` | `text/plain; charset=utf-8` |
| `refs.json` | `no-store` | `application/json` |
| `versions/**` | `public, max-age=31536000, immutable` | ファイルに合わせる |
| `data/**` | `public, max-age=31536000, immutable` | `application/octet-stream` |
| `attachments/**` | `public, max-age=31536000, immutable` | 添付ファイルの `media_type` |
| `fdl.json` | 短い `max-age` | `application/json` |

バージョンのフォルダとデータファイルは一度書いたら変わらないので、CDN が許す限り長くキャッシュできます。変わるのは `HEAD` と ref だけです。ref をキャッシュすると、読み手は知らないうちに古いバージョンを読みます。`expired.json` はあとからバージョンのフォルダに増えることがあるので、保持期間を終わらせたホストは、そのとき `versions/<ID>/` を CDN から消してください。取り下げでは、消したデータファイルと `attachments/<sha256>` も CDN から消してください。

サーバーは範囲リクエストに応える必要があります（`Accept-Ranges: bytes`、`206 Partial Content`）。応えないと DuckDB はファイル全体を取得し、大きな Parquet ファイルでは遅くなります。

## 一覧

読み手は一覧を取りません。ref の名前が分かれば、あとは ID をたどって全部取得できます。名前が分からなければ `refs.json` を読みます。ディレクトリの一覧は使わないので、無効にしておいてください。

## 決まった URL で最新のカタログを出す

FDL を知らない読み手は、カタログの URL が 1 つであることを期待します。ホストは、`HEAD` が指すバージョンのカタログを `<root>/ducklake.duckdb` で出してもかまいません。その場合は、どのバージョンを返したか分かるように、レスポンスヘッダー `FDL-Version` にバージョン ID を付け、ref より長くキャッシュさせないでください。

これは読み手のための便宜で、リポジトリの一部ではありません。置き場には `versions/` の外のカタログはありません。

## CORS

ブラウザの DuckDB WASM から読むときは、読むページからの `GET` と `HEAD` を許可し、`Content-Range`、`Content-Length`、`ETag`、`FDL-Version` を公開してください。
