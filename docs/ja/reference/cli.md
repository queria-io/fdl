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

## fdl checkout

```bash
fdl checkout <remote> <working.sqlite> [--ref refs/heads/main] [--storage s3://bucket/prefix/]
```

ブランチが指すバージョンのカタログから作業カタログ（SQLite の DuckLake）を作ります。最初のバージョンの前は空のものを作ります。隣の `<working.sqlite>.fdl.json` に、リポジトリ、ブランチ、元のバージョンを記録します。書くときの `ATTACH` 文を表示します。

HTTP のリポジトリでは、新しいデータファイルは `<working.sqlite>.data/` に書かれ、push でアップロードされます。リポジトリに既にあるファイルは、その場所から HTTP で読みます。push はアップロードを終えると、新しいバージョンから作業カタログを作り直して手元の写しを削除するので、このフォルダにはまだ push していないファイルだけが残ります。

`--storage` を付けると、作業カタログはリポジトリのストレージで直接データファイルを読み書きします。`--storage` にはそのストレージでのリポジトリの根を渡します。新しいファイルはその `data_dir` の下に直接書かれるので、push でアップロードするものはありません。鍵は `AWS_*` の環境変数（R2 などの S3 互換ストレージは `AWS_ENDPOINT_URL`）か、自分で作った DuckDB の secret から取ります。作業カタログに書くビルドにも同じ鍵が要ります。

バージョンの保持期間が過ぎているとき、カタログの sha256 が `version.json` の記録と違うとき、カタログがデータフォルダの外のデータファイルを指しているときは断られます。

## fdl commit

```bash
fdl commit <working.sqlite> -m "What changed" --author alice [--dataset dataset.json] [--allow-empty] [--assets files/]
```

作業カタログを、チェックアウトした（または最後に push した）バージョンを親とする新しいバージョンに固め、`fdl push` で送るまで `<working.sqlite>.pending/` に置きます。リポジトリは何も変わりません。新しいバージョン ID と `seq` を表示します。push する前にもう一度コミットすると、まだ push していないバージョンを置き換えます。

