---
title: FDL
sidebar:
  label: はじめに
description: DuckLake のカタログのバージョンを、Git がコミットを残すのと同じ形でオブジェクトストレージに残します。
---

FDL は、[DuckLake](https://ducklake.select/) のカタログの履歴をバケットやフォルダに残します。バージョンはそれぞれ、カタログを固めた写しと、親・作者・メッセージの記録でできています。読み手がどのバージョンを読むかは、`refs/heads/main` のような ref が決めます。Parquet ファイルはバージョンの間で共有し、書き換えません。

```
s3://my-bucket/towns/
  fdl.json
  HEAD                              ref: refs/heads/main
  refs/heads/main                   019a3c4e-…
  versions/019a3c4e-…/
    version.json                    親・作者・メッセージ・sha256
    ducklake.duckdb                 固めたカタログ
  data/main/towns/ducklake-….parquet
```

読み手に要るのは DuckDB だけです。`refs/heads/main` を読み、そのバージョンのカタログを HTTP か S3 で読み取り専用に ATTACH します。書き手に要るのは、読む・書く・条件付きで書くの 3 つができる置き場です。サーバーは要りません。

## FDL を使う場面

DuckLake は、変更をすべてスナップショットとして残します。スナップショットは書き込みのたびにできるので、データベースの単位としては合っていますが、人が扱う単位としては細かすぎます。FDL は、データを公開するときに人が使う単位を足します。「これは 2025 年のリリースで、Alice が作り、2024 年のリリースの上に積んだもの」という単位です。この単位に、変わらない ID と、履歴の中の位置と、読み手がたどれる名前を付けます。

FDL は、公開して多くの人が読み、少ない人が書くデータに向いています。

- バージョンごとにリリースするオープンデータや統計
- チームで整備し、読み手に見せる前に変更を確認するデータセット
- 同じデータセットを複数のホストに置く写し（ミラー）

FDL はトランザクションのためのデータベースではありません。2 人が同時に push したときは置き場が決めます。先に push したほうが通り、後のほうは断られます。後の書き手は新しいバージョンの上に作り直すか、自分のブランチで作業してマージします。マージではテーブルの主キーで行を照合します。

## FDL と Git

FDL は、Git がプロジェクトのコミットを残すのと同じ形で、カタログのバージョンを残します。Git を知っていれば、FDL のほとんどは次のように対応します。

| Git | FDL |
| --- | --- |
| サーバー上の bare リポジトリ | リポジトリ（バケットのプレフィックス、またはフォルダ） |
| コミット | バージョン（`versions/<ID>/version.json`） |
| ツリー | 固めたカタログ（`ducklake.duckdb`） |
| ブロブ | データファイル（Parquet）、アセット |
| ブランチ・タグ・`HEAD` | `refs/heads/`・`refs/tags/`・`HEAD` |
| 作業ツリー | 作業カタログ（手元の SQLite） |
| `git init --bare` | `fdl init` |
| `git commit`、`git push` | `fdl commit`、`fdl push` |
| `git log`・`git tag` | `fdl log`・`fdl tag` |
| `git branch`・`git merge` | `fdl branch`・`fdl merge` |

バージョンの親は 1 つです。別のブランチをマージしたバージョンは親を 2 つ持ちます。[書いてから読まれるまで](/ja/internals/from-write-to-read)で、コミットのときにファイルがどう動くかを順に追えます。

FDL はベータです。リリースをまたいで変わらないものは[安定性](/ja/stability)に書いています。

## ドキュメントの構成

- [はじめかた](/ja/getting-started): リポジトリを作り、最初のバージョンを書いて読み返します
- [概念](/ja/concepts/repositories): リポジトリ、バージョン、ref、作業カタログを説明します
- [仕組み](/ja/internals/inside-a-repository): コミットを何回かしたときに、リポジトリのファイルがどう増えるかを追います
- [ガイド](/ja/guides/stores): 置き場、HTTP での配信、ミラー、ホストの作り方を扱います
- [仕様](/ja/spec): 形式の規範となる説明です。ツールは仕様に合わせて作ります
- [リファレンス](/ja/reference/cli): コマンドと Python API の一覧です
- [安定性](/ja/stability): 今書いたリポジトリが何を前提にできるかを書いています

`fdl` コマンドと `fdl` Python パッケージが参照実装です。PyPI に `fdl-cli` として MIT ライセンスで公開しています。

```bash
pip install 'fdl-cli[s3]'
```
