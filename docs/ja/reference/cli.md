---
title: コマンドライン
description: fdl のコマンド、オプション、断られるとき。
---

`<remote>` はリポジトリで、`s3://bucket/prefix/`、`https://host/path/`、またはフォルダです。S3 の認証情報とエンドポイントは boto3 の通常の場所から読みます（`AWS_*` の環境変数、R2 などの S3 互換ストレージには `AWS_ENDPOINT_URL`）。HTTP のリポジトリには、`FDL_TOKEN` か credential helper でトークンを渡します（[HTTP のトークン](#http-のトークン)）。

## HTTP のトークン

fdl はトークンを保存せず、ログインのコマンドもありません。HTTP のリポジトリは、手元のマシン（`localhost`）を除いて `https` でなければなりません。fdl はトークンをリポジトリと同じオリジンにだけ送り、別のオリジンへのリダイレクトの後には送りません。トークンは次の順に見つけたものを `Authorization: Bearer` で送ります。

1. `FDL_TOKEN` があればそれ（CI 向け）
2. 無ければ、fdl の設定でリポジトリの URL に設定した credential helper が答えたもの。helper が無ければトークンを送りません

設定は `~/.config/fdl/config.toml`（`$XDG_CONFIG_HOME/fdl/config.toml`、または `FDL_CONFIG` で指定したファイル）です。helper は URL の前方一致で設定し、パスが一番長く一致したものを使います。

```toml
[credential."https://data.example.org"]
helper = "example-cli fdl-credential"
```

fdl は `<helper> get` を実行し、標準入力に `protocol=`・`host=`・`path=` の行と空行を渡して、出力の `password=` の行を読みます。何も出さないか、エラーで終わったら、トークンは無いものとします。トークンの保存と helper の書き込みは、ホストの側の道具が行います。

helper から得たトークンにホストが 401 を返すと、fdl は helper をもう一度実行し、別のトークンが返ればそれでリクエストを送り直します。helper は期限の短いトークンを渡せます。`FDL_TOKEN` のトークンは取り直しません。

## fdl init

```bash
fdl init <remote> [--data-dir data/]
```

新しい `id` を入れた `fdl.json` と、`HEAD` を書きます。既にあるリポジトリに流すと、何も変えずに設定を表示します。

## fdl clone

```bash
fdl clone <remote> [<working.sqlite>] [--branch main] [--storage s3://bucket/prefix/]
```

ブランチが指すバージョンのメタデータから作業メタデータ（SQLite の DuckLake）を作ります。最初のバージョンの前は空のものを作ります。`<working.sqlite>` を省くと、今のフォルダにリポジトリの URL の最後の部分の名前で作ります（`https://example.com/towns/` なら `towns.sqlite`）。`--branch` には `main` のようなブランチ名か、`refs/heads/main` のような ref を渡します。隣の `<working.sqlite>.fdl.json` に、リポジトリ、ブランチ、元のバージョンを記録します。書くときの `ATTACH` 文を表示します。

HTTP のリポジトリでは、新しいデータファイルは `<working.sqlite>.data/` に書かれ、push でアップロードされます。リポジトリに既にあるファイルは、その場所から HTTP で読みます。push はアップロードを終えると、新しいバージョンから作業メタデータを作り直して手元の写しを削除するので、このフォルダにはまだ push していないファイルだけが残ります。

`--storage` を付けると、作業メタデータはリポジトリのストレージで直接データファイルを読み書きします。`--storage` にはそのストレージでのリポジトリの根を渡します。新しいファイルはその `data_dir` の下に直接書かれるので、push でアップロードするものはありません。鍵は `AWS_*` の環境変数（R2 などの S3 互換ストレージは `AWS_ENDPOINT_URL`）か、自分で作った DuckDB の secret から取ります。作業メタデータに書くビルドにも同じ鍵が要ります。

データセットの説明・主キー・アセットは作業メタデータの `_fdl` スキーマのテーブルにあり（[_fdl スキーマ](/ja/spec/fdl-schema)）、クローンはデータと一緒にそれらを持ちます。作業メタデータのほかにファイルは書きません。

バージョンの保持期間が過ぎているとき、メタデータの sha256 が `version.json` の記録と違うとき、メタデータがデータフォルダの外のデータファイルを指しているときは断られます。

## fdl config

```bash
fdl config author.name "Alice"
fdl config author.email alice@example.com
fdl config author.name
```

コミットするバージョンの作者を設定します。設定はリポジトリとは別に `~/.config/fdl/config.json`（`$XDG_CONFIG_HOME` があればその下）に保存します。値を省くと、設定した値を表示します。`fdl commit`・`fdl revert`・`fdl merge` は、作者を `--author`、環境変数 `FDL_AUTHOR_NAME` と `FDL_AUTHOR_EMAIL`、この設定の順に探し、どれにも名前が無ければ断ります。

## fdl commit

```bash
fdl commit <working.sqlite> -m "What changed" [--author alice] [--allow-empty] [--assets files/]
```

作業メタデータを、クローンした（または最後に push した）バージョンを親とする新しいバージョンに固め、`fdl push` で送るまで `<working.sqlite>.pending/` に置きます。リポジトリは何も変わりません。新しいバージョン ID と `seq` を表示します。push する前にもう一度コミットすると、まだ push していないバージョンを置き換えます。

コマンドは作業メタデータの `_fdl` スキーマ（[_fdl スキーマ](/ja/spec/fdl-schema)）を読み、まずテーブルとビューに照らして確認します。`_fdl` は、ほかのスキーマと同じく SQL か `fdl meta` で、コミットの前に書きます。

```sql
CREATE SCHEMA lake._fdl;
CREATE TABLE lake._fdl.tables ("schema" VARCHAR, "name" VARCHAR, primary_key VARCHAR[]);
INSERT INTO lake._fdl.tables VALUES ('main', 'towns', ['code']);
```

バージョンの `files.json` には、テーブルごとの `primary_key` とアセット列、ビューごとの SQL とクエリが返す列、`_fdl.catalogs` を書きます（[files.json](/ja/spec/versions#filesjson)）。ビューの列を求めるときは、`_fdl.catalogs` が挙げるリポジトリを、それぞれの `HEAD` が指すバージョンでその名前で ATTACH します。クエリを解決できないビューは列なしで載せます。メタデータのコメント（`COMMENT ON`）はそのまま残し、読みません。バージョンは `dataset.json` も `tree.json` も持たず、コマンドは tag を書きません。

元のバージョンが `_fdl` より前に書かれたもの（fdl-cli 0.33 以前）のときは、そのバージョンの `dataset.json` か `fdl.` tag が言っていた主キー・アセット・catalogs・`"profile": false` を、`_fdl` がまだ言っていなければ先に `_fdl` に書きます（[以前のバージョンの tag](/ja/spec/versions#以前のバージョンの-tag)）。このとき作業メタデータが変わります。それだけを持つ `_fdl` のテーブルは変更とみなさず、それだけではバージョンを作らず、追加したテーブルとしても表示しません。

`_fdl.columns` で `asset` の印を付けた列と `_fdl.files` の `sha256` は、ファイルの sha256 を持ちます（[アセット](/ja/spec/versions#アセット)）。コマンドはその列の値を読み、push がリポジトリに無いファイルを `--assets` のフォルダから `assets/<sha256>` にアップロードします。ファイルは中身で照合するので、フォルダの中のファイル名は問いません。データセットの文書（`README.md` とその画像など）は `_fdl.files` に載せ、そのファイルを `--assets` のフォルダに置きます（`fdl assets` を参照）。

親と同じ中身になるとき（[変更がないとき](/ja/spec/writing#変更がないとき)）は `Nothing to commit` と表示し、何も作らずに終了コード 0 で終わります。同じ行を新しいファイルに書き直しただけの作り直しも、変更なしとみなします。`--allow-empty` を付けると、それでもバージョンを作ります。行を読めず判定できないときは、警告を出してバージョンを作ります。

次のときは断られます。

- `_fdl` が[確認](/ja/spec/fdl-schema#確認)に通らないとき。行が無いスキーマ・テーブル・ビュー・列を指すとき、主キーがテーブルに無い列や同じ列を 2 回挙げるとき、アセット列が `varchar` でないとき、ビューに主キーかアセット列を付けたとき、`_fdl.dataset` が 2 行以上あるとき、`_fdl` のテーブルに主キーの列が無いとき
- 作業メタデータが行をメタデータの中に持っているとき
- アセットの列が sha256 でない値を持つとき
- データファイルが、データディレクトリの外を指す絶対パスで記録されているとき

## fdl meta

```bash
fdl meta list <working.sqlite> [<name>] [--json]
fdl meta get <working.sqlite> <name> <key>
fdl meta set <working.sqlite> <name> <key> <value>...
fdl meta unset <working.sqlite> <name> <key>
fdl meta dump <working.sqlite>
fdl meta load <working.sqlite> <rows.json|-> [--merge]
fdl meta objects <working.sqlite>
```

作業メタデータの `_fdl` の行を、`git config` が設定ファイルを読み書きするように読み書きします（[_fdl スキーマ](/ja/spec/fdl-schema)）。変更は、テーブルの変更と同じように、次の `fdl commit` で新しいバージョンに入ります。

`<name>` は、データセットなら `.`、スキーマなら `schema`、テーブルかビューなら `schema.table`、列なら `schema.table.column` です。`<key>` は、その名前に対応する `_fdl` のテーブル（`_fdl.dataset`・`_fdl.schemas`・`_fdl.tables`・`_fdl.columns`）の列で、`title`・`description`・`primary_key`・`asset` などです。`example-theme` のように `-` を含むキーは、`_fdl.attributes` の行のキーです。

```bash
fdl meta set lake.sqlite . title "Population by town"
fdl meta set lake.sqlite main.population primary_key code year
fdl meta set lake.sqlite main.population.code title "Area code"
fdl meta set lake.sqlite main.population example-theme '{"color": "blue"}'
fdl meta set lake.sqlite main.population licenses '[{"spdx": "CC-BY-4.0"}]'
```

`set` は、必要な `_fdl` スキーマ・テーブル・列・行を作ります。リスト（`primary_key`・`spatial_coverage`）には値を 1 つ以上渡します。STRUCT（`licenses`・`sources`・`temporal_coverage`）と属性には値を 1 つ JSON で渡します。JSON でない属性の値は文字列として扱います。結果が、それまで `_fdl` が通っていた[確認](/ja/spec/fdl-schema#確認)のどれかに通らないとき（名前のものが無い、ビューに主キーを付けた、など）は断ります。`unset` はキーを消し、名前のほかに何も残らない行を削除します。

`list` は 1 行に 1 つのキーを `name.key=value` の形で表示します。データセットのキーは名前を付けずに表示します。`get` は値を表示します。どちらも、1 行に収まる文字列はそのまま、それ以外は JSON で表示します。`get` と `unset` は、キーが設定されていないと終了コード 1 で終わります。`_fdl.files` と `_fdl.catalogs` はここでは読み書きしません。SQL で書いてください。

`dump` は `_fdl` のすべての行を 1 つの JSON オブジェクトで表示します。`_fdl` のテーブル（接頭辞なし）ごとに行の配列を持ち、各行は NULL でない列だけを持ちます。日付は ISO の文字列で、`_fdl.attributes` の `value` は JSON の値そのものです。

```json
{
  "dataset": [{ "title": "Population by town" }],
  "tables": [{ "schema": "main", "name": "population", "primary_key": ["code", "year"] }],
  "attributes": [{ "schema": "main", "table": "population", "column": "code", "key": "example-theme", "value": { "color": "blue" } }]
}
```

`load` は、書き手のファイルから `_fdl` を作るツールのために、この形のオブジェクトをファイルか標準入力（`-`）から受け取ります。既定では、`_fdl` はこの行だけを持つようになります。`_fdl` のテーブルは、仕様が定めるすべての列で作り直します。`--merge` を付けると、渡した行を主キーで照合して、行が持つ列で変更または追加し、ほかの行は残します。テーブル・列・値が `_fdl` に書けないものだったときや、結果が確認に通らないときは、何も書かずに終了コード 1 で終わります。問題ごとに場所を示します（例: `dataset: issued cannot be written as DATE (at the dataset.issued)`）。

`objects` は、作業メタデータのうち `_fdl` が説明できるテーブルとビューを JSON で表示します。`schema`・`name`・`kind`（`table` か `view`）と、テーブルなら `columns`（列と型）を持ちます。

`_fdl` より前に書かれたバージョンからクローンした作業メタデータでは、`fdl meta` は最初に、そのバージョンが述べていたことを `fdl commit` と同じように `_fdl` に書きます。`get`・`list`・`dump`・`objects` はそれを表示するだけで残しません。`set`・`unset`・`load` はそれを残すので、消したキーはコミットでも消えたままです。

## fdl push

```bash
fdl push <working.sqlite> [--no-profile]
```

`fdl commit` が作ったバージョンをアップロードし、条件付きでブランチをそこへ動かします（[書き込みの順番](/ja/spec/writing#書き込みの順番)）。ブランチ、バージョン ID、`seq` を表示します。fast-forward の `fdl merge` のあとは、何もアップロードせず、ブランチをマージしたバージョンへ動かします。コミットしたものが無いときは `Nothing to push` と表示します。

アップロードの前に、テーブルとビューごとの profile（[profiles/](/ja/spec/versions#profiles)）を書き、`version.json` より前にアップロードします。ブランチが動く時点で profile がリポジトリにあります。読むのは、データファイルか列が親から変わったテーブルだけで、ほかのテーブルは親の profile を引き継ぎます。ビューは、そのクエリか、クエリが読むテーブル・ビューが変わると、クエリを実行して測り直します。`_fdl.tables` の `profile` が `false` のテーブルとビュー、`_fdl` のテーブルには profile を書きません。読めなかったテーブルには profile を書かず、警告を出します。コミットのあとに作業メタデータが変わっていたときは profile を書かずに警告します。`--no-profile` を付けると profile を書きません。`fdl profile` であとから足せます。

作業メタデータがリポジトリに書いたデータファイルは push の前からありますが、push するまでどのバージョンも参照しません。push しなかったファイルは猶予期間のあとに削除されます。

次のときは断られます。

- クローンのあとにブランチが動き、バージョンがいまブランチの指すバージョンの子孫でないとき。クローンし直して作業をやり直す
- メタデータが参照するデータファイルがリポジトリに無いとき
- アセットの列が、リポジトリにも `--assets` にも無いファイルを指すとき

断られても、コミットしたバージョンはそのまま残るので、原因を直してからもう一度送れます。

## fdl assets

```bash
fdl assets <folder> > files.csv
```

フォルダより下のファイル（隠しファイルを除く）を、`path`・`sha256`・`bytes`・`media_type` の CSV で表示します。これは `_fdl.files` の列です。データセットの文書なら `_fdl.files` に、行がファイルを指す自分のテーブルならそのテーブルに DuckDB の `read_csv` などで読み込み（後者は `_fdl.columns` で `sha256` の列に `asset` の印を付けます）、`--assets <folder>` を付けてコミットします。ファイルの sha256 はここで 1 回だけ計算し、SQL でファイルを読む必要はありません。

```sql
CREATE TABLE lake._fdl.files AS
SELECT path, sha256, bytes::BIGINT AS bytes, media_type FROM read_csv('docs.csv', all_varchar = true);
```

## fdl log

```bash
fdl log <remote> [--ref HEAD]
```

ref が指すバージョンから最初のバージョンまで、最初の親をたどって並べます。`seq`、ID、時刻、作者、メッセージを表示します。保持期間を過ぎたバージョンには `(expired)` が付きます。

## fdl status

```bash
fdl status <working.sqlite> [--json]
```

作業メタデータが clone してから何を変えたかを、`fdl diff` と同じ区分で表示します。元のバージョンと同じ内容なら `Nothing to commit` と表示します。`_fdl` のテーブルもほかのテーブルと同じく比べます。コミットしてまだ push していないバージョンがあれば、先にそれを表示し、変更はそのバージョンから数えます。`--json` では `pending` にその ID を入れます。fast-forward の `fdl merge` のあとは、`fdl push` でブランチが動くことを表示します。

## fdl diff

```bash
fdl diff <remote> [<version>] [--against <version>] [--json]
```

バージョン（既定は `HEAD` が指すもの）が親から、または `--against` で指定したバージョンから何を変えたかを表示します。バージョンはバージョン ID か ref で指定します。1 行が 1 つの変更です。

```
+ table main.roads
- table main.old_towns
~ rows main.towns: 1,741 -> 1,802
+ column main.towns.note
- column main.towns.area  (breaking)
~ column main.towns.name -> town  (breaking)
~ column main.towns.id: int32 -> int64
~ data main.towns
```

親と比べるときは、バージョンの `diff` から取ります（[diff](/ja/spec/versions#diff)）。それ以外のときや `diff` のないバージョンでは、行数・データファイル・列を `files.json` から取り、列は名前で比べます。そのため列名の変更は見分けられません。`~ data` は、行が同じでもテーブルのデータファイルが変わったことを示します。`_fdl` の変更は、そのテーブルの変更として表示します。`--json` を付けると、同じ内容を `tables`（`added`・`dropped`）・`rows`・`columns`・`data` を持つオブジェクトで表示します。`_fdl` より前に書かれたバージョンでは `~ tags`（[tag](/ja/spec/versions#以前のバージョンの-tag) が変わった）と `~ dataset.json` の行も、fdl-cli 0.31 以前が書いたバージョンでは `~ description` の行も表示します。

## fdl show

```bash
fdl show <remote> [<version>] [--json]
```

バージョンの番号・ID・親・作者・時刻・メッセージと、テーブルとその行数、ビュー、親からの変更を表示します。

## fdl url

```bash
fdl url <remote> [--ref HEAD] [--attach] [--alias lake]
```

ref が指すバージョンのメタデータの URL を表示します。読み取り専用で `ATTACH` するのに使います。`--attach` を付けると、データパス付きの `ATTACH` 文をそのまま表示します（トークンのある HTTP のリポジトリでは、その前に `CREATE SECRET` も表示します）。保持期間を過ぎたか取り下げたバージョンは、メタデータがまだ配られていても断ります。

## fdl tag

```bash
fdl tag <remote>                       # タグを並べる
fdl tag <remote> <name> [<version>]    # バージョンにタグを付ける（既定は HEAD が指すバージョン）
fdl tag <remote> <name> --delete
```

`<version>` にはバージョン ID か、`refs/heads/main` のような ref を渡します。タグは一度作ると動かしません。別のバージョンを指す同じ名前のタグがあるときは断られます。保持期間を過ぎたバージョンにはタグを付けられません。タグを作るか消すと `refs.json` を書き直します。`fdl push` もブランチを動かしたあとに書き直します。

## fdl branch

```bash
fdl branch <remote>                       # ブランチを並べる
fdl branch <remote> <name> [<version>]    # バージョンからブランチを作る（既定は HEAD が指すバージョン）
fdl branch <remote> <name> --delete
```

`<version>` にはバージョン ID か、`refs/heads/main` のような ref を渡します。別のバージョンを指す同じ名前のブランチがあるときは断られます。同じバージョンなら何も変えません。保持期間を過ぎたバージョンからはブランチを作れません。`HEAD` が指すブランチは削除できません。ブランチは `fdl clone --branch <name>` でクローンします。ブランチを作るか削除すると `refs.json` を書き直します。

## fdl revert

```bash
fdl revert <version> <working.sqlite> [--author <name>] [-m <message>]
fdl push <working.sqlite>
```

作業メタデータを `<version>` が持っていたテーブルとビュー（`_fdl` も含みます）に書き戻し、`fdl commit` と同じく新しいバージョンをコミットします。`<version>` が `_fdl` より前に書かれたものなら、その `dataset.json` か tag が言っていたことを `_fdl` に書きます。作業メタデータのコミットしていない変更は失われます。そのあと `fdl push` でブランチを前に進めます。間のバージョンは履歴に残り、読み続けられます。`<version>` から変わっていないテーブルはデータファイルをそのまま使い、変わったテーブルは `<version>` の時点の行から書き直します。`<version>` にはバージョン ID か ref を渡し、作業メタデータをクローンしたバージョンから最初の親をたどった履歴にあって（マージで取り込んだだけのバージョンは含みません）保持期間内のバージョンを指定します。そのバージョンがすでに `<version>` と同じものを持つときは、何も作りません。

公開してはいけないデータを除くときは `fdl withdraw` を使います。revert は間のバージョンとそのデータファイルをそのまま残します。

## fdl merge

```bash
fdl merge <theirs> <working.sqlite> [--author <name>] [-m <message>]
fdl push <working.sqlite>
```

別のブランチを、作業メタデータをクローンしたブランチにマージします（[マージ](/ja/spec/writing#マージ)）。`<theirs>` には `refs/heads/feature` のような ref か、バージョン ID を渡します。基点は 2 つのバージョンの共通の祖先です。

- `<theirs>` が作業メタデータのバージョンの履歴にすでにあるときは `Already up to date` と表示し、何も作りません
- 作業メタデータのバージョンが `<theirs>` の履歴にあるときは、作業メタデータを `<theirs>` から作り直し、バージョンは作りません。そのあと `fdl push` でブランチをそこへ動かします（fast-forward）
- それ以外は、マージの結果を作業メタデータに書き、マージのバージョンをコミットします。`parents` は作業メタデータのバージョンと `<theirs>` です。`fdl push` はコミットと同じくそれをアップロードします。メッセージの既定は `Merge <theirs>` です

基点から `<theirs>` だけが変えた・追加した・削除したテーブルは、`<theirs>` の内容を新しいデータファイルに書いて取り込みます。両方が変えたテーブルは、両方が `_fdl.tables` で同じ `primary_key` を付け、同じアセット列を持つとき、その主キーで行をマージします。`<theirs>` だけが追加・変更・削除した行は、その変更を取り込みます。`_fdl` のテーブルも、[_fdl スキーマ](/ja/spec/fdl-schema)が決める主キーで同じようにマージするので、片方だけが変えた説明は取り込まれます。コマンドは、取り込んだテーブルと、`<theirs>` が追加・変更・削除した行数を表示します。

衝突があるときは、テーブルごとに、行の衝突は主キーの値で一覧を表示し、終了コード 1 で終わり、何も変えません。衝突になるのは次のときです。

- 両方が違う形に変えたテーブルに、両方が宣言する主キーが無いとき（ビューはその SQL で、そのようなテーブルと同じく比較します）
- 片方が削除したテーブルを、もう片方が変えたとき
- 両方が変えたテーブルの列か型が両者で違うとき、または両方が列を変えたとき
- 両方が違う形に変えた行があるとき、または主キーに `NULL` や重複があるとき
- マージした `_fdl` が[確認](/ja/spec/fdl-schema#確認)に通らないとき（片方がテーブルを削除し、もう片方がその列を説明したときなど）

衝突しなくなるようにどちらかのブランチを直してから、もう一度マージします。

作業メタデータにコミットしていない変更があるとき、コミットして push していないバージョンがあるとき、2 つのバージョンに、ほかの共通の祖先の祖先でない共通の祖先が複数あるとき、基点か `<theirs>` が保持期間を過ぎているときは断られます。

## fdl withdraw

```bash
fdl withdraw <remote> [<version>...] [--objects <file>] [--json] [--yes] [--by <name>] [-m <message>]
```

間違って公開した個人データなど、公開してはいけないデータを持つバージョンを取り下げます（[取り下げ](/ja/spec/data-files#取り下げ)）。先にそのデータを除いたバージョンをコミットし、該当するバージョンを指すブランチが無い状態にします。そのうえでこのコマンドが、該当するバージョンを指すタグ、そのバージョンが参照し残すバージョンが参照しないデータファイルとアセット、メタデータを消し、`"reason": "withdrawn"` と消したオブジェクトの名前（`deleted`）を持つ `expired.json` を置きます。`version.json` は残します。保持期間を過ぎたバージョンを含めると、そのメタデータはもう無いので、有効なバージョンのどれも参照しないデータファイルとアセットを、猶予期間を待たずにすべて消します。

バージョンが参照するアセットは、アセット列（`_fdl.files` の `sha256` も含みます）が持つ sha256 と、`_fdl` より前に書かれたバージョンでは `tree.json` に載っているものです。

`--yes` を付けないと、消すものを表示するだけです。残すバージョンが参照しているファイルは残すものとして表示します。それがそのデータを持つなら、そのバージョンも取り下げます。ブランチかリクエストが該当するバージョンを指しているあいだは断られます。

`--objects` は、別のリポジトリで行った取り下げを、その写し（ミラーなど）に適用します。ファイルはそのリポジトリの `expired.json` か、その `deleted` だけです。名前のあるオブジェクトを参照する有効なバージョンも、指定したバージョンと一緒に取り下げます。名前のあるオブジェクトは、どのバージョンも参照していなくても、残っていれば消します。

`--json` は計画を 1 つの JSON（`versions`・`tags`・`deleted`・`still_read`・`blocked`）で表示します。該当するバージョンを指すタグ以外の ref は、断る代わりに `blocked` に並べます。ホストがそれらを動かしてから `--yes` で実行するためです。

## fdl profile

```bash
fdl profile <remote> [--ref refs/heads/main]
```

ブランチかタグが指すバージョンのテーブルとビューのうち、[profile](/ja/spec/versions#profiles) が無いものを測り、profile をバージョンの横に置きます。`fdl push` はアップロードするバージョンの profile を作ります。このコマンドは profile 無しで push したバージョンに使います。fdl が profile を書く前に作ったバージョン、`--no-profile` で push したバージョン、そのときテーブルを読めなかったバージョンです。既にある profile は変えません。保持期間を過ぎたバージョンはメタデータが無いので断られます。

## fdl expire

```bash
fdl expire <remote> [--keep 1] [--since 2026-10-01T00:00:00Z] [--grace 7d] [--by <name>] [-m <message>]
```

バージョンの保持期間を終わらせます。`expired.json` を置き、メタデータを消します。残すのは、各ブランチの最初の親のつながりで新しい `--keep` 個、ref が指すバージョン、その main との共通祖先、`--since` 以降または `--grace` のうちに作ったバージョンです。それ以外（マージが取り込んだ最初の親以外の親など）は保持期間を終わらせます（[保持期間を終わらせるバージョンの選び方](/ja/spec/data-files#保持期間を終わらせるバージョンの選び方)）。main にバージョンが無いうちは何も終わらせません。データファイルは消しません。消すのは `fdl gc` です。`--by` は保持期間を終わらせた人を、`--message` はその理由を `expired.json` に記録します。`fdl withdraw` の同じオプションも同じです。

## fdl gc

```bash
fdl gc <remote> [--grace 7d] [--yes]
```

どの生きているバージョンからも参照されず、`--grace`（`7d`、`24h`、`30m`）より前に書かれたデータファイルとアセットを削除します（[共有オブジェクトを消してよいとき](/ja/spec/data-files#共有オブジェクトを消してよいとき)）。保持期間を過ぎたバージョンだけが参照していたファイルと、作業メタデータが書いて push しなかったファイルがこれに当たります。`--yes` を付けないときは、一覧を表示するだけです。

生きているバージョンそれぞれの `files.json` と、行がアセットを持つバージョンのメタデータを読みます。HTTP のリポジトリ（削除はホストが行う）と、`files.json` もメタデータも無い生きているバージョンがあるときは断られます。
