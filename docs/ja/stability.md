---
title: 安定性
sidebar:
  order: 7
description: FDL と fdl-cli のリリースをまたいで変わらないものと、それぞれが動く環境。
---

FDL はベータです。このページには、今書いたリポジトリが何を前提にできるかと、`fdl-cli` のリリースで何が変わるかを書きます。

## 形式

FDL 0.1 で書いたリポジトリは、この先のリリースの `fdl` でも読めます。`fdl.json` と `version.json` の `fdl` は、そのファイルがどの仕様に沿って書かれたかを示し、読み手は知らないメジャーバージョンを読みません。

次のものは、パスも意味も変わりません。

- `fdl.json`・`HEAD`・`refs/`・`refs.json`
- `version.json` とその必須の項目
- 固めたカタログ `ducklake.duckdb`
- `dataset.json` の `name`・`title`・`tables` と、各表の `fields`
- `expired.json`
- データファイルと `assets/<sha256>`

バージョンの任意のファイル `files.json`・`tree.json`・`profiles/` は、カタログとデータから作るもので、読み手はこれらが無くても読めます。この先のリリースでキーが増えることがあります。読み手は、どのファイルでも知らないキーを無視します。

## fdl-cli

`fdl-cli` は `0.x` としてリリースしています。マイナーリリース（0.21 から 0.22）では、コマンド・オプション・Python の関数が変わることがあり、そのときは変更履歴の破壊的変更の欄に書きます。パッチリリース（0.21.0 から 0.21.1）は修正だけです。自動化では、マイナーリリースを固定してください。

```bash
pip install 'fdl-cli[s3]>=0.21,<0.22'
```

## 動く環境

- Python 3.10 以降
- DuckDB 1.5.2 以降の 1.x と `ducklake` 拡張。`fdl` が書くカタログは DuckLake の形式 1.0 で、DuckDB 1.5 以降で読めます

## 問題の報告

[github.com/queria-io/fdl](https://github.com/queria-io/fdl/issues) に Issue を立ててください。脆弱性は、[SECURITY.md](https://github.com/queria-io/fdl/blob/main/SECURITY.md) のとおり非公開で報告してください。
