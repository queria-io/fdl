---
title: FDL のテーブル
description: メタデータの中の fdl_ のテーブル。データセット・テーブル・列の意味、FDL が読む主キー・アセット・カタログ、データセットのファイル、ほかの読み手のための属性。
---

DuckLake が記録する構造のほかに FDL がデータセットについて知る必要のある値と、データセット・テーブル・列が人にとって何を意味するかは、メタデータのファイルの中に、名前が `fdl_` で始まる普通のテーブルとして持ちます。DuckLake 自身の `ducklake_` のテーブルと同じデータベース（作業メタデータでは SQLite、バージョンでは `metadata.db`）に並びますが、DuckLake のテーブルではありません。DuckLake はこれを一覧に出さず、スナップショットも取らず、データファイルも書きません。Git の `.gitattributes` に当たります。リポジトリと一緒に置かれ、ほかと一緒にバージョン管理されますが、ツールはこれを中身ではなく設定として読みます。

- 書き手のツールは、作業メタデータに fdl-cli（`fdl meta`）か、メタデータのファイルへの SQL で書きます。変更は、ほかのテーブルの変更と同じく、次のコミットまで作業メタデータにあります
- チェックアウトはバージョンのメタデータから作業メタデータへこれを写し、固めるときは書き戻します。DuckLake のテーブルと同じ扱いです（[書く](/ja/spec/writing#作業メタデータ)）。メタデータを開かない読み手のために、バージョンには [fdl_tables.json](/ja/spec/versions#fdl_tablesjson) としても置きます
- 行が変わるとバージョンができます。ほかのテーブルと同じく、このページが決める主キーでマージします（[マージ](/ja/spec/writing#マージ)）
- テーブルと、テーブルの主キー以外の列はすべて任意です。これらの無いデータセットは、主キーもアセットも説明も持ちません
- 読み手は、このページが定めない `fdl_` のテーブルと列を無視します

列の型は `VARCHAR`・`BOOLEAN`・`BIGINT`・`JSON` のどれかで、SQLite と DuckDB の両方で持てます。`JSON` の値は JSON の値を持つテキストで、列ごとに決めたリスト・オブジェクト・文字列を持ちます。日付は ISO 8601 の `VARCHAR` のテキスト（`2025-04-01`）です。

`table` と `column` は SQL のキーワードなので、二重引用符で囲みます（`"table"`）。

## FDL が読むもの

| 場所 | 使い道 |
| --- | --- |
| `fdl_tables.primary_key` | [主キーでのセルのマージ](/ja/spec/writing#主キーでのセルのマージ) |
| `fdl_columns.asset` | [アセット](/ja/spec/versions#アセット): アップロードして残す値がどれか |
| `fdl_tables.profile` | [profiles/](/ja/spec/versions#profiles): profile を作らないテーブルとビュー |
| `fdl_catalogs` | ビューを読む: 読み手は先にそれぞれのリポジトリをその名前で ATTACH する |
| `fdl_files` | データセットのファイル。アセットとして置く |

ほかの列は、ツールの間で意味をそろえるためにここで定めます。FDL 自身は読みません。

## fdl_dataset

データセット全体の意味です。行は 1 つまでで、主キーはありません。マージでは 1 行として列ごとに比べます。

| 列 | 型 | 意味 |
| --- | --- | --- |
| `title` | VARCHAR | タイトル |
| `description` | VARCHAR | 説明（Markdown） |
| `language` | VARCHAR | 言葉の言語。BCP 47 のタグ（`ja`、`en`） |
| `homepage` | VARCHAR | 提供元のデータのページ |
| `issued` | VARCHAR | データセットを最初に公開した日（`2025-04-01`） |
| `update_frequency` | VARCHAR | 更新の頻度（`daily`、`monthly`、`irregular` など） |
| `licenses` | JSON | ライセンス。`id`・`spdx`・`url`・`title` を持つオブジェクトのリスト。`spdx` は SPDX の識別子があるときに書く |
| `sources` | JSON | データの出典。`title` と `path`（URL）を持つオブジェクトのリスト |
| `contributors` | JSON | 作った人・提供する人。`title`・`path`・`roles`（文字列のリスト）を持つオブジェクトのリスト |
| `temporal_coverage` | JSON | データが対象とする期間。`start` と `end` を持つオブジェクト |
| `spatial_coverage` | JSON | 対象とする地域のコードのリスト（`JP`） |

名前は [DCAT](https://www.w3.org/TR/vocab-dcat-3/) に合わせています。

## fdl_schemas

主キー: `schema`。

| 列 | 型 | 意味 |
| --- | --- | --- |
| `schema` | VARCHAR | スキーマ名 |
| `title` | VARCHAR | タイトル |
| `description` | VARCHAR | 説明（Markdown） |

## fdl_tables

テーブルかビュー 1 つにつき 1 行です。主キー: `schema`・`name`。

| 列 | 型 | 意味 |
| --- | --- | --- |
| `schema` | VARCHAR | スキーマ名 |
| `name` | VARCHAR | テーブル名かビュー名 |
| `title` | VARCHAR | タイトル |
| `description` | VARCHAR | 説明（Markdown） |
| `licenses`・`sources`・`temporal_coverage`・`spatial_coverage` | `fdl_dataset` と同じ | データセットと違うときに書く |
| `primary_key` | JSON | 行を識別する列の名前のリスト。テーブルだけ |
| `profile` | BOOLEAN | `false`: profile を作らない。NULL は `true` |
| `sql` | VARCHAR | クエリで作ったテーブルの、作った SQL。ビューの SQL はメタデータにあるので、ここには書かない |
| `depends_on` | JSON | 読むテーブルとビューのリスト。`schema.name`、ほかのデータセットのものは `<リポジトリの URL>#schema.name` |

## fdl_columns

説明する列 1 つにつき 1 行です。主キー: `schema`・`table`・`name`。

| 列 | 型 | 意味 |
| --- | --- | --- |
| `schema` | VARCHAR | スキーマ名 |
| `table` | VARCHAR | テーブル名かビュー名 |
| `name` | VARCHAR | 列名 |
| `title` | VARCHAR | タイトル |
| `description` | VARCHAR | 説明（Markdown） |
| `asset` | BOOLEAN | `true`: 各値は[アセット](/ja/spec/versions#アセット)の sha256。テーブルだけ |

## fdl_files

テーブルのデータではない、データセットのファイルです。ドキュメントと、ドキュメントが表示する画像などです。主キー: `path`。`sha256` はアセットの列で、各ファイルはアセットとして `assets/<sha256>` に置きます。

| 列 | 型 | 意味 |
| --- | --- | --- |
| `path` | VARCHAR | データセットの中のパス。フォルダの区切りは `/`。絶対パスにせず、`.` や `..` を含めない |
| `sha256` | VARCHAR | 中身の sha256（小文字の 16 進） |
| `bytes` | BIGINT | 大きさ |
| `media_type` | VARCHAR | メディアタイプ（`text/markdown`、`image/png`） |

ファイルの用途を FDL は決めません。ホストは、Git のホストがフォルダの `README.md` を表示するように、パスに従ってファイルを表示できます。

## fdl_catalogs

データセットのビューが読むほかのリポジトリを、ATTACH する名前ごとに持ちます。主キー: `name`。

| 列 | 型 | 意味 |
| --- | --- | --- |
| `name` | VARCHAR | ビューの SQL がそのリポジトリを読むときの名前 |
| `url` | VARCHAR | リポジトリのルートの URL |

## fdl_attributes

1 つの読み手のためのキーと値です。`.gitattributes` の行が、Git の知らない属性をそれを知るツールのために持つのと同じです。主キー: `schema`・`table`・`column`・`key`。

| 列 | 型 | 意味 |
| --- | --- | --- |
| `schema` | VARCHAR | スキーマ名。データセットには `''` |
| `table` | VARCHAR | テーブル名かビュー名。データセットとスキーマには `''` |
| `column` | VARCHAR | 列名。テーブル・ビュー・スキーマ・データセットには `''` |
| `key` | VARCHAR | 対象の読み手の名前と `-` で始める（`example-theme`） |
| `value` | JSON | 値 |

FDL はどれも読みません。1 行に 1 つのキーを持つので、キーごとに別の値になります。

## 確認

書き手は、次のとき作業メタデータを固めるのを断ります。

- `fdl_schemas`・`fdl_tables`・`fdl_columns` の行が、無いスキーマ・テーブル・ビュー・列を指す。`fdl_attributes` が `''` でない名前で指すときも同じ
- `primary_key` の列がテーブルに無いか、2 回現れる
- `asset` が true の列が `varchar` でない
- ビューの `fdl_tables` の行が `primary_key` を持つ。またはビューの `fdl_columns` の行で `asset` が true
- `fdl_dataset` が 2 行以上ある。`fdl_` のテーブルに主キーの列が無い。または `JSON` の値が JSON でない

行はスキーマ・テーブル・列を名前で指します。スキーマ・テーブル・列の名前を変えるか、別の名前で作り直す書き手のツールは、コミットする前に、それを指す `fdl_` の行に新しい名前を書きます。マージのバージョンも確認します（[マージ](/ja/spec/writing#fdl-のテーブル)）。

`fdl_` のテーブルは DuckLake のテーブルではないので、`fdl_` の行が指すことはありません。`fdl_files` の `sha256` は、`fdl_columns` に行を持たないアセットの列です。
