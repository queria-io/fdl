---
title: 置き場
description: フォルダ、S3、R2、ほかの S3 互換ストレージをリポジトリの置き場にする方法と、fdl コマンドが読む認証情報。
---

リポジトリの URL で置き場が決まります。`s3://bucket/prefix/` は S3 か、S3 の API を話すストレージです。`https://host/path/` は [HTTP で push を受け付ける](/ja/spec/http)ホストで、`FDL_TOKEN` か credential helper のトークンを使います（[HTTP のトークン](/ja/reference/cli#http-のトークン)）。それ以外はディスク上のフォルダです。

## フォルダ

```bash
fdl init ./towns/
```

FDL を試すにはフォルダが一番早く、リポジトリを、それを作るコードの隣に置くこともできます。フォルダの置き場は ref を置き換える前に中身を確かめますが、ファイルをロックしません。2 人の書き手が同時に push すると、両方が確認を通ることがあります。フォルダは書き手が 1 人のときに使い、複数の人やジョブが push するときはオブジェクトストレージを使ってください。

## S3

```bash
pip install 'fdl-cli[s3]'
fdl init s3://my-bucket/towns/
```

認証情報は boto3 の通常の場所から読みます。`AWS_*` の環境変数、`~/.aws/credentials`、インスタンスロールです。書き手には、プレフィックスに対する `GetObject`、`PutObject`、`ListBucket` が要ります。`DeleteObject` は保守（`fdl expire`）だけが使います。

S3 は `PutObject` で `If-None-Match: *` と `If-Match` を受け付けます。ref を動かすにはこれが要ります。

## R2 とほかの S3 互換ストレージ

boto3 の向き先をそのストレージのエンドポイントにします。

```bash
export AWS_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
export AWS_ACCESS_KEY_ID=…
export AWS_SECRET_ACCESS_KEY=…
fdl init s3://my-bucket/towns/
```

S3 と R2 以外に書く前に、ETag が変わったときに `If-Match` 付きの `PutObject` を断るか確かめてください。このヘッダーを無視するストレージは、普段は動いているように見えますが、2 人が同時に push するとバージョンを黙って失います。

## DuckDB の認証情報

`fdl` と DuckDB は、認証情報を別々に読みます。書き手の DuckDB のセッションも置き場に Parquet を書くので、シークレットが要ります。

```sql
CREATE SECRET (TYPE s3, PROVIDER credential_chain);
```

R2 では `ENDPOINT '<account>.r2.cloudflarestorage.com'` と `URL_STYLE 'path'` を足すか、`TYPE r2` と `ACCOUNT_ID` を使います。

## ほかの置き場

Python パッケージは、`put`、`put_file`、`get`、`head`、`list`、`delete` を [Python API](/ja/reference/python#置き場) に書いた条件どおりに持つオブジェクトなら、何でも扱えます。組み込みの置き場の代わりに、`fdl.repo` の関数に渡してください。
