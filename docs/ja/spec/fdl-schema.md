---
title: _fdl スキーマ
description: メタデータの中の _fdl スキーマのテーブル。データセット・テーブル・列の意味、FDL が読む主キー・アセット・カタログ、データセットのファイル、ほかの読み手のための属性。
---

DuckLake が記録する構造のほかに FDL がデータセットについて知る必要のある値と、データセット・テーブル・列が人にとって何を意味するかは、メタデータの中に、スキーマ `_fdl` の普通の DuckLake のテーブルとして持ちます。Git の `.gitattributes` に当たります。リポジトリの中のファイルで、ほかと一緒にバージョン管理され、ツールがほかのファイルをどう扱うかを示します。

- `_fdl` は、ほかのスキーマと同じく、書き手が使うツールで SQL で書きます。FDL は書きません
- 行が変わるとバージョンができ、テーブルはほかのテーブルと同じにマージします（[書く](/ja/spec/writing)）。各テーブルの主キーはこのページが決めます
- `_fdl` のテーブルと列はすべて任意です。`_fdl` の無いデータセットは、主キーもアセットも説明も持ちません
- 読み手は、このページが定めない `_fdl` のテーブルと列を無視します

`table` と `column` は DuckDB のキーワードなので、SQL では二重引用符で囲みます（`"table"`）。

## FDL が読むもの

