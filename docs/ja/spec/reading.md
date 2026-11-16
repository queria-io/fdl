---
title: 読む
description: 読み手が ref をたどり、バージョンを確かめ、メタデータを ATTACH する手順と、キャッシュしてよいもの。
---

## 手順

どの ref を読めばよいか分からない読み手は、`refs.json` で名前を探してかまいません（[ref](/ja/spec/refs#refsjson) を参照）。

1. `HEAD` か、指定された ref を読む。中身が `ref: ` で始まっていれば、その先の ref を読む
2. 見つけた ID の `versions/<ID>/version.json` を読む。`id` が合っているか、`fdl` のメジャーバージョンを知っているかを確かめる
3. `versions/<ID>/expired.json` があれば止まる。保持期間を過ぎたか、取り下げられている
4. `versions/<ID>/` の `metadata.path` のファイル（`metadata.db`）か、開ける `metadata/` のファイルを読み取り専用で開く

```sql
ATTACH 'ducklake:https://example.org/lake/versions/019a3c4e-…/metadata.db' AS lake (
  DATA_PATH 'https://example.org/lake/tables/', OVERRIDE_DATA_PATH true, READ_ONLY
);
```

## 署名

バージョンは `versions/<ID>/version.json.sig` を持つことがあります。`version.json` のバイト列への SSH 署名で、`ssh-keygen -Y sign -n fdl` で作ります。署名は記録を、記録は `metadata.sha256` でメタデータを保証します。

```bash
ssh-keygen -Y verify -f allowed_signers -I alice@example.org -n fdl \
  -s version.json.sig < version.json
```

- 署名は任意。署名を確かめない読み手は、署名付きのバージョンもほかと同じように読む
- 確かめる読み手は、信用する鍵で署名を確かめ、メタデータを取得して、その sha256 を `metadata.sha256` と照らし合わせる
- 鍵は、読んでいるリポジトリではなく元のホストから受け取る。`fdl.json` の `signers` は、どの鍵が来るはずかを示すだけ

## データパス

メタデータの `data_path` は、書き手が公開したデータフォルダの絶対 URL です。読み手は `DATA_PATH '<root><data_dir>'` と `OVERRIDE_DATA_PATH true` を渡さなければなりません。`<root>` は、読み手がリポジトリを読んでいる場所です。こうすれば、元でもミラーでも同じ文で読め、別のホストに写したリポジトリもメタデータを変えずに読めます。

## メタデータの確認

メタデータはリポジトリに書き込める人が書きます。DuckDB は、メタデータが指すファイルをすべて読み手の認証情報で読みます。リポジトリの外にも届く認証情報でメタデータを ATTACH する読み手は、その前に次のことを確認するべきです。

- メタデータの sha256 が `version.json` の `metadata.sha256` と一致する
- メタデータが指すデータファイルはすべてデータパスからの相対パスで記録されていて、空・`.`・`..` の要素を含まない

絶対パスや `data_dir` の外のファイルを指すメタデータを開くと、読み手はその指す先を取得してしまいます。

HTTP では、読み手はトークンをリポジトリと同じオリジンにだけ送り、読み手自身のマシン以外には `https` でしか送りません。別のオリジンへのリダイレクトにはトークンを付けません。

## キャッシュ

- バージョンのファイルは、[バージョン](/ja/spec/versions)に書いた例外を除いて変わらないので、メタデータはいつまでもキャッシュしてよい
- `HEAD`、`refs/`、`refs.json` はキャッシュしてはいけない
- `expired.json` はあとから増えることがある。増えたら、バージョンのフォルダのキャッシュを捨てるのがよい

HTTP では、`HEAD` と `refs/` を `Content-Type: text/plain; charset=utf-8`、`Cache-Control: no-store` で返します。

## 決まった URL のメタデータ

ホストは、FDL を知らない読み手のために、`HEAD` が指すバージョンのメタデータを `<root>/metadata.db` で出してもかまいません。その場合は、レスポンスヘッダー `FDL-Version` にバージョン ID を付けなければなりません。このメタデータは配信するだけで、置き場には置きません。置き場には `versions/` の外のメタデータはありません。以前 `<root>/ducklake.duckdb` で出していたホストは、そこでも出し続けてかまいません。
