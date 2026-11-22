---
title: バージョン
description: バージョン ID、version.json の項目、固めたメタデータ、ほかの DuckLake 形式のメタデータ。
---

バージョンは `versions/<version ID>/` の下のファイルです。一度書いたら変えてはいけません。例外は次のとおりです。

- 保持期間を過ぎたとき、`expired.json` を足し、メタデータを消す（[データファイル](/ja/spec/data-files#保持期間を過ぎたとき)）
- 取り下げのとき、`"reason": "withdrawn"` の `expired.json` を足してメタデータを消し、`version.json` に取り下げるデータがあれば、それを除いて書き換える（[データファイル](/ja/spec/data-files#取り下げ)）
- `metadata/<format>.db` はあとから足してよい。既にあるファイルは変えない
- `version.json.sig` は、バージョンを作ったあとに署名する書き手が足してよい
- `profiles/<schema>/<table>.json` は、profile の無いテーブルについてあとから足してよい。既にある profile は変えない

## バージョン ID

- 小文字・ハイフン区切りの UUIDv7。たとえば `019a3c4e-7b21-7f3a-9c55-2f0d8e1a4b6c`。作った順に並び、書き手どうしが調整しなくても重ならない
- 中身のハッシュではない。同じメタデータでも別のバイト列で書かれることがあり、ハッシュは安定しない。中身は `metadata.sha256` で確かめる
- 人に見せる番号は `seq`。ブランチをまたぐと重なるので ID にはしない
- 読み手は、これ以外の形の ID を受け付けてはならない。ref・フォルダ名・`parents` のどこから来たものでも同じ。ID はパスの一部になるため

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
  "metadata": {
    "path": "metadata.db",
    "storage": "duckdb-v1.5.0",
    "bytes": 4194304,
    "sha256": "9f2c…"
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
  "fdl_tables": {
    "path": "fdl_tables.json",
    "bytes": 2048,
    "sha256": "c41d…"
  },
  "diff": {
    "tables": { "added": [], "dropped": [] },
    "rows": [{ "table": "main.population", "from": 1741, "to": 1802 }],
    "columns": [
      { "table": "main.population", "kind": "add_column", "column": "note", "breaking": false }
    ],
    "data": ["main.population"]
  },
  "annotations": {}
}
```

| 項目 | 必須 | 意味 |
| --- | --- | --- |
| `fdl` | must | 書いたときの仕様のバージョン |
| `id` | must | ディレクトリと同じ ID。違っていたら読み手は読んではいけない |
| `seq` | must | 人に見せる番号。最初は 1、そのあとは最初の親の `seq` に 1 を足す |
| `parents` | must | 親のバージョン ID。最初のバージョンでは空、そのあとは 1 つ、マージでは 2 つ |
| `created_at` | must | UTC、ISO 8601 |
| `author` | must | 作った人。`name` は必須、`email` などは任意 |
| `message` | must | 何を変えたか |
| `metadata` | must | メタデータのファイル名（`metadata.db`）、大きさ、sha256、保存形式（`storage`） |
| `ducklake` | must | DuckLake の形式、固めたスナップショット、このバージョンのために作業メタデータが親の後に作ったスナップショットの範囲（最初と最後）。メタデータに入るのは固めたスナップショットだけ |
| `files` | may | メタデータの隣の `files.json` を書いたときの、そのファイル名、大きさ、sha256（[files.json](#filesjson)） |
| `fdl_tables` | may | メタデータの隣の `fdl_tables.json` を書いたときの、そのファイル名、大きさ、sha256（[fdl_tables.json](#fdl_tablesjson)） |
| `diff` | may | 最初の親からの変更。最初のバージョンでは、そのバージョンの内容（[diff](#diff)） |
| `annotations` | may | 形式が解釈しない情報。読む側（ホストやツール）の名前をキーにする。バージョンを作った元のファイルなど |

読み手は、知らない項目を無視しなければなりません。fdl-cli 0.31 以前が書いたバージョンは、使った元のファイルを `inputs` に持ちます。読み手は表示してもよく、表示しなくてもかまいません。以前のバージョンは `dataset` と `tree` を持ちます（[以前のバージョンのファイル](#以前のバージョンのファイル)）。

`metadata`・`files`・`fdl_tables`、および以前のバージョンの `dataset`・`tree` の `path` は、バージョンのフォルダの中のファイル名です。`/` や `\` を含まず、`.` や `..` でもありません。読み手は、それ以外の値を持つ記録を拒否しなければなりません。

バージョンの親は 1 つで、マージなら 2 つです。作ったブランチのバージョン、マージで取り込んだバージョンの順に並べます（[マージ](/ja/spec/writing#マージ)）。最初のバージョンには親がありません。読み手は、どんな長さのリストも受け付け、ブランチの履歴をたどるときは最初の要素を、あるバージョンが別のバージョンの子孫かを判定するときはすべての要素をたどらなければなりません。

固めたスナップショットの説明と SQL は、DuckLake の `commit_message` と `commit_extra_info` としてメタデータの中に残し、`version.json` には写しません。

### diff

`diff` は、バージョンとその最初の親との違いです。読み手は `version.json` だけで履歴を表示できます。最初のバージョンでは、すべての表を追加として記録します。どのキーでも `table` は `schema.table` です。

| キー | 内容 |
| --- | --- |
| `tables` | `added`・`dropped`: 片方にしかない表 |
| `rows` | `table`・`from`・`to`: 両方にあり、行数が違う表 |
| `columns` | 列の違い（下の表） |
| `data` | 両方にあり、データファイルが違う表。行が同じでも記録します |
| `fdl` | 行が違う [FDL のテーブル](/ja/spec/fdl-schema)の名前（`fdl_tables`） |
| `tags` | 以前の書き手が書くもの: 両方にあり、[tag](#以前のバージョンの-tag) が違う表とビュー |

書き手は、固める前の作業メタデータで、親のスナップショットと最後のスナップショットを比べます。作業メタデータは親のメタデータからクローンするので、親のスナップショットを持っています。持っていないときは `diff` を書きません。以前のバージョンは `diff` に `dataset` か `descriptions` を持ち、`dataset.json` が変わったかを表します。読み手はこれを無視します。`diff` のないバージョンや、最初の親以外のバージョンと比べるときは、読み手が 2 つのバージョンの `files.json` を比べます。

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
| `metadata.storage` | メタデータファイルの保存形式 | DuckDB の保存形式が変わったとき |

読み手は、`version.json` だけで、そのバージョンを開けるか判断できます。

## files.json

`files.json` は、`ducklake.snapshot_id` の時点の表ごとのデータファイルとビューごとの列の索引です。メタデータを開けない読み手のためのものです。書き手はこれをメタデータから作り、メタデータと食い違ったときはメタデータが正です。置くかどうかは任意で、読み手はこれが無くても動かなければなりません。Git の到達可能性ビットマップ（pack の隣の `.bitmap`）に当たります。あるコミットからたどれるオブジェクトの一覧をオブジェクトから作ったもので、読むのを速くし、無くても読めます。

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
      ],
      "columns": [
        { "id": 1, "name": "code", "type": "varchar", "nullable": false },
        { "id": 2, "name": "population", "type": "int64", "nullable": true, "default": "0" },
        {
          "id": 3, "name": "tags", "type": "list", "nullable": true,
          "children": [{ "id": 4, "name": "element", "type": "varchar", "nullable": true }]
        }
      ],
      "primary_key": ["code"]
    }
  },
  "views": {
    "main.population_by_prefecture": {
      "sql": "SELECT prefecture, sum(population) AS population FROM main.population GROUP BY prefecture",
      "columns": [
        { "name": "prefecture", "type": "VARCHAR" },
        { "name": "population", "type": "HUGEINT" }
      ]
    }
  },
  "catalogs": { "census": "https://example.org/census/" },
  "named": [
    "main/population/ducklake-019a3c4e-….parquet",
    "main/population/ducklake-019a3c4f-…-delete.parquet",
    "main/population/ducklake-0199f2a1-….parquet"
  ]
}
```

- `tables` には、そのスナップショットにあるすべての DuckLake の表を `schema.table` をキーにして入れます。`fdl_` のテーブルは DuckLake のテーブルではないので入りません。行の無い表の `files` は空です
- `path` はリポジトリの `data_dir` からの相対パスです。ファイルはメタデータの順に並べます
- `rows` と `bytes` は、メタデータが記録している行数と大きさです。`footer_bytes` は Parquet のフッターの大きさで、記録があるときだけ書きます
- `deletes` は、そのデータファイルから行を消したときの削除ファイルです
- `columns` は、そのスナップショットでの表の列をメタデータの順に並べたものです。`id` は DuckLake の列 ID で、DuckLake はこれをどの Parquet データファイルにも列の `field_id` として書きます。列の名前を変えても `id` は変わりません。`type` は DuckLake の型名で、`nullable` は `NOT NULL` の列で `false` になります。`default` は、列を追加する前に書いたデータファイルの行がその列に持つ値を文字列にしたもので、NULL でないときだけ書きます。`struct` はフィールドを、`list` は `element` を、`map` は `key` と `value` を `children` に持ちます。fdl-cli 0.24 より前の書き手は `columns` を書きません
- `primary_key` と `assets` は、その表の `fdl_tables` の `primary_key` と、`fdl_columns` で `asset` が true の列です（[FDL のテーブル](/ja/spec/fdl-schema)）。あるときだけ書きます。`fdl_` のテーブルより前に書かれたバージョン（[以前のバージョンの tag](#以前のバージョンの-tag)）では、その表の tag の `fdl.primary_key` と `fdl.assets` です
- `renamed_columns` は、その表の列の名前を変えたことがあるときに `true` にします。名前を変える前に書いたデータファイルは、古い名前のままです
- `views` には、そのスナップショットにあるすべてのビューを `schema.view` をキーにして入れます。`sql` はメタデータが記録しているビューの SQL、`columns` はそのクエリが返す列で、それぞれ `name` と `type`（DuckDB の型名）を持ちます。書き手は、`catalogs` のリポジトリを ATTACH してクエリをバインドし、列を求めます。クエリをバインドできないビューでは `columns` を省きます。以前の書き手は `views` を書きません。DuckLake は、クエリがデータセット自身のカタログを指す箇所を `{DUCKLAKE_CATALOG}` にしてビューの SQL を記録します（`{DUCKLAKE_CATALOG}.main.population`）。`sql` はこれをそのまま持ち、読み手はクエリを実行する前に、データセットを ATTACH した名前に置き換えます
- `catalogs` は `fdl_catalogs` で、各 `name` をその `url` に対応させます。行があるときだけ書きます
- `named` には、メタデータがどのスナップショットででも名指しするデータファイルと削除ファイルを、`data_dir` からの相対パスですべて入れます。これがそのバージョンから到達するデータファイルです（[データファイル](/ja/spec/data-files)）。メタデータが古いスナップショットを残しているときは、`tables` より多くなります

DuckLake を使わずに Parquet ファイルを読む読み手は、`deletes` を持つファイルがある表や、`renamed_columns` が `true` の表を読んではいけません。DuckLake と同じように削除と改名を当てはめる場合は読んでかまいません。当てはめずに読むと、消した行が返ったり、名前を変えた列が欠けたりします。`columns` の `id` を使い、データファイルの列を `field_id` で探す読み手は、名前を変えた列も DuckLake と同じように読めます。

保持期間から外すとき、`files.json` はメタデータと一緒に消します。

## fdl_tables.json

`fdl_tables.json` は、そのバージョンの [FDL のテーブル](/ja/spec/fdl-schema)の行を、メタデータを開かない読み手のために持ちます。書き手は固めるときにメタデータからこれを作り、食い違ったときはメタデータが正です。`files.json` と同じく任意です。書き手は、どれかの `fdl_` のテーブルに行があるときに書きます。

```json fdl_tables.json
{
  "fdl_dataset": [{ "title": "町ごとの人口", "language": "ja" }],
  "fdl_tables": [
    { "schema": "main", "name": "population", "primary_key": ["code"] }
  ],
  "fdl_columns": [
    { "schema": "main", "table": "population", "name": "code", "description": "町のコード" }
  ],
  "fdl_files": [
    { "path": "README.md", "sha256": "5e8a…", "bytes": 912, "media_type": "text/markdown" }
  ]
}
```

- キーは行のある `fdl_` のテーブルで、行を列名をキーにしたオブジェクトとして持つ。`NULL` は書かない
- `JSON` の列は、テキストではなく JSON の値として持つ（`["code"]`）
- 行は、各テーブルの主キーの順に並べる

保持期間から外すとき、`fdl_tables.json` はメタデータと一緒に消します。

## profiles/

profile は、`ducklake.snapshot_id` の時点の 1 つのテーブルの要約です。行数、列ごとの中身、先頭の行、図形の列を持つテーブルでは図形の範囲と見本を持ちます。読み手はこの小さなファイル 1 つで、メタデータを開かずデータも読まずにテーブルを表示できます。ホストは検索の索引をこれから作れます。profile が持つのはデータから測った事実で、列が何を意味するかは持ちません。`files.json` と同じくメタデータとデータから作るもので、食い違うときはメタデータとデータが正です。任意で、読み手は profile が無くても読めなければなりません。

テーブルごとの profile は `versions/<ID>/profiles/<schema>/<table>.json` に置きます。ビューにも置き、クエリが返す行から測ります。スキーマ名かテーブル名が空・`.`・`..` のとき、または `/` を含むときは置きません。profile を作らなかった書き手や作るのに失敗した書き手のバージョンには、あとから profile を足せます。ほかの profile と同じく、そのバージョンのメタデータとデータから測ります。

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

- `rows` はテーブルの行数。`columns` にはメタデータの順にすべての列を並べ、`name` と `type`（DuckLake の型名）は `files.json` と同じにする。メタデータに列の無いビューでは、クエリが返す列を DuckDB の型名で並べる
- 図形の列を除くすべての列が `null_count` を持つ。数値・テキスト・日付と時刻の列は `distinct_count` と `values` を持つ。大きなテーブルの `distinct_count` は推定値のことがある
- `values` は、値（テキスト）とその値を持つ行の数を、多い順、同数なら値の順に並べる。列が持つ異なる値が 2,000 以下なら全部を並べ、そのとき `distinct_count` は正確な数になる。読み手は、`values` の長さが `distinct_count` と等しいことで全部の一覧だと判定する。それより多い列は上位 10 件だけを並べる。ホストは全部の一覧を、地域コードや名前などの値で検索できる
- 数値の列は `min`・`max`・`mean`・`std`、四分位数の `q1`・`median`・`q3`、`min` から `max` までを等幅に区切った `histogram` を持つ。日付と時刻の列は `min` と `max` をテキストで持ち、日付とタイムスタンプは `span_days` も持つ。真偽値の列は `true_count` と `false_count` を持つ
- `head` は、テーブルが返す先頭の行を JSON のオブジェクトで持つ。図形の列と、値を JSON で表せない列は入れない
- `geometry` は、書き手が読めたときに、最初の図形の列を説明する。図形を持つ行の数、全体の `bbox`（列の座標で `[xmin, ymin, xmax, ymax]`）、含まれる図形の種類、地図を描くための、無作為に選んで簡略化した図形の GeoJSON `FeatureCollection` を持つ
- 書き手は計算できないキーを省いてよい。読み手は知らないキーを無視する
- 書き手は、データファイル（`files.json`）と列が親と同じテーブルには親の profile を使い回す。profile を作るときに読むのは変わったテーブルだけになる。ビューは、そのビューの `sql` が親と同じで、クエリが読むテーブルとビューがどれも変わっていないときに親の profile を使い回す。テーブルはデータファイルで、ビューは同じ規則で判定する。クエリから読むテーブルを判定できないビュー（テーブル関数を読むものなど）は、すべてのビューの `sql` が親と同じで、どのテーブルのデータファイルも変わっていないときだけ親の profile を使い回す。親の profile の `values` が書き手と異なる上限で書かれているときは、profile を作り直す
- 書き手は、`fdl_tables` の `profile` が `false` のテーブルとビュー（[FDL のテーブル](/ja/spec/fdl-schema)）の profile を作らない。書き手のツールは、ビルドが作る中間のテーブルなど、利用者に読ませないテーブルにこれを設定し、profile を作るときに読まないようにする。ホストも、自分が表示するものだけの profile を作ってよい
- profile はバージョンの一部で、そのバージョンの中では変わらない。保持期間から外すときはメタデータと一緒に消す

## 固めたメタデータ

メタデータは、バージョンのフォルダーにある `metadata.path` のファイル（`metadata.db`）です。DuckDB のファイル形式の DuckLake メタデータです。

- 読み手は `metadata.path` のファイルを開く。fdl-cli 0.26 以前が書いたバージョンでは名前が `ducklake.duckdb` で、項目名が `catalog` です。読み手は `catalog` を `metadata` として読む

- スナップショットは `ducklake.snapshot_id` の 1 つだけを持ち、管理用の行はそのスナップショットが読むものだけを持つ。前のスナップショット、それより前に終わった行、削除予定のファイルは入れない。dbt などのツールで作り直したテーブルはすべて新しいファイルに書かれるので、1 つのメタデータの中の前のスナップショットは最後のものとほとんど共有せず、ファイルを大きくするだけになる。fdl-cli 0.31 以前が書いたバージョンは前のスナップショットも持つ
- 行をメタデータの中に持たない。データはすべてデータファイルにある
- 参照するデータファイルはすべて `data_dir` の下にあり、メタデータのデータパスからの相対で記録されている。書き手はこれを確かめなければならず、ホストも確かめてよい
- `data_path` は、書き手が公開したデータフォルダの絶対 URL（ルートと `data_dir`）。読み手はこれを上書きする（[読む](/ja/spec/reading)を参照）
- 読み手は、前のバージョンをそのバージョンのメタデータを開いて読む（[読む](/ja/spec/reading#前のバージョン)）。別のバージョンのメタデータで `AT (VERSION => n)` を使って読まない

## FDL のテーブル

DuckLake が記録する列のほかに FDL が知る必要のある値（主キー、アセット、ビューが読むリポジトリ、profile を作らないテーブル）と、データセットが人にとって何を意味するかは、メタデータのファイルの中に、DuckLake 自身のテーブルと並べて、名前が `fdl_` で始まる普通のテーブルとして持ちます（[FDL のテーブル](/ja/spec/fdl-schema)）。DuckLake のテーブルではないので、DuckLake はこれを一覧に出さず、スナップショットも取らず、データファイルも書きません。行が変わればバージョンができ、セルごとにマージします。

DuckLake の `comment`（`COMMENT ON`）は FDL のものではありません。書き手はそのまま残し、FDL は読みません。

## 以前のバージョンの tag

`fdl_` のテーブルより前に書かれたバージョンは、FDL が読む値をテーブルやビューの DuckLake の tag として持ちます。`ducklake_tag` の行で、`object_id` はテーブルかビューの ID、キーは `fdl.` で始まり、値は JSON のテキストです。メタデータに `fdl_` のテーブルが無く、`fdl.` の tag があるバージョンがそれです。

| tag | 付ける先 | 読み替え |
| --- | --- | --- |
| `fdl.primary_key` | テーブル | `fdl_tables.primary_key` |
| `fdl.assets` | テーブル | 示す列ごとに `fdl_columns.asset` が true |
| `fdl.catalogs` | ビュー | `fdl_catalogs` の行 |
| `fdl.profile` | テーブルまたはビュー | `fdl_tables.profile` |

- 読み手はメタデータの `duckdb_tables()`・`duckdb_views()` の `tags` か、`ducklake_tag` の行から読む。テーブルの tag は `files.json` からも読める
- このバージョンからコミット・元に戻す・マージで新しいバージョンを作る書き手は、`fdl_` のテーブルにまだ無ければ読んだ値をそこに書き、`fdl.` の tag は書かない。作業メタデータが親から引き継いだ tag は新しいバージョンにも残るが、そのメタデータには `fdl_` のテーブルがあるので、読み手はその tag を無視する。`diff` と[変更がないとき](/ja/spec/writing#変更がないとき)の判定では、親から読んだ値と比べるので、主キーとアセットが同じテーブルは変わったことにならない。親の tag が示していた値だけを持つ `fdl_` の行は親のものとみなし、それだけではバージョンを作らない

### アセット

テーブルは、報告書の PDF や施設の写真などのファイルを指せます。Git LFS が大きなファイルを指すのと同じ形で、行にはファイルの sha256 を持ち、ファイルはデータと同じリポジトリに置きます。

```sql
-- テーブル main.reports、fdl_columns で pdf_sha256 をアセットにしている
SELECT title, pdf_sha256, pdf_media_type FROM lake.main.reports;
```

- `fdl_columns` で `asset` が true の列と `fdl_files` の `sha256` は、各行にファイルの sha256（小文字の 16 進）か null を持つ。型は `varchar`
- 各ファイルはルートの `assets/<sha256>` に置く（[レイアウト](/ja/spec/layout#パス)）。同じ中身は 1 回だけ置き、複数のバージョンで共有する
- ファイルの名前・大きさ・メディアタイプは、同じテーブルの列として持つ。列名は書き手が決める。読み手がファイルを取得する前に表示方法を判定できるよう、少なくともメディアタイプの列を持つのがよい
- ファイルを差し替えるときは、新しい中身を置き、その sha256 を行に入れる。古いバージョンは古い中身を指したまま残る
- 書き手は、ファイルのメディアタイプを `Content-Type` に付けてアップロードする。バージョンの行が持つアセットは、そのバージョンを書く前にすべてリポジトリに置く
- 読み手は、リポジトリを読んでいる場所の `<root>assets/<sha256>` でファイルを取得し、表示する前に sha256 を確認する。ファイルが無い行は、リンク切れとして表示する
- 提供元のサイトにあるファイルなど、リポジトリの外のファイルは、普通の列に URL で持つ。これはアセットではなく、FDL は管理しない

## ほかの形式のメタデータ

1 つのバージョンが DuckLake の形式ごとにメタデータを持ってよく、読み手は DuckDB を替えずに、開けるファイルを選べます。

```
versions/<ID>/
  metadata.db                作ったときのメタデータ（version.json が説明するもの）
  metadata/1.0.db            形式 1.0 で書いたもの
  metadata/1.1.db            形式 1.1 で書いたもの
```

- `metadata/<format>.db` はすべて、同じデータファイルを参照し、同じ行を返さなければならない
- 読み手は、開ける形式を新しい順に試し、どれも無ければ `metadata.path` のメタデータを開く
- 宣言した形式をあるバージョンで作れない書き手は、そのファイルを置かない。どのファイルも開けない読み手は、そのバージョンを読めない
- 古い形式で表せないものを使ったバージョンには、その形式のメタデータを置かない
- `fdl.json` の `until` を過ぎた形式は、新しいバージョンでは書かない。過去のバージョンはそのまま残してよい
- データファイルは、`fdl.json` で宣言した一番古い形式が読めるように書かなければならない
- `metadata.storage` は、宣言した読み手が開ける一番古い DuckDB の保存形式にするのがよい

DuckDB 2.0 で古い形式を書く方法は [DuckLake の形式](/ja/internals/ducklake-formats)にあります。

## 以前のバージョンのファイル

FDL が必要とする値を tag と `fdl_` のテーブルに置く前に書かれたバージョンは、メタデータの隣にファイルを 2 つ多く持ちます。`version.json` に `dataset` か `tree` があるバージョンがそれです。

```
versions/<バージョン ID>/
  dataset.json       書き手が書いた、バージョンの中身の説明
  tree.json          README など、バージョンのほかのファイルのパスの一覧（任意）
objects/<sha256>     tree.json が並べるファイル（共有、変更しない）
```

- `version.json` の `dataset` と `tree` は、`files` と同じく、これらのファイルの名前・大きさ・sha256 を持つ
- 読み手は `dataset.json`・`tree.json`・`objects/` を無視してよい。表示する読み手が表示するのは、書き手が当時書いたもので、いまの意味ではない
- このバージョンには tag も `fdl_` のテーブルも無い。読み手と書き手は、主キーとアセットを `dataset.json` から読む。`tables` の要素（`schema` と `name` で対応を取る）の `primary_key` が `fdl_tables.primary_key`、その `fields` のうち `"asset": true` の要素が `asset` が true の列になる。`dataset.json` の最上位の `catalogs` は `fdl_catalogs`。このバージョンを基点にしたマージと、どのバージョンからも到達しないアセットの削除は、これに頼る
- `tree.json` の `entries` には、`path`、ファイルの `sha256` と `bytes` を並べる。各ファイルは `objects/<sha256>` にあり、そのバージョンが到達する共有オブジェクト（[データファイル](/ja/spec/data-files#共有オブジェクト)）。fdl-cli 0.31 以前はこれらのファイルを `assets/<sha256>` に置いていた。そのバージョンを持つリポジトリは、それを `objects/<sha256>` に写す
- `dataset.json` と `tree.json` は、バージョンを保持期間から外すときにメタデータと一緒に消す。`tree.json` を持つ有効なバージョンが無くなれば、`objects/` の下のファイルはどのバージョンからも到達せず、猶予期間のあとに消える
- 書き手は、新しいバージョンにこれらのファイルを書かない。このバージョンからコミット・元に戻す・マージで新しいバージョンを作る書き手は、読んだ主キー・アセット・`catalogs` を `fdl_` のテーブルに書く。`diff` と[変更がないとき](/ja/spec/writing#変更がないとき)の判定では、親の `dataset.json` から読んだ値と比べるので、主キーとアセットが同じテーブルは変わったことにならない。親の `dataset.json` が示していた値だけを持つ `fdl_` の行は親のものとみなす
