---
title: HTTP での push
description: 書き手が置き場の鍵を持たずに push できるよう、ホストが応えるリクエスト。
---

ホストは HTTP で push を受け付けてかまいません。そうすれば、書き手は置き場の鍵を持たずに済みます。リポジトリのルートは `https://data.example.org/towns/` のような URL で、配置のキーはすべてその下のパスです。

## リクエスト

| リクエスト | すること | 返すもの |
| --- | --- | --- |
| `GET`、`HEAD` `<root>/<key>` | オブジェクトを読む | `ETag` 付きの 200、または 404 |
| `PUT` `<root>/<key>` | `If-None-Match: *` か `If-Match: <ETag>` を付けてオブジェクトを書く | 新しい `ETag` 付きの 200 か 201。条件が合わなければ 412 |
| `GET` `<root>/?list&prefix=<p>[&after=<key>]` | プレフィックスの下のキーの一覧 | `{"keys": [...], "next": <key か null>}`。`next` がある間は `after` を付けて続きを求める |
| `POST` `<root>/?batch` | アップロード用の URL を求める。任意（[下](#ストレージへ直接アップロードする)を参照） | `{"objects": [...]}` |
| `POST` `<root>/<key>?uploads` | 分割アップロードを始める。条件は `PUT` と同じ | `{"upload": "<id>"}` |
| `PUT` `<root>/<key>?upload=<id>&part=<n>` | `n` 番目（1 から）の部分を送る | `{"etag": "<部分のタグ>"}` |
| `POST` `<root>/<key>?upload=<id>` | `{"parts": [{"part": n, "etag": "…"}]}` を順に並べて終える | `PUT` と同じ |

- 書き込みには `Authorization: Bearer <token>` を付ける。使えるトークンが無ければ 401、ここに書けないトークンなら 403
- 最後以外の部分は同じ大きさにする。1 回のリクエストで送れる大きさ（既定で 64 MB）を超えるファイルは分割して送る
- どのキーを受け付けるかはホストが決める。`HEAD`、`fdl.json`、配置の外のものへの書き込みは断ってよく（ただし HTTP の `HEAD` リクエストには答える。書き手が ref の ETag を読むのに使う）、`versions/` とデータファイルは `If-None-Match: *` 付きのときだけ受け付けてよい
- 確かめたあとに自分で ref を動かすホストは、`refs.json` も自分で書き直す。書き手からの `refs.json` の書き込みは 403 で断ってよく、書き手はそれを済んだものとして扱う
- ホストが push を確かめるのは ref を動かすとき。バージョンがあること、fast-forward であること、カタログが `version.json` と合うこと、参照するデータファイルがすべてリポジトリにあることを確かめる。断るときは 409 で、理由を本文に書く
- `DELETE` は無い。保持期間と削除はホストの仕事

## ストレージへ直接アップロードする

ホストはアップロード用の URL を渡し、ファイルをホストを通さずストレージへ直接送らせてよい。Git LFS の batch API と同じ形にする。この経路を使うのは、一度だけ書くファイル（データファイル、添付ファイル、バージョンのファイル）だけ。ref は常にホストを通して書く。

書き手は、アップロードするファイルを `POST <root>/?batch` で送る。

```json
{ "operation": "upload", "objects": [{ "key": "data/main/towns/a.parquet", "size": 1048576 }] }
```

答えにはオブジェクトごとに 1 つずつ項目が入る。順番は問わない。

```json
{
  "objects": [
    {
      "key": "data/main/towns/a.parquet",
      "size": 1048576,
      "actions": {
        "upload": { "href": "https://storage.example.org/…", "header": { "If-None-Match": "*" }, "expires_in": 3600 }
      }
    },
    { "key": "data/main/towns/b.parquet", "size": 2048 },
    { "key": "data/main/towns/c.parquet", "size": 9, "error": { "code": 413, "message": "Too large for one request" } }
  ]
}
```

- `actions.upload`: 書き手はファイルを本文にして `PUT <href>` を送り、ファイルの `Content-Type` と `header` のヘッダーを付ける。リポジトリの `Authorization` は付けない。そこで 412 が返れば、オブジェクトは既にある。`expires_in` を過ぎた URL を使う前に、書き手はそのオブジェクトをもう一度尋ねる
- `actions` も `error` も無い: オブジェクトは既にある
- コード 413 の `error`: ホストはこのファイルに署名しない。書き手はホストを通して（`PUT` か分割で）アップロードする
- それ以外の `error`: アップロードは断られた。コードと理由が入る
- 書き手が一度に尋ねるのは 100 個まで
- batch を持たないホストは 404・405・501 を返し、書き手はホストを通してアップロードする
- アップロードしただけではバージョンにならない。ホストが push を確かめるのは、これまでどおり ref を動かすとき

## 書き方

DuckDB は HTTP の URL にデータファイルを書けないので、こうしたリポジトリの作業カタログは 2 つの場所を持ちます。

- リポジトリに既にあるファイルはその場に残す。チェックアウトで、その参照をリポジトリの `data_dir` の下の絶対 URL に変え、HTTP で読む
- 新しいファイルは書き手のマシンのフォルダ（作業カタログのデータパス）に書く

コミットで、リポジトリの `data_dir` の下を指す参照を相対に戻します。新しいファイルは初めから相対です。push では、リポジトリに無い新しいファイルを、カタログより先にアップロードします。
