---
title: コマンドライン
description: fdl のコマンド、オプション、断られるとき。
---

`<remote>` はリポジトリで、`s3://bucket/prefix/`、`https://host/path/`、またはフォルダです。S3 の認証情報とエンドポイントは boto3 の通常の場所から読みます（`AWS_*` の環境変数、R2 などの S3 互換ストレージには `AWS_ENDPOINT_URL`）。HTTP のリポジトリには、`FDL_TOKEN` か credential helper でトークンを渡します（[HTTP のトークン](#http-のトークン)）。

## HTTP のトークン

fdl はトークンを保存せず、ログインのコマンドもありません。HTTP のリポジトリには、次の順に見つけたトークンを `Authorization: Bearer` で送ります。

1. `FDL_TOKEN` があればそれ（CI 向け）
2. 無ければ、fdl の設定でリポジトリの URL に設定した credential helper が答えたもの。helper が無ければトークンを送りません

設定は `~/.config/fdl/config.toml`（`$XDG_CONFIG_HOME/fdl/config.toml`、または `FDL_CONFIG` で指定したファイル）です。helper は URL の前方一致で設定し、パスが一番長く一致したものを使います。

```toml
[credential."https://data.example.org"]
helper = "example-cli fdl-credential"
```

fdl は `<helper> get` を実行し、標準入力に `protocol=`・`host=`・`path=` の行と空行を渡して、出力の `password=` の行を読みます。何も出さないか、エラーで終わったら、トークンは無いものとします。トークンの保存と helper の書き込みは、ホストの側の道具が行います。

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
fdl commit <working.sqlite> -m "What changed" --author alice [--dataset dataset.json] [--allow-empty]
```

作業カタログを、チェックアウトしたバージョンを親とする新しいバージョンに固め、アップロードし、条件付きでブランチを動かします。ブランチ、新しいバージョン ID、`seq` を表示します。

バージョンはそれぞれ `dataset.json`（[バージョン](/ja/spec/versions#datasetjson)を参照）で説明します。`--dataset` で指定したファイルか、なければいまのディレクトリの `dataset.json` か `dataset.yml` から読みます。そのフォルダより下にある `*.table.yml`（または `*.table.json`）のテーブルの説明も合わせて読みます。隠しフォルダの中は読みません。コマンドはそれをカタログと照らし合わせ、列の型、行数、カタログのコメントを書き足します。

親と同じ中身になるとき（[変更がないとき](/ja/spec/writing#変更がないとき)）は `Nothing to commit` と表示し、ブランチを動かさずに終了コード 0 で終わります。同じ行を新しいファイルに書き直しただけの作り直しも、変更なしとみなします。`--allow-empty` を付けると、それでもバージョンを作ります。行を読めず判定できないときは、警告を出してバージョンを作ります。

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

## fdl tag

```bash
fdl tag <remote>                       # タグを並べる
fdl tag <remote> <name> [<version>]    # バージョンにタグを付ける（既定は HEAD が指すバージョン）
fdl tag <remote> <name> --delete
```

`<version>` にはバージョン ID か、`refs/heads/main` のような ref を渡します。タグは一度作ると動かしません。別のバージョンを指す同じ名前のタグがあるときは断られます。保持期間を過ぎたバージョンにはタグを付けられません。タグを作るか消すと `refs.json` を書き直します。`fdl commit` もブランチを動かしたあとに書き直します。

## fdl expire

```bash
fdl expire <remote> [--keep 1] [--since 2026-10-01T00:00:00Z] [--ref refs/heads/main]
```

ブランチの最初の親のつながりで、新しい `--keep` 個より古いバージョンの保持期間を終わらせます。`expired.json` を置き、カタログを消します。`--since` 以降に作ったバージョンと、ref が指すバージョンは残します。データファイルは消しません。