| 場所 | 使い道 |
| --- | --- |
| `_fdl.tables.primary_key` | [主キーでの行のマージ](/ja/spec/writing#主キーでの行のマージ) |
| `_fdl.columns.asset` | [アセット](/ja/spec/versions#アセット): アップロードして残す値がどれか |
| `_fdl.tables.profile` | [profiles/](/ja/spec/versions#profiles): profile を作らないテーブルとビュー |
| `_fdl.catalogs` | ビューを読む: 読み手は先にそれぞれのリポジトリをその名前で ATTACH する |
| `_fdl.files` | データセットのファイル。アセットとして置く |

ほかの列は、ツールの間で意味をそろえるためにここで定めます。FDL 自身は読みません。

## _fdl.dataset

データセット全体の意味です。行は 1 つまでで、主キーはありません。マージで両方がこの行を変えたときは衝突になります。

| 列 | 型 | 意味 |
| --- | --- | --- |
| `title` | VARCHAR | タイトル |
| `description` | VARCHAR | 説明（Markdown） |
| `language` | VARCHAR | 言葉の言語。BCP 47 のタグ（`ja`、`en`） |
| `keywords` | VARCHAR[] | キーワード |
| `homepage` | VARCHAR | 提供元のデータのページ |
| `issued` | DATE | データセットを最初に公開した日 |
| `update_frequency` | VARCHAR | 更新の頻度（`daily`、`monthly`、`irregular` など） |
| `licenses` | STRUCT(id VARCHAR, spdx VARCHAR, url VARCHAR, title VARCHAR, commercial_use BOOLEAN, share_alike BOOLEAN, attribution_required BOOLEAN)[] | ライセンス。`spdx` は SPDX の識別子があるときに書く |
| `sources` | STRUCT(title VARCHAR, path VARCHAR)[] | データの出典。`path` は URL |
| `contributors` | STRUCT(title VARCHAR, path VARCHAR, roles VARCHAR[])[] | 作った人・提供する人 |
| `temporal_coverage` | STRUCT(start VARCHAR, "end" VARCHAR) | データが対象とする期間 |
| `spatial_coverage` | VARCHAR[] | 対象とする地域のコード（`JP`） |

名前は [DCAT](https://www.w3.org/TR/vocab-dcat-3/) に合わせています。

## _fdl.schemas

主キー: `schema`。

| 列 | 型 | 意味 |
| --- | --- | --- |
| `schema` | VARCHAR | スキーマ名 |
| `title` | VARCHAR | タイトル |
| `description` | VARCHAR | 説明（Markdown） |

## _fdl.tables

テーブルかビュー 1 つにつき 1 行です。主キー: `schema`・`name`。

| 列 | 型 | 意味 |
| --- | --- | --- |
| `schema` | VARCHAR | スキーマ名 |
| `name` | VARCHAR | テーブル名かビュー名 |
| `title` | VARCHAR | タイトル |
| `description` | VARCHAR | 説明（Markdown） |
| `keywords` | VARCHAR[] | キーワード |
| `licenses`・`sources`・`temporal_coverage`・`spatial_coverage` | `_fdl.dataset` と同じ | データセットと違うときに書く |
| `primary_key` | VARCHAR[] | 行を識別する列。テーブルだけ |
| `profile` | BOOLEAN | `false`: profile を作らない。NULL は `true` |
| `sql` | VARCHAR | クエリで作ったテーブルの、作った SQL。ビューの SQL はメタデータにあるので、ここには書かない |
| `depends_on` | VARCHAR[] | 読むテーブルとビュー。`schema.name`、ほかのデータセットのものは `<リポジトリの URL>#schema.name` |
| `materialized` | VARCHAR | 書き手のビルドツールがどう作ったか（`table`、`view`、`incremental` など） |
| `source_path` | VARCHAR | 書き手のプロジェクトでの定義のパス |

## _fdl.columns

説明する列 1 つにつき 1 行です。主キー: `schema`・`table`・`name`。

| 列 | 型 | 意味 |
| --- | --- | --- |
| `schema` | VARCHAR | スキーマ名 |
| `table` | VARCHAR | テーブル名かビュー名 |
| `name` | VARCHAR | 列名 |
| `title` | VARCHAR | タイトル |
| `description` | VARCHAR | 説明（Markdown） |
| `entity` | VARCHAR | ほかのデータセットと共有するキーの列のとき、値が識別するもの。コード体系の名前で書く（`corporate_number`、`lg_code`） |
| `asset` | BOOLEAN | `true`: 各値は[アセット](/ja/spec/versions#アセット)の sha256。テーブルだけ |

## _fdl.files

テーブルのデータではない、データセットのファイルです。ドキュメントと、ドキュメントが表示する画像などです。主キー: `path`。`sha256` はアセットの列で、各ファイルはアセットとして `assets/<sha256>` に置きます。

| 列 | 型 | 意味 |
| --- | --- | --- |
| `path` | VARCHAR | データセットの中のパス。フォルダの区切りは `/`。絶対パスにせず、`.` や `..` を含めない |
| `sha256` | VARCHAR | 中身の sha256（小文字の 16 進） |
| `bytes` | BIGINT | 大きさ |
| `media_type` | VARCHAR | メディアタイプ（`text/markdown`、`image/png`） |

ファイルの用途を FDL は決めません。ホストは、Git のホストがフォルダの `README.md` を表示するように、パスに従ってファイルを表示できます。

## _fdl.catalogs

データセットのビューが読むほかのリポジトリを、ATTACH する名前ごとに持ちます。主キー: `name`。

| 列 | 型 | 意味 |
| --- | --- | --- |
| `name` | VARCHAR | ビューの SQL がそのリポジトリを読むときの名前 |
| `url` | VARCHAR | リポジトリのルートの URL |

## _fdl.attributes

1 つの読み手のためのキーと値です。`.gitattributes` の行が、Git の知らない属性をそれを知るツールのために持つのと同じです。主キー: `schema`・`table`・`column`・`key`。

| 列 | 型 | 意味 |
| --- | --- | --- |
| `schema` | VARCHAR | スキーマ名。データセットには `''` |
| `table` | VARCHAR | テーブル名かビュー名。データセットとスキーマには `''` |
| `column` | VARCHAR | 列名。テーブル・ビュー・スキーマ・データセットには `''` |
| `key` | VARCHAR | 対象の読み手の名前と `-` で始める（`example-theme`） |
| `value` | JSON | 値 |

FDL はどれも読みません。1 行に 1 つのキーを持つので、同じテーブルの別のキーへの変更はマージできます。

## 確認

書き手は、次のとき作業メタデータを固めるのを断ります。

- `_fdl.schemas`・`_fdl.tables`・`_fdl.columns` の行が、無いスキーマ・テーブル・ビュー・列を指す。`_fdl.attributes` が `''` でない名前で指すときも同じ
- `primary_key` の列がテーブルに無いか、2 回現れる
- `asset` が true の列が `varchar` でない
- `_fdl.dataset` が 2 行以上ある。または `_fdl` のテーブルに主キーの列が無い

`_fdl` のテーブル自身については、このページが説明するだけです。書き手はそのための行を追加しません。profile も作りません。
