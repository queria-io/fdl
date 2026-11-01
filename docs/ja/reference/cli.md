---
title: コマンドライン
description: fdl のコマンド、オプション、断られるとき。
---

`<remote>` はリポジトリで、`s3://bucket/prefix/`、`https://host/path/`、またはフォルダです。S3 の認証情報とエンドポイントは boto3 の通常の場所から読みます（`AWS_*` の環境変数、R2 などの S3 互換ストレージには `AWS_ENDPOINT_URL`）。HTTP のリポジトリには、`fdl login` で保存したトークンか `FDL_TOKEN` を使います。

## fdl login

```bash
fdl login <host>
```

ホストから受け取ったトークンを尋ね、ホストごとに `~/.config/fdl/credentials.json` に保存します。`FDL_TOKEN` があればそちらを優先します（CI 向け）。

## fdl init

```bash
fdl init <remote> [--data-dir data/]
```

新しい `id` を入れた `fdl.json` と、`HEAD` を書きます。既にあるリポジトリに流すと、何も変えずに設定を表示します。

## fdl checkout

```bash
fdl checkout <remote> <working.sqlite> [--ref refs/heads/main]
```

ブランチが指すバージョンのカタログから作業カタログ（SQLite の DuckLake）を作ります。最初のバージョンの前は空のものを作ります。隣の `<working.sqlite>.fdl.json` に、リポジトリ、ブランチ、元のバージョンを記録します。書くときの `ATTACH` 文を表示します。

HTTP のリポジトリでは、新しいデータファイルは `<working.sqlite>.data/` に書かれ、コミットでアップロードされます。リポジトリに既にあるファイルは、その場所から HTTP で読みます。

バージョンの保持期間が過ぎていると断られます。

## fdl commit

```bash
fdl commit <working.sqlite> -m "What changed" --author alice [--dataset dataset.json]
```

作業カタログを、チェックアウトしたバージョンを親とする新しいバージョンに固め、アップロードし、条件付きでブランチを動かします。ブランチ、新しいバージョン ID、`seq` を表示します。

バージョンはそれぞれ `dataset.json`（[バージョン](/ja/spec/versions#datasetjson)を参照）で説明します。`--dataset` で指定したファイルか、なければいまのディレクトリの `dataset.json` か `dataset.yml` から読みます。そのフォルダより下にある `*.table.yml`（または `*.table.json`）のテーブルの説明も合わせて読みます。隠しフォルダの中は読みません。コマンドはそれをカタログと照らし合わせ、列の型、行数、カタログのコメントを書き足します。

次のときは断られます。

- `dataset.json` が無いとき、カタログに無い表や列を説明しているとき、形式が決めていないキーがあるとき
- チェックアウトのあとにブランチが動いたとき。チェックアウトし直して作業をやり直す
- 作業カタログが行をカタログの中に持っているとき
- カタログが参照するデータファイルがリポジトリに無いとき
- データファイルが、データディレクトリの外を指す絶対パスで記録されているとき

## fdl log

```bash
fdl log <remote> [--ref HEAD]
```

ref が指すバージョンから最初のバージョンまで、最初の親をたどって並べます。`seq`、ID、時刻、作者、メッセージを表示します。保持期間を過ぎたバージョンには `(expired)` が付きます。

## fdl url

```bash
fdl url <remote> [--ref HEAD]
```

ref が指すバージョンのカタログの URL を表示します。読み取り専用で `ATTACH` するのに使います。

## fdl expire

```bash
fdl expire <remote> [--keep 1] [--since 2026-10-01T00:00:00Z] [--ref refs/heads/main]
```

ブランチの最初の親のつながりで、新しい `--keep` 個より古いバージョンの保持期間を終わらせます。`expired.json` を置き、カタログを消します。`--since` 以降に作ったバージョンと、ref が指すバージョンは残します。データファイルは消しません。
