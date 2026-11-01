---
title: DuckLake の形式
description: バージョンが持つ 3 つの形式番号と、DuckLake の形式が上がったときに古い DuckDB の読み手へどう届けるか。
---

## 3 つの形式番号

バージョンは、それぞれ別のものを指す 3 つの形式を記録します。読み手は、カタログを取得する前に、`version.json` だけでそのバージョンを開けるか判断できます。

| 項目 | 指すもの | 変わるとき |
| --- | --- | --- |
| `fdl` | この仕様（配置と `version.json`） | 仕様が変わったとき |
| `ducklake.format` | DuckLake の管理用の表の形（`1.0`、`1.1` など） | DuckLake の仕様が変わったとき |
| `catalog.storage` | カタログを入れた DuckDB ファイルの保存形式 | DuckDB の保存形式が変わったとき |

後の 2 つは別々に動きます。DuckDB 2.0 は、既定で DuckLake 形式 1.1 を書き、自分のファイルを DuckDB 1.5 が開けない保存形式で書きます。DuckDB 2.0 の既定のまま作ったカタログは、どちらの理由でも DuckDB 1.5 では読めません。

## 公開データで問題になる理由

読み手は自分の都合で DuckDB を上げます。選べない読み手もいます。ブラウザは、ページに入っている DuckDB WASM をそのまま使います。リポジトリが新しい形式に切り替えると、こうした読み手は読めなくなります。

データファイルは問題になりません。DuckLake の新しい形式で変わるのは管理用の表で、Parquet は変わりません。そこで、1 つのバージョンに形式ごとのカタログを置き、すべて同じデータファイルを参照させ、読み手が開けるものを選べるようにします。

```
versions/<ID>/
  ducklake.duckdb            作ったときのカタログ（version.json が説明するもの）
  catalogs/1.0.duckdb        同じバージョンを形式 1.0 で
  catalogs/1.1.duckdb        同じバージョンを形式 1.1 で
```

どの形式をいつまで出すかは `fdl.json` に書きます。

```json
"catalog_formats": [
  { "ducklake": "1.1" },
  { "ducklake": "1.0", "until": "2027-03-31" }
]
```

読み手は、開ける形式を新しい順に試し、どれも無ければ `ducklake.duckdb` を開きます。`until` を過ぎた形式は、新しいバージョンでは書きません。過去のバージョンはそのまま残します。

## DuckDB 2.0 で古い形式を書く

DuckDB 2.0 は、古い 2 つの形式をどちらも書けます。形式番号ごとに 1 つずつ設定します。

```sql
-- DuckLake の形式: カタログを 1.0 で作る
ATTACH 'ducklake:work/lake.sqlite' AS lake (DUCKLAKE_VERSION '1.0', …);

-- 保存形式: DuckDB 1.5 が開けるファイルに固める
ATTACH 'frozen.duckdb' AS dst (STORAGE_VERSION 'v1.5.0');
COPY FROM DATABASE src TO dst;
```

`SET ducklake_default_version = '1.0'` は、そのセッションで作るすべてのカタログに `DUCKLAKE_VERSION` を付けるのと同じです。既にある形式 1.0 のカタログは、DuckDB 2.0 で書いても 1.0 のままです。

この方法で作ったカタログは DuckDB 1.5 で開け、どのスナップショットでも DuckDB 2.0 と同じ行を返します。

## 書き手の決まり

- データファイルは、`fdl.json` で宣言した一番古い形式が読めるように書く。新しい形式でしか表せない機能（新しい列の型など）を使ったバージョンには、古い形式のカタログを置かない
- 1 つのバージョンの `catalogs/<format>.duckdb` は、すべて同じデータファイルを参照し、同じ行を返す
- `catalog.storage` には、そのファイルを開ける一番古い保存形式を書く
