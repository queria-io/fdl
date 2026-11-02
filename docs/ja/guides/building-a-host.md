---
title: ホストを作る
description: ほかの人のリポジトリを預かり、変更を承認し、ref を自分で動かすサービスで fdl Python パッケージを使う方法。
---

ホストは、ほかの人のリポジトリを預かるサービスです。誰が読み書きできるかを決め、変更を読み手に見せる前に確認することもよくあります。`fdl` パッケージはこのために作っています。コードを預かるサービスが Git を呼ぶのと同じように、ホストはこのパッケージを呼びます。パッケージの中に、特定のホストのことは何も書いてありません。

## ref を動かせない書き手

変更を確認するホストは、書き手に ref を書かせません。書き手はバージョンをアップロードして止まります。

```python
from fdl import repo
from fdl.stores import open_store

store = open_store("s3://lake/towns/")
version = repo.commit(working, out, parent=base, data_path=repo.data_url(store),
                      author={"name": "alice"}, message="Add 2026")
repo.push(store, out / version.id, version, move_ref=False)
```

バージョンはリポジトリにありますが、どの ref も指していないので、読み手には見えません。ホストは承認を待つ間、`refs/requests/<number>` でそのバージョンを指しておけます。リクエストの ref も普通の ref で、バージョン ID と改行を書きます。

## 承認する

承認したら、ホストが自分で `main` を動かします。条件は書き手と同じです。

```python
from fdl import refs

current = refs.read(store, "refs/heads/main")
refs.write(store, "refs/heads/main", version.id, expected=current)
```

ホストは、そのバージョンがいまの `main` の上に積まれているかを確かめます（`repo.is_ancestor`）。リクエストを出したあとに `main` が動いていれば、そのリクエストは古くなっていて、書き手が作り直します。

## 利用者の代わりに書く

定期の取り込みや Web アプリのフォームも、人と同じ手順で書きます。コンテナの中の SQLite ファイルにチェックアウトし、DuckDB で書き、コミットします。2 つのジョブが同時にコミットしたときは、ref の条件付き書き込みで決まります。負けたほうはチェックアウトし直し、SQL を流し直します。

## ホストのものを形式に持ち込まない

ホストが足すものは、FDL が決めた項目の外に置きます。

- バージョンのフォルダに置く追加のファイル（プレビューなど）。読み手は無視する
- `version.json` と `dataset.json` の `annotations`。ホストの名前をキーにする
- `fdl.json` の `id` に書く、ホスト側のデータセットの ID。`init` のときに書き、変えない

権限、承認、課金はホスト自身のデータベースに置きます。ホストから持ち出したリポジトリも、ホストにしか分からないものを含まずに正しく読めます。

## 保持期間

`repo.prune` はバージョンの保持期間をまとめて終わらせ、`repo.expire` は 1 つだけ終わらせます。どの有効なバージョンも参照していないデータファイルを消すのはホストの仕事です。ホストはリポジトリ全体の一覧を取り、有効なカタログをすべて読めます。ファイルを消してよい条件は[データファイル](/ja/spec/data-files)にあります。