バージョンはそれぞれ `dataset.json`（[バージョン](/ja/spec/versions#datasetjson)を参照）で説明します。`--dataset` で指定したファイルか、なければいまのディレクトリの `dataset.json` か `dataset.yml` から読みます。そのフォルダより下にある `*.table.yml`（または `*.table.json`）のテーブルの説明も合わせて読みます。隠しフォルダの中は読みません。コマンドはそれをカタログと照らし合わせ、列の型、行数、カタログのコメントを書き足します。

コマンドは、説明の隣の `README.md` と、そこにある `docs/` より下のファイル（隠しファイルを除く）をバージョンの tree に入れます。そのフォルダからのパスで `tree.json` を書き、push がリポジトリに無いものを書いたままの中身で `assets/<sha256>` にアップロードします（[tree.json](/ja/spec/versions#treejson)）。

profile は書きません。`fdl push` の前に `fdl profile` を実行して作ります。

`asset: true` の印を付けた列は、ファイルの sha256 を持ちます（[アセット](/ja/spec/versions#アセット)）。コマンドはその列の値を読み、push がリポジトリに無いファイルを `--assets` のフォルダから `assets/<sha256>` にアップロードします。ファイルは中身で照合するので、フォルダの中のファイル名は問いません。

親と同じ中身になるとき（[変更がないとき](/ja/spec/writing#変更がないとき)）は `Nothing to commit` と表示し、何も作らずに終了コード 0 で終わります。同じ行を新しいファイルに書き直しただけの作り直しも、変更なしとみなします。`--allow-empty` を付けると、それでもバージョンを作ります。行を読めず判定できないときは、警告を出してバージョンを作ります。

次のときは断られます。

- `dataset.json` が無いとき、カタログに無い表や列を説明しているとき。形式が決めていないキーは、警告を出してそのまま残します
- 作業カタログが行をカタログの中に持っているとき
- アセットの列が `varchar` でないとき、sha256 でない値を持つとき
- データファイルが、データディレクトリの外を指す絶対パスで記録されているとき

## fdl push

```bash
fdl push <working.sqlite>
```

`fdl commit` が作ったバージョンをアップロードし、条件付きでブランチをそこへ動かします（[書き込みの順番](/ja/spec/writing#書き込みの順番)）。ブランチ、バージョン ID、`seq` を表示します。fast-forward の `fdl merge` のあとは、何もアップロードせず、ブランチをマージしたバージョンへ動かします。コミットしたものが無いときは `Nothing to push` と表示します。

作業カタログがリポジトリに書いたデータファイルは push の前からありますが、push するまでどのバージョンも参照しません。push しなかったファイルは猶予期間のあとに削除されます。

次のときは断られます。

- チェックアウトのあとにブランチが動き、バージョンがいまブランチの指すバージョンの子孫でないとき。チェックアウトし直して作業をやり直す
- カタログが参照するデータファイルがリポジトリに無いとき
- アセットの列が、リポジトリにも `--assets` にも無いファイルを指すとき

断られても、コミットしたバージョンはそのまま残るので、原因を直してからもう一度送れます。

## fdl assets

```bash
fdl assets <folder> > files.csv
```

フォルダより下のファイル（隠しファイルを除く）を、`path`・`sha256`・`bytes`・`media_type` の CSV で表示します。これを DuckDB の `read_csv` などでファイルを指すテーブルに読み込み、`sha256` の列に `asset: true` の印を付けて、`--assets <folder>` を付けてコミットします。ファイルの sha256 はここで 1 回だけ計算し、SQL でファイルを読む必要はありません。

## fdl log

```bash
fdl log <remote> [--ref HEAD]
```

ref が指すバージョンから最初のバージョンまで、最初の親をたどって並べます。`seq`、ID、時刻、作者、メッセージを表示します。保持期間を過ぎたバージョンには `(expired)` が付きます。

## fdl status

```bash
fdl status <working.sqlite> [--json]
```

作業カタログが checkout してから何を変えたかを、`fdl diff` と同じ区分で表示します。元のバージョンと同じ内容なら `Nothing to commit` と表示します。`dataset.json` の文言はコミットまで比べません。コミットしてまだ push していないバージョンがあれば、先にそれを表示し、変更はそのバージョンから数えます。`--json` では `pending` にその ID を入れます。fast-forward の `fdl merge` のあとは、`fdl push` でブランチが動くことを表示します。

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
~ description main.towns.population
```

親と比べるときは、バージョンの `diff` から取ります（[diff](/ja/spec/versions#diff)）。それ以外のときや `diff` のないバージョンでは、行数を `dataset.json` から、データファイルを `files.json` から取り、列は名前で比べます。そのため列名の変更は見分けられません。`~ data` は、行が同じでもテーブルのデータファイルが変わったことを示します。`--json` を付けると、同じ内容を `tables`（`added`・`dropped`）・`rows`・`columns`・`data`・`descriptions` を持つオブジェクトで表示します。

## fdl show

```bash
fdl show <remote> [<version>] [--json]
```

バージョンの番号・ID・親・作者・時刻・メッセージと、テーブルとその行数、親からの変更を表示します。

## fdl url

```bash
fdl url <remote> [--ref HEAD] [--attach] [--alias lake]
```

ref が指すバージョンのカタログの URL を表示します。読み取り専用で `ATTACH` するのに使います。`--attach` を付けると、データパス付きの `ATTACH` 文をそのまま表示します（トークンのある HTTP のリポジトリでは、その前に `CREATE SECRET` も表示します）。保持期間を過ぎたか取り下げたバージョンは、カタログがまだ配られていても断ります。

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

`<version>` にはバージョン ID か、`refs/heads/main` のような ref を渡します。別のバージョンを指す同じ名前のブランチがあるときは断られます。同じバージョンなら何も変えません。保持期間を過ぎたバージョンからはブランチを作れません。`HEAD` が指すブランチは削除できません。ブランチは `fdl checkout --ref refs/heads/<name>` でチェックアウトします。ブランチを作るか削除すると `refs.json` を書き直します。

## fdl revert

```bash
fdl revert <version> <working.sqlite> --author <name> [-m <message>]
fdl push <working.sqlite>
```

作業カタログを `<version>` が持っていたテーブルとビューに書き戻し、`<version>` の `dataset.json` と `tree.json` で、`fdl commit` と同じく新しいバージョンをコミットします。作業カタログのコミットしていない変更は失われます。そのあと `fdl push` でブランチを前に進めます。間のバージョンは履歴に残り、読み続けられます。`<version>` から変わっていないテーブルはデータファイルをそのまま使い、変わったテーブルは `<version>` の時点の行から書き直します。`<version>` にはバージョン ID か ref を渡し、作業カタログをチェックアウトしたバージョンから最初の親をたどった履歴にあって（マージで取り込んだだけのバージョンは含みません）保持期間内のバージョンを指定します。そのバージョンがすでに `<version>` と同じものを持つときは、何も作りません。

公開してはいけないデータを除くときは `fdl withdraw` を使います。revert は間のバージョンとそのデータファイルをそのまま残します。

## fdl merge

```bash
fdl merge <theirs> <working.sqlite> --author <name> [-m <message>]
fdl push <working.sqlite>
```

別のブランチを、作業カタログをチェックアウトしたブランチにマージします（[マージ](/ja/spec/writing#マージ)）。`<theirs>` には `refs/heads/feature` のような ref か、バージョン ID を渡します。基点は 2 つのバージョンの共通の祖先です。

- `<theirs>` が作業カタログのバージョンの履歴にすでにあるときは `Already up to date` と表示し、何も作りません
- 作業カタログのバージョンが `<theirs>` の履歴にあるときは、作業カタログを `<theirs>` でチェックアウトし直し、バージョンは作りません。そのあと `fdl push` でブランチをそこへ動かします（fast-forward）
- それ以外は、マージの結果を作業カタログに書き、マージのバージョンをコミットします。`parents` は作業カタログのバージョンと `<theirs>` です。`fdl push` はコミットと同じくそれをアップロードします。メッセージの既定は `Merge <theirs>` です

基点から `<theirs>` だけが変えた・追加した・削除したテーブルは、`<theirs>` の内容を新しいデータファイルに書いて取り込みます。両方が変えたテーブルは、両方が `dataset.json` で宣言した `primary_key` で行をマージします。`<theirs>` だけが追加・変更・削除した行は、その変更を取り込みます。`dataset.json` と `tree.json` はキーごとにマージします。行数・型・NULL の可否は、マージしたカタログから書き直します。コマンドは、取り込んだテーブルと、`<theirs>` が追加・変更・削除した行数を表示します。`fdl commit` は説明を手元のファイルから読むので、`<theirs>` が `dataset.json` か `tree.json` を変えたときは警告を出します。次のコミットの前に、その変更を手元のファイルに反映してください。

衝突があるときは、テーブルごとに、行の衝突は主キーの値で一覧を表示し、終了コード 1 で終わり、何も変えません。衝突になるのは次のときです。

- 両方が違う形に変えたテーブルに、両方が宣言する主キーが無いとき（ビューはその SQL で、そのようなテーブルと同じく比較します）
- 片方が削除したテーブルを、もう片方が変えたとき
- 両方が変えたテーブルの列か型が両者で違うとき、または両方が列を変えたとき
- 両方が違う形に変えた行があるとき、または主キーに `NULL` や重複があるとき
- `dataset.json` か `tree.json` の同じ値を、両方が違う形に変えたとき

衝突しなくなるようにどちらかのブランチを直してから、もう一度マージします。

作業カタログにコミットしていない変更があるとき、コミットして push していないバージョンがあるとき、2 つのバージョンに、ほかの共通の祖先の祖先でない共通の祖先が複数あるとき、基点か `<theirs>` が保持期間を過ぎているときは断られます。

## fdl withdraw

```bash
fdl withdraw <remote> <version>... [--yes]
```

間違って公開した個人データなど、公開してはいけないデータを持つバージョンを取り下げます（[取り下げ](/ja/spec/data-files#取り下げ)）。先にそのデータを除いたバージョンをコミットし、該当するバージョンを指すブランチが無い状態にします。そのうえでこのコマンドが、該当するバージョンを指すタグ、そのバージョンが参照し残すバージョンが参照しないデータファイルとアセット、カタログを消し、`"reason": "withdrawn"` の `expired.json` を置きます。`version.json` は残します。保持期間を過ぎたバージョンを含めると、そのカタログはもう無いので、有効なバージョンのどれも参照しないデータファイルとアセットを、猶予期間を待たずにすべて消します。

バージョンが参照するアセットは、`tree.json` に載っているものと、アセット列が持つ sha256 です。

`--yes` を付けないと、消すものを表示するだけです。残すバージョンが参照しているファイルは残すものとして表示します。それがそのデータを持つなら、そのバージョンも取り下げます。ブランチかリクエストが該当するバージョンを指しているあいだは断られます。

## fdl profile

```bash
fdl profile <working.sqlite>
fdl profile <remote> [--ref refs/heads/main]
```

テーブルごとの profile（[profiles/](/ja/spec/versions#profiles)）をデータから測って書きます。

作業カタログを渡すと、そこで `fdl commit` が作ったバージョンの profile を作り、`fdl push` がバージョンと一緒にアップロードします。ブランチが動く時点で profile がリポジトリにあるように、commit と push の間に実行します。読むのは、データファイルか列が親から変わったテーブルだけで、ほかのテーブルは親の profile を引き継ぎます。読めなかったテーブルには profile を書かず、警告を出します。コミットしたバージョンが無いとき、コミットのあとに作業カタログが変わったときは断られます。

リポジトリを渡すと、ブランチかタグが指すバージョンのテーブルのうち profile が無いものを測り、profile をバージョンの横に置きます。fdl が profile を書く前に作ったバージョンや、profile を作らずに push したバージョンに使います。既にある profile は変えません。保持期間を過ぎたバージョンはカタログが無いので断られます。

## fdl expire

```bash
fdl expire <remote> [--keep 1] [--since 2026-10-01T00:00:00Z] [--ref refs/heads/main]
```

ブランチの最初の親のつながりで、新しい `--keep` 個より古いバージョンの保持期間を終わらせます。`expired.json` を置き、カタログを消します。`--since` 以降に作ったバージョンと、ref が指すバージョンは残します。データファイルは消しません。消すのは `fdl gc` です。

## fdl gc

```bash
fdl gc <remote> [--grace 7d] [--yes]
```

どの生きているバージョンからも参照されず、`--grace`（`7d`、`24h`、`30m`）より前に書かれたデータファイルとアセットを削除します（[共有オブジェクトを消してよいとき](/ja/spec/data-files#共有オブジェクトを消してよいとき)）。保持期間を過ぎたバージョンだけが参照していたファイルと、作業カタログが書いて push しなかったファイルがこれに当たります。`--yes` を付けないときは、一覧を表示するだけです。

生きているバージョンそれぞれの `files.json` と、行がアセットを持つバージョンのカタログを読みます。HTTP のリポジトリ（削除はホストが行う）と、`files.json` もカタログも無い生きているバージョンがあるときは断られます。
