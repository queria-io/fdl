---
title: バージョン
description: バージョン ID、version.json の項目、固めたカタログ、dataset.json、ほかの DuckLake 形式のカタログ。
---

バージョンは `versions/<version ID>/` の下のファイルです。一度書いたら変えてはいけません。例外は次のとおりです。

- 保持期間を過ぎたとき、`expired.json` を足し、カタログを消す（[データファイル](/ja/spec/data-files#保持期間を過ぎたとき)）
- 取り下げのとき、`"reason": "withdrawn"` の `expired.json` を足してカタログを消し、`version.json` 自体に取り下げるデータがあればそれも消す（[データファイル](/ja/spec/data-files#取り下げ)）
- `catalogs/<format>.duckdb` はあとから足してよい。既にあるファイルは変えない
- `version.json.sig` は、バージョンを作ったあとに署名する書き手が足してよい

## バージョン ID

- 小文字・ハイフン区切りの UUIDv7。たとえば `019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c`。作った順に並び、書き手どうしが調整しなくても重ならない
- 中身のハッシュではない。同じカタログでも別のバイト列で書かれることがあり、ハッシュは安定しない。中身は `catalog.sha256` で確かめる
- 人に見せる番号は `seq`。ブランチをまたぐと重なるので ID にはしない

## version.json

```json
{
  "fdl": "0.1",
  "id": "019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c",
  "seq": 12,
  "parents": ["019a2f10-0c4d-7e88-a1b2-55c0d9e3f7aa"],
  "created_at": "2026-10-30T09:00:00Z",
  "author": { "name": "example" },
  "message": "Add the final 2025 figures",
  "catalog": {
    "path": "ducklake.duckdb",
    "storage": "duckdb-v1.5.0",
    "bytes": 4194304,
    "sha256": "9f2c…"
  },
  "dataset": {
    "path": "dataset.json",
    "bytes": 2048,
    "sha256": "4c1d…"
  },
  "ducklake": {
    "format": "1.0",
    "snapshot_id": 345,
    "snapshots": [301, 345]
  },
  "files": {
    "path": "files.json",
    "bytes": 1536,
    "sha256": "7a0e…"
  },
  "tree": {
    "path": "tree.json",
    "bytes": 214,
    "sha256": "3c5d…"
  },
  "diff": {
    "tables": { "added": [], "dropped": [] },
    "rows": [{ "table": "main.population", "from": 1741, "to": 1802 }],
    "columns": [
      { "table": "main.population", "kind": "add_column", "column": "note", "breaking": false }
    ],
    "data": ["main.population"],
    "descriptions": ["main.population.note"]
  },
  "inputs": [
    {
      "source_id": "example:population-2025",
      "sha256": "1b7e…",
      "retrieved_at": "2026-10-29T03:12:00Z",
      "url": "https://example.org/population-2025.csv"
    }
  ],
  "annotations": {}
}
```

| 項目 | 必須 | 意味 |
| --- | --- | --- |
| `fdl` | must | 書いたときの仕様のバージョン |
| `id` | must | ディレクトリと同じ ID。違っていたら読み手は読んではいけない |
| `seq` | must | 人に見せる番号。最初は 1、そのあとは最初の親の `seq` に 1 を足す |
| `parents` | must | 親のバージョン ID。最初のバージョンでは空、そのあとは 1 つ |
| `created_at` | must | UTC、ISO 8601 |
| `author` | must | 作った人。`name` は必須、`email` などは任意 |
| `message` | must | 何を変えたか |
| `catalog` | must | カタログのファイル名、大きさ、sha256、保存形式（`storage`） |
| `dataset` | must | カタログの隣の `dataset.json` のファイル名、大きさ、sha256。必須になる前に書かれたバージョンには無い |
| `ducklake` | must | DuckLake の形式、固めたスナップショット、親から足したスナップショットの範囲（最初と最後） |
| `files` | may | カタログの隣の `files.json` を書いたときの、そのファイル名、大きさ、sha256（[files.json](#filesjson)） |
| `tree` | may | バージョンがほかのファイルを持つときの、カタログの隣の `tree.json` のファイル名、大きさ、sha256（[tree.json](#treejson)） |
| `diff` | may | 最初の親からの変更。最初のバージョンでは、そのバージョンの内容（[diff](#diff)） |
| `inputs` | may | 使った元のファイル。それぞれの取得元・取得日時・sha256 を書く。FDL はファイル自体を置かない。残したい書き手は、テーブルのアセットにする。`source_id` の付け方は書き手が決める |
| `annotations` | may | 形式が解釈しない情報。読む側（ホストやツール）の名前をキーにする |

読み手は、知らない項目を無視しなければなりません。

FDL 0.1 では、書き手が書く親は多くても 1 つです。`parents` をリストにしているのは、仕様の将来のバージョンで、項目を変えずにマージを表せるようにするためです。読み手は、どんな長さのリストも受け付け、履歴をたどるときは最初の要素を使わなければなりません。

スナップショットごとの説明と SQL は、DuckLake の `commit_message` と `commit_extra_info` としてカタログの中に残し、`version.json` には写しません。

### diff

`diff` は、バージョンとその最初の親との違いです。読み手は `version.json` だけで履歴を表示できます。最初のバージョンでは、すべての表を追加として記録します。どのキーでも `table` は `schema.table` です。

| キー | 内容 |
| --- | --- |
| `tables` | `added`・`dropped`: 片方にしかない表 |
| `rows` | `table`・`from`・`to`: 両方にあり、行数が違う表 |
| `columns` | 列の違い（下の表） |
| `data` | 両方にあり、データファイルが違う表。行が同じでも記録します |
| `descriptions` | `dataset.json` の `title` か `description` が違うもの: `dataset`、表、または `schema.table.column` |

書き手は、親のスナップショットのカタログと固めたカタログを比べます。カタログに親のスナップショットが残っていないときは `diff` を書きません。`descriptions` は、両方の `dataset.json` があるときだけ記録します。`diff` のないバージョンや、最初の親以外のバージョンと比べるときは、読み手が 2 つのバージョンの `dataset.json` と `files.json` を比べます。

`columns` の各項目は、両方にある表の、列 1 つの違いです。`column` は新しいバージョンでの列名です。

| `kind` | ほかに持つもの | `breaking` |
| --- | --- | --- |
| `add_column` | | `false` |
| `drop_column` | | `true` |
| `rename_column` | `from`: 前の列名 | `true` |
| `change_type` | `from_type`・`to_type`: DuckLake の型名 | 前の型の値がすべて新しい型に収まるとき（`int32` から `int64`、`float32` から `float64`）は `false`、それ以外は `true` |

読み手は、知らない `kind` を無視し、その `breaking` は読みます。`diff` の知らないキーも無視します。

### 形式番号

| 項目 | 指すもの | 変わるとき |
| --- | --- | --- |
| `fdl` | この仕様 | この仕様が変わったとき |
| `ducklake.format` | DuckLake の管理用の表の形 | DuckLake の仕様が変わったとき |
| `catalog.storage` | カタログファイルの保存形式 | DuckDB の保存形式が変わったとき |

読み手は、`version.json` だけで、そのバージョンを開けるか判断できます。

## files.json

`files.json` は、`ducklake.snapshot_id` の時点の表ごとのデータファイルの索引です。カタログを開けない読み手のためのものです。書き手はこれをカタログから作り、カタログと食い違ったときはカタログが正です。置くかどうかは任意で、読み手はこれが無くても動かなければなりません。Git の到達可能性ビットマップ（pack の隣の `.bitmap`）に当たります。あるコミットからたどれるオブジェクトの一覧をオブジェクトから作ったもので、読むのを速くし、無くても読めます。

```json files.json
{
  "tables": {
    "main.population": {
      "files": [
        {
          "path": "main/population/ducklake-019a3c4e-….parquet",
          "rows": 1741,
          "bytes": 52311,
          "footer_bytes": 2048,
          "deletes": { "path": "main/population/ducklake-019a3c4f-…-delete.parquet", "rows": 3, "bytes": 812 }
        }
      ]
    }
  }
}
```

- `tables` には、そのスナップショットにあるすべての表を `schema.table` をキーにして入れます。行の無い表の `files` は空です
- `path` はリポジトリの `data_dir` からの相対パスです。ファイルはカタログの順に並べます
- `rows` と `bytes` は、カタログが記録している行数と大きさです。`footer_bytes` は Parquet のフッターの大きさで、記録があるときだけ書きます
- `deletes` は、そのデータファイルから行を消したときの削除ファイルです
- `renamed_columns` は、その表の列の名前を変えたことがあるときに `true` にします。名前を変える前に書いたデータファイルは、古い名前のままです

DuckLake を使わずに Parquet ファイルを読む読み手は、`deletes` を持つファイルがある表や、`renamed_columns` が `true` の表を読んではいけません。DuckLake と同じように削除と改名を当てはめる場合は読んでかまいません。当てはめずに読むと、消した行が返ったり、名前を変えた列が欠けたりします。

保持期間から外すとき、`files.json` はカタログと一緒に消します。

## tree.json

`tree.json` は、バージョンがテーブルのほかに持つファイル（README とそこに表示する画像など）をパスで並べた一覧です。Git の tree に当たります。パスごとにファイルの sha256 を持ち、ファイル自体はアセットとして `assets/<sha256>` に置きます。テーブルはここに入れません。テーブルの tree はカタログです。

```json tree.json
{
  "entries": [
    { "path": "README.md", "sha256": "5e88…", "bytes": 3120 },
    { "path": "docs/images/towns.png", "sha256": "c9d2…", "bytes": 48211 }
  ]
}
```

- `entries` に、ファイルのパス・sha256（小文字の 16 進）・大きさを、パスの順に並べる。パスは相対パスで `/` で区切り、空・`.`・`..` のセグメントを含まず、重複しない
- ファイルは書いたままの中身で置く。Markdown のファイルの中の相対リンクは、そのファイル自身のパスを基準に解決し、パーセントエンコーディングをデコードしてから `entries` で引く。GitHub が README のリンクをコミットの tree で解決するのと同じである。`entries` に無いパスへのリンクはリンク切れとして扱う
- 多くの読み手は `README.md` を最初に表示する。どのファイルをどこに表示するかは読み手が決める
- `dataset.json` の `description` は短いテキストのまま残す。tree を読まない読み手もこれを持ち、一覧にはこれを表示する
- 書き手は、ファイルを編集する場所（Git のリポジトリの中の YAML の隣など）に置き、書き手のツールが `tree.json` を書く。書き手が手で書くものではない
- `tree.json` はバージョンの一部で、そのバージョンの中では変わらない。保持期間から外すときはカタログと一緒に消す

## profiles/

profile は、`ducklake.snapshot_id` の時点の 1 つのテーブルの要約です。行数、列ごとの中身、先頭の行、図形の列を持つテーブルでは図形の範囲と見本を持ちます。読み手はこの小さなファイル 1 つで、カタログを開かずデータも読まずにテーブルを表示できます。ホストは検索の索引をこれから作れます。profile が持つのはデータから測った事実で、列が何を意味するかは書き手が `dataset.json` に書きます。`files.json` と同じくカタログとデータから作るもので、食い違うときはカタログとデータが正です。任意で、読み手は profile が無くても読めなければなりません。

テーブルごとの profile は `versions/<ID>/profiles/<schema>/<table>.json` に置きます。スキーマ名かテーブル名が空・`.`・`..` のとき、または `/` を含むときは置きません。ビューには置きません。

```json profiles/main/towns.json
{
  "rows": 1741,
  "columns": [
    { "name": "town_code", "type": "varchar", "null_count": 0, "distinct_count": 1741,
      "values": [{ "value": "082201", "count": 1 }] },
    { "name": "prefecture", "type": "varchar", "null_count": 0, "distinct_count": 2,
      "values": [{ "value": "茨城県", "count": 1200 }, { "value": "栃木県", "count": 541 }] },
    { "name": "population", "type": "int64", "null_count": 3, "distinct_count": 1650,
      "min": 0, "max": 84211, "mean": 3120.5, "std": 4410.2, "q1": 420, "median": 1630, "q3": 3990,
      "values": [{ "value": "0", "count": 12 }],
      "histogram": [{ "start": 0, "end": 4210.55, "count": 1302 }] },
    { "name": "surveyed_on", "type": "date", "null_count": 0, "distinct_count": 1,
      "min": "2025-10-01", "max": "2025-10-01", "span_days": 0,
      "values": [{ "value": "2025-10-01", "count": 1741 }] },
    { "name": "coastal", "type": "boolean", "null_count": 0, "true_count": 210, "false_count": 1531 },
    { "name": "geom", "type": "geometry" }
  ],
  "head": [{ "town_code": "082201", "prefecture": "茨城県", "population": 2210, "surveyed_on": "2025-10-01", "coastal": false }],
  "geometry": {
    "column": "geom",
    "count": 1741,
    "bbox": [139.69, 35.74, 140.85, 37.15],
    "types": ["MULTIPOLYGON", "POLYGON"],
    "sample": { "type": "FeatureCollection", "features": [] }
  }
}
```

- `rows` はテーブルの行数。`columns` にはカタログの順にすべての列を並べ、`name` と `type`（DuckLake の型名）は `dataset.json` と同じにする
- 図形の列を除くすべての列が `null_count` を持つ。数値・テキスト・日付と時刻の列は `distinct_count` と `values` を持つ。大きなテーブルの `distinct_count` は推定値のことがある
- `values` は、値（テキスト）とその値を持つ行の数を、多い順、同数なら値の順に並べる。列が持つ異なる値が 2,000 以下なら全部を並べ、そのとき `distinct_count` は正確な数になる。読み手は、`values` の長さが `distinct_count` と等しいことで全部の一覧だと判定する。それより多い列は上位 10 件だけを並べる。ホストは全部の一覧を、地域コードや名前などの値で検索できる
- 数値の列は `min`・`max`・`mean`・`std`、四分位数の `q1`・`median`・`q3`、`min` から `max` までを等幅に区切った `histogram` を持つ。日付と時刻の列は `min` と `max` をテキストで持ち、日付とタイムスタンプは `span_days` も持つ。真偽値の列は `true_count` と `false_count` を持つ
- `head` は、テーブルが返す先頭の行を JSON のオブジェクトで持つ。図形の列と、値を JSON で表せない列は入れない
- `geometry` は、書き手が読めたときに、最初の図形の列を説明する。図形を持つ行の数、全体の `bbox`（列の座標で `[xmin, ymin, xmax, ymax]`）、含まれる図形の種類、地図を描くための、無作為に選んで簡略化した図形の GeoJSON `FeatureCollection` を持つ
- 書き手は計算できないキーを省いてよい。読み手は知らないキーを無視する
- 書き手は、データファイル（`files.json`）と列が親と同じテーブルには親の profile を使い回す。コミットで読むのは変わったテーブルだけになる。親の profile の `values` が書き手と異なる上限で書かれているときは、profile を作り直す
- profile はバージョンの一部で、そのバージョンの中では変わらない。保持期間から外すときはカタログと一緒に消す

## 固めたカタログ

`ducklake.duckdb` は、DuckDB のファイル形式の DuckLake カタログです。

- 管理用の行を `ducklake.snapshot_id` まで持ち、それより後は持たない
- 行をカタログの中に持たない。データはすべてデータファイルにある
- 参照するデータファイルはすべて `data_dir` の下にあり、カタログのデータパスからの相対で記録されている。書き手はこれを確かめなければならず、ホストも確かめてよい
- `data_path` は、書き手が公開したデータフォルダの絶対 URL（ルートと `data_dir`）。読み手はこれを上書きする（[読む](/ja/spec/reading)を参照）
- DuckLake がスナップショットにタグを付けられるようになるまで、書き手は作業カタログで、バージョンを固めたスナップショットを消すような `ducklake_expire_snapshots` を流してはいけない

## dataset.json

バージョンの中身を言葉で説明するファイルです。データセット、その表、表の列を説明します。バージョンの一部としてバージョンと一緒に一度だけ書くので、読み手はカタログを開かなくても、何がどんな条件で入っているかが分かり、古いバージョンも公開したときの説明を持ち続けます。

書き手が説明を書き、書き手のツールがそれを固めたカタログと照らし合わせ、カタログから分かることを書き足します。

```json
{
  "name": "population",
  "title": "Population by town",
  "description": "Census population of each town, by age and sex",
  "keywords": ["population", "census"],
  "language": "en",
  "homepage": "https://example.org/population",
  "issued": "2026-10-30",
  "update_frequency": "yearly",
  "licenses": [{ "id": "CC-BY-4.0", "spdx": "CC-BY-4.0", "url": "https://creativecommons.org/licenses/by/4.0/" }],
  "sources": [{ "title": "2025 census", "path": "https://example.org/census-2025" }],
  "contributors": [{ "title": "Example City", "roles": ["publisher"] }],
  "temporal_coverage": { "start": "2025", "end": "2025" },
  "spatial_coverage": ["JP"],
  "schemas": [{ "name": "main", "title": "Tables" }],
  "tables": [
    {
      "schema": "main",
      "name": "population",
      "title": "Population",
      "description": "One row per town, age group and sex",
      "primary_key": ["town_code", "age_group", "sex"],
      "depends_on": [],
      "sql": "SELECT …",
      "rows": 120000,
      "fields": [
        { "name": "town_code", "title": "Town code", "description": "…", "type": "varchar", "nullable": false }
      ]
    }
  ],
  "annotations": {}
}
```

- 必須なのは `name` と `title` だけで、ほかは省いてよい
- 書き手は説明を YAML（`dataset.yml`）で持ってもよく、テーブルごとに別のファイル（`<name>.table.yml`、`tables` の 1 項目）に分けてもよい。バージョンには常に説明全体が 1 つの JSON で入る
- `licenses` の各要素は SPDX の識別子か、`id` に書き手が足したいもの（`spdx`・`url`・`title`）を添えたオブジェクト
- `tables` には、カタログの最後のスナップショットにある表をすべて並べる。書き手が説明しなかった表は、書き手のツールがカタログから分かることで足す。カタログに無い表や列を説明していたら断る
- カタログのビューは表のあとに `"kind": "view"` を付けて並べ、`sql` はカタログのものを入れる。カタログはビューの列を記録しないので、ビューの `fields` は書き手が書いたままで、`type`・`nullable`・`rows` を持たない。表には `kind` を付けない
- カタログから取るもの: 表ごとの `rows`、列ごとの `type`（DuckLake の型名）と `nullable`。書き手が `description` を書かなかったところには、カタログのコメント（`COMMENT ON`）を使う
- 読み手は、この仕様が決めていないキーを無視する。書き手の道具は、綴りの誤りに気づけるよう、そのキーを断ってもよい。形式が解釈しない情報は、最上位・表・列それぞれの `annotations` に、読む側の名前をキーにして置く。書き手は書かれたまま残す
- `annotations` という名前になる前のバージョンは、同じものを `extensions` に持つ。読み手は `extensions` を `annotations` として読む
- ライセンスや出典に書いたことは書き手の主張で、ホストは公開する前に確かめてよい

### アセット

テーブルは、報告書の PDF や施設の写真などのファイルを指せます。Git LFS が大きなファイルを指すのと同じ形で、行にはファイルの sha256 を持ち、ファイルはデータと同じリポジトリに置きます。

```json
{ "name": "sha256", "title": "PDF", "type": "varchar", "asset": true }
```

- `"asset": true` の列は、各行にファイルの sha256（小文字の 16 進）か null を持つ。型は `varchar`
- 各ファイルはルートの `assets/<sha256>` に置く（[レイアウト](/ja/spec/layout#パス)）。同じ中身は 1 回だけ置き、複数のバージョンで共有する
- ファイルの名前・大きさ・メディアタイプは、同じテーブルの列として持つ。列名は書き手が決める。読み手がファイルを取得する前に表示方法を判定できるよう、少なくともメディアタイプの列を持つのがよい
- ファイルを差し替えるときは、新しい中身を置き、その sha256 を行に入れる。古いバージョンは古い中身を指したまま残る
- 書き手は、ファイルのメディアタイプを `Content-Type` に付けてアップロードする。バージョンの行が持つアセットは、そのバージョンを書く前にすべてリポジトリに置く
- 読み手は、リポジトリを読んでいる場所の `<root>assets/<sha256>` でファイルを取得し、表示する前に sha256 を確認する。ファイルが無い行は、リンク切れとして表示する
- 提供元のサイトにあるファイルなど、リポジトリの外のファイルは、普通の列に URL で持つ。これはアセットではなく、FDL は管理しない

## ほかの形式のカタログ

1 つのバージョンが DuckLake の形式ごとにカタログを持ってよく、読み手は DuckDB を替えずに、開けるファイルを選べます。

```
versions/<ID>/
  ducklake.duckdb            作ったときのカタログ（version.json が説明するもの）
  catalogs/1.0.duckdb        形式 1.0 で書いたもの
  catalogs/1.1.duckdb        形式 1.1 で書いたもの
```

- `catalogs/<format>.duckdb` はすべて、同じデータファイルを参照し、同じ行を返さなければならない
- 読み手は、開ける形式を新しい順に試し、どれも無ければ `ducklake.duckdb` を開く
- 宣言した形式をあるバージョンで作れない書き手は、そのファイルを置かない。どのファイルも開けない読み手は、そのバージョンを読めない
- 古い形式で表せないものを使ったバージョンには、その形式のカタログを置かない
- `fdl.json` の `until` を過ぎた形式は、新しいバージョンでは書かない。過去のバージョンはそのまま残してよい
- データファイルは、`fdl.json` で宣言した一番古い形式が読めるように書かなければならない
- `catalog.storage` は、宣言した読み手が開ける一番古い DuckDB の保存形式にするのがよい

DuckDB 2.0 で古い形式を書く方法は [DuckLake の形式](/ja/internals/ducklake-formats)にあります。
