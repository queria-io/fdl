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

`<working.sqlite>.fdl.json` には、バージョンの[宣言](/ja/spec/versions#宣言)（主キー・アセット列・profile・dependencies）も保存します。`fdl declare` がそれを変え、次のコミットが書きます。ほかのファイルは書きません。

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

コマンドはまず、作業メタデータの隣に保存した宣言（`fdl declare` を参照）をテーブルとビューに照らして確認します。コミットは親の宣言から始めます。dbt の `CREATE OR REPLACE` のように同じ名前で作り直したテーブルは宣言を引き継ぎ、削除したテーブルは宣言を失い、宣言のあとに名前を変えた列は新しい名前で宣言されます。

バージョンの `tables.json`（[tables.json](/ja/spec/versions#tablesjson)）には、テーブルごとのデータファイルと列、`primary_key`・`assets`・`profile`、ビューごとの SQL とクエリが返す列、`dependencies` を書きます。ビューの列を求めるときは、dependencies をそれぞれの名前で、指定したバージョンか、無ければその `main` が指すバージョンで ATTACH します。クエリを解決できないビューは列なしで載せます。メタデータのコメント（`COMMENT ON`）はそのまま残し、読みません。

`assets` で宣言した列は、ファイルの sha256 を持ちます（[アセット](/ja/spec/versions#アセット)）。コマンドはその列の値を読み、push がリポジトリに無いファイルを `--assets` のフォルダから `assets/<sha256>` にアップロードします。ファイルは中身で照合するので、フォルダの中のファイル名は問いません。データセットの文書（`README.md` とその画像など）は、`sha256` 列をアセット列として宣言した自分のテーブルに載せ、そのファイルを `--assets` のフォルダに置きます（`fdl assets` を参照）。

宣言も含めて親と同じ中身になるとき（[変更がないとき](/ja/spec/writing#変更がないとき)）は `Nothing to commit` と表示し、何も作らずに終了コード 0 で終わります。同じ行を新しいファイルに書き直しただけの作り直しも、変更なしとみなします。`--allow-empty` を付けると、それでもバージョンを作ります。行を読めず判定できないときは、警告を出してバージョンを作ります。

次のときは断られます。

- 宣言がテーブルに無い列や同じ列を 2 回挙げるとき、アセット列が `varchar` でないとき、ビューに主キーかアセット列を付けたとき、dependencies の名前が英小文字・数字・`_` でないか `memory`・`system`・`temp` のとき、その `url` が `/` で終わる絶対 URL でないか `version` がバージョン ID でないとき
- 作業メタデータが行をメタデータの中に持っているとき
- アセットの列が sha256 でない値を持つとき
- データファイルが、データディレクトリの外を指す絶対パスで記録されているとき

## fdl declare

```bash
fdl declare list <working.sqlite> [--json]
fdl declare primary-key <working.sqlite> <schema.table> [<column>...]
fdl declare assets <working.sqlite> <schema.table> [<column>...]
fdl declare profile <working.sqlite> <schema.name> (true|false)
fdl declare dependency <working.sqlite> <name> [<url>] [--version <ID>]
```

次のコミットがテーブルとビューについて[宣言](/ja/spec/versions#宣言)する内容を変えます。宣言は `<working.sqlite>.fdl.json` に保存し、作業メタデータをクローンしたバージョンの宣言から始まります。コミットがそれを `tables.json` に書きます。

```bash
fdl declare primary-key lake.sqlite main.population code year
fdl declare assets lake.sqlite main.reports pdf_sha256
fdl declare profile lake.sqlite main.stg_population false
fdl declare dependency lake.sqlite census https://example.org/census/
```

- `primary-key` は、テーブルの行を識別する列を設定します。[セル単位のマージ](/ja/spec/writing#主キーでのセルのマージ)に使います。列を省くと主キーを外します
- `assets` は、ファイルの sha256 を持つテーブルの `varchar` 列を設定します。列を省くと外します
- `profile false` は、ビルドの中間テーブルなどに push が profile を書かないようにします。`profile true` でそれを外します
- `dependency` は、ビューの SQL が読む別のデータセットを、SQL が使う名前で設定します。`--version` を省くと、ビューはその `main` が指すバージョンを読みます。`<url>` を省くと外します
- `list` は 1 行に 1 つの宣言を表示します。`--json` を付けるとすべてを 1 つのオブジェクトで表示します

テーブルとビューに照らして通らない変更は断ります。無いテーブルや列、ビューへの主キーやアセット列、`varchar` でないアセット列、コミットが断る名前や `url` の dependencies です。

## fdl push

```bash
fdl push <working.sqlite> [--no-profile]
```

`fdl commit` が作ったバージョンをアップロードし、条件付きでブランチをそこへ動かします（[書き込みの順番](/ja/spec/writing#書き込みの順番)）。ブランチ、バージョン ID、`seq` を表示します。fast-forward の `fdl merge` のあとは、何もアップロードせず、ブランチをマージしたバージョンへ動かします。コミットしたものが無いときは `Nothing to push` と表示します。

アップロードの前に、テーブルとビューごとの profile（[profiles/](/ja/spec/versions#profiles)）を書き、`version.json` より前にアップロードします。ブランチが動く時点で profile がリポジトリにあります。読むのは、データファイルか列が親から変わったテーブルだけで、ほかのテーブルは親の profile を引き継ぎます。ビューは、そのクエリか、クエリが読むテーブル・ビューが変わると、クエリを実行して測り直します。`profile` を `false` と宣言したテーブルとビューには profile を書きません。読めなかったテーブルには profile を書かず、警告を出します。コミットのあとに作業メタデータが変わっていたときは profile を書かずに警告します。`--no-profile` を付けると profile を書きません。`fdl profile` であとから足せます。

作業メタデータがリポジトリに書いたデータファイルは push の前からありますが、push するまでどのバージョンも参照しません。push しなかったファイルは猶予期間のあとに削除されます。

次のときは断られます。

- クローンのあとにブランチが動き、バージョンがいまブランチの指すバージョンの子孫でないとき。クローンし直して作業をやり直す
- メタデータが参照するデータファイルがリポジトリに無いとき
- `tables.json` が `version.json` の記録と合わないとき、またはメタデータが参照するデータファイルと削除ファイルを並べていないとき
- アセットの列が、リポジトリにも `--assets` にも無いファイルを指すとき

断られても、コミットしたバージョンはそのまま残るので、原因を直してからもう一度送れます。

## fdl assets

```bash
fdl assets <folder> > files.csv
```

フォルダより下のファイル（隠しファイルを除く）を、`path`・`sha256`・`bytes`・`media_type` の CSV で表示します。DuckDB の `read_csv` などでテーブルに読み込み、その `sha256` 列をアセット列として宣言し、`--assets <folder>` を付けてコミットします。ファイルの sha256 はここで 1 回だけ計算し、SQL でファイルを読む必要はありません。

```sql
CREATE TABLE lake.docs.files AS
SELECT path, sha256, bytes::BIGINT AS bytes, media_type FROM read_csv('docs.csv', all_varchar = true);
```

```bash
fdl declare assets lake.sqlite docs.files sha256
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

作業メタデータが clone してから何を変えたかを、`fdl diff` と同じ区分で表示します。元のバージョンと同じ内容なら `Nothing to commit` と表示します。`~ declarations <name>` はテーブルかビューの宣言が元のバージョンと違うこと、`~ dependency <name>` は dependencies を追加・削除・変更したことを示します。コミットしてまだ push していないバージョンがあれば、先にそれを表示し、変更はそのバージョンから数えます。`--json` では `pending` にその ID を入れます。fast-forward の `fdl merge` のあとは、`fdl push` でブランチが動くことを表示します。

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

親と比べるときは、バージョンの `diff` から取ります（[diff](/ja/spec/versions#diff)）。それ以外のときは、行数・データファイル・列・宣言を `tables.json` から取り、列は名前で比べます。そのため列名の変更は見分けられません。`~ data` は、行が同じでもテーブルのデータファイルが変わったことを示します。`~ declarations` はテーブルかビューの[宣言](/ja/spec/versions#宣言)が変わったこと、`~ dependency` は dependencies を追加・削除したか、`url` か `version` を変えたことを示します。`--json` を付けると、同じ内容を `tables`（`added`・`dropped`）・`rows`・`columns`・`data`・`declarations`・`dependencies` を持つオブジェクトで表示します。

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

作業メタデータを `<version>` が持っていたテーブルとビューと宣言に書き戻し、`fdl commit` と同じく新しいバージョンをコミットします。作業メタデータのコミットしていない変更は失われます。そのあと `fdl push` でブランチを前に進めます。間のバージョンは履歴に残り、読み続けられます。`<version>` から変わっていないテーブルはデータファイルをそのまま使い、変わったテーブルは `<version>` の時点の行から書き直します。`<version>` にはバージョン ID か ref を渡し、作業メタデータをクローンしたバージョンから最初の親をたどった履歴にあって（マージで取り込んだだけのバージョンは含みません）保持期間内のバージョンを指定します。そのバージョンがすでに `<version>` と同じものを持つときは、何も作りません。

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

基点から `<theirs>` だけが変えた・追加した・削除したテーブルは、`<theirs>` の内容を新しいデータファイルに書いて取り込みます。両方が変えたテーブルは、基点と両方で同じ `primary_key` を持つとき、その主キーでセル単位にマージします（[主キーでのセルのマージ](/ja/spec/writing#主キーでのセルのマージ)）。片方だけが追加・削除した行と、片方だけが変えたセルは、その変更を取り込みます。片方だけが追加・改名・削除した列や型を変えた列も、その変更を取り込みます。列は、テーブルが DuckLake のテーブル ID を保っているあいだは ID で、作り直したテーブルでは名前で対応させます。片方が型を変えた列で、もう片方が変えたセルは、新しい型にキャストします。基点・ours・theirs のどれにも主キーが無いテーブルは、行の多重集合としてマージします（[主キーの無いテーブルの行のマージ](/ja/spec/writing#主キーの無いテーブルの行のマージ)）。行ごとに基点と両方での出現数を数え、片方だけが変えた数はその数を取ります。片方だけが変えた宣言と dependencies は、値として取り込みます。コマンドは、取り込んだテーブルと、追加・変更・削除した行数を表示します。

衝突があるときは、テーブルごとに、行の衝突は主キーの値で一覧を表示し、終了コード 1 で終わり、何も変えません。衝突になるのは次のときです。

- 両方が変えたテーブルの主キーが基点と両方で違うとき、または両方が違う形に変えたビュー
- 片方が削除したテーブルを、もう片方が変えたとき
- 両方が違う形に追加・改名・削除した列、または型を変えた列。片方が既定値の無い `NOT NULL` の列を追加し、もう片方が行を追加したとき
- 両方が違う値に変えたセル、もう片方が変えた型にキャストできないセル、片方が削除しもう片方が変えた行、両方が違う値で追加した行、片方が削除した列でもう片方が変えたセル、主キーに `NULL` や重複があるとき
- 主キーの無いテーブルで、両方が違う数に変えた行、または両方で列が違うとき
- 両方が違う形に変えた宣言か dependencies
- マージした宣言をマージしたテーブルが通さないとき（片方が削除した列を、もう片方が主キーにしたときなど）

衝突しなくなるようにどちらかのブランチを直してから、もう一度マージします。

作業メタデータにコミットしていない変更があるとき、コミットして push していないバージョンがあるとき、2 つのバージョンに、ほかの共通の祖先の祖先でない共通の祖先が複数あるとき、基点か `<theirs>` が保持期間を過ぎているときは断られます。

## fdl withdraw

```bash
fdl withdraw <remote> [<version>...] [--objects <file>] [--json] [--yes] [--by <name>] [-m <message>]
```

間違って公開した個人データなど、公開してはいけないデータを持つバージョンを取り下げます（[取り下げ](/ja/spec/data-files#取り下げ)）。先にそのデータを除いたバージョンをコミットし、該当するバージョンを指すブランチが無い状態にします。そのうえでこのコマンドが、該当するバージョンを指すタグ、そのバージョンが参照し残すバージョンが参照しないデータファイルとアセット、メタデータを消し、`"reason": "withdrawn"` と消したオブジェクトの名前（`deleted`）を持つ `expired.json` を置きます。`version.json` は残します。保持期間を過ぎたバージョンを含めると、そのメタデータはもう無いので、有効なバージョンのどれも参照しないデータファイルとアセットを、猶予期間を待たずにすべて消します。

バージョンが参照するアセットは、アセット列が持つ sha256 です。

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

生きているバージョンそれぞれの `tables.json` と、アセット列を持つバージョンのメタデータを読みます。HTTP のリポジトリ（削除はホストが行う）と、`tables.json` の無い生きているバージョンがあるときは断られます。
