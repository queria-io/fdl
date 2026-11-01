---
title: ミラー
description: リポジトリを別のホストに写し、最新に保ち、写しから読む方法。
---

ミラーは、別のホストに置いたリポジトリの写しです。バージョンとデータファイルは変わらないので、まだ無いファイルを写し、そのあと ref を写せば済みます。

## 写す

push と同じ順に写します。そうすれば、ミラーの読み手が、まだ揃っていないバージョンを ref からたどることはありません。

1. ミラーに無い `data/` の下のデータファイル
2. ミラーに無い `versions/` の下のバージョンのフォルダ。各フォルダで `version.json` を最後に
3. 変わっていれば `fdl.json`
4. `refs/`、`HEAD`、`refs.json`

```bash
aws s3 sync s3://origin/towns/data/     s3://mirror/towns/data/
aws s3 sync s3://origin/towns/versions/ s3://mirror/towns/versions/
aws s3 cp   s3://origin/towns/fdl.json  s3://mirror/towns/fdl.json
aws s3 sync s3://origin/towns/refs/     s3://mirror/towns/refs/ --delete
aws s3 cp   s3://origin/towns/HEAD      s3://mirror/towns/HEAD
aws s3 cp   s3://origin/towns/refs.json s3://mirror/towns/refs.json
```

ミラーは、元の `fdl.json` の `id` をそのまま持ちます。ツールはこれで、2 つの URL が同じリポジトリだと分かります。

ミラーは読むためのものです。元とミラーの両方に push すると、同じ `id` で別の履歴ができてしまいます。

## ミラーから読む

カタログには、書き手がデータファイルを公開した場所（元のデータフォルダ）が絶対 URL で記録されています。ミラーの読み手は、代わりにミラーのデータフォルダを DuckDB に渡します。

```sql
ATTACH 'ducklake:https://mirror.example.org/towns/versions/019a3c4e-…/ducklake.duckdb' AS towns (
  DATA_PATH 'https://mirror.example.org/towns/data/', OVERRIDE_DATA_PATH true, READ_ONLY
);
```

`OVERRIDE_DATA_PATH true` を付けないと、DuckDB はカタログに記録されたのと違うデータの場所を使いません。読み手は元でも常にこの 2 つを付けるので、同じ文がルートを変えるだけでどこでも動きます。

## 写したものを確かめる

`version.json` にはカタログの大きさと sha256 があります。ミラーは、カタログを写すたびに確かめられます。Parquet ファイルは UUID で名前が付き、自分のチェックサムを持ち、DuckLake はカタログにファイルの大きさを記録しています。

`version.json` は誰でも書けるので、チェックサムで分かるのは写しが記録と合っていることだけで、記録が元から来たことまでは分かりません。元が署名したバージョンなら、ミラーでも確かめられます。[署名](/ja/spec/reading#署名)を参照してください。

## 保持期間を過ぎたバージョンと取り下げたデータ

元がバージョンの保持期間を終わらせると、ミラーは次に写すときに、`expired.json` が増え、カタログが消えたことに気づきます。`expired.json` を写し、元が消したカタログを消してください。残しておくと、元がもう出していないバージョンをミラーが出し続けます。

`"reason": "withdrawn"` の `expired.json` は、公開してはいけないデータを元が取り下げたことを意味します。元が消したカタログとデータファイルを、待たずに消してください。
