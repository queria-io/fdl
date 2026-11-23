---
title: 読む
description: 読み手が ref をたどり、バージョンを確かめ、メタデータを ATTACH する手順と、キャッシュしてよいもの。
---

## 手順

どの ref を読めばよいか分からない読み手は、`refs.json` で名前を探してかまいません（[ref](/ja/spec/refs#refsjson) を参照）。

1. `HEAD` か、指定された ref を読む。中身が `ref: ` で始まっていれば、その先の ref を読む
2. 見つけた ID の `versions/<ID>/version.json` を読む。`id` が合っているか、`fdl` のメジャーバージョンを知っているかを確かめる
3. `versions/<ID>/expired.json` があれば止まる。保持期間を過ぎたか、取り下げられている
4. `versions/<ID>/metadata.db` を読み取り専用で開く

```sql
ATTACH 'ducklake:https://example.org/lake/versions/019a3c4e-…/metadata.db' AS lake (
  DATA_PATH 'https://example.org/lake/tables/', OVERRIDE_DATA_PATH true, READ_ONLY
);
```

## 前のバージョン

読み手は、前のバージョンも同じ手順で、そのバージョンの ID で読みます。ID はタグ、`parents`、バージョンの一覧から取得します。どのバージョンのメタデータも自分のスナップショットの時点のデータセットを持つので、前のバージョンに戻るには、別のメタデータの中でタイムトラベルするのではなく、そのバージョンのメタデータを開きます。

```sql
ATTACH 'ducklake:https://example.org/lake/versions/019a2f10-…/metadata.db' AS lake_v11 (
  DATA_PATH 'https://example.org/lake/tables/', OVERRIDE_DATA_PATH true, READ_ONLY
);
```

- 読み手は、後のバージョンのメタデータで `AT (VERSION => n)` や `ducklake_snapshots()` を使って前のバージョンを読んではいけない。メタデータは自分のスナップショットだけを持つ（[固めたメタデータ](/ja/spec/versions#固めたメタデータ)）
- 2 つのバージョンを比べるときは、両方のメタデータを別の名前で ATTACH する
- 保持期間を過ぎたバージョンや取り下げたバージョンには、開くメタデータが無い（手順 3）

## ほかのデータセットを読むビュー

ビューの SQL は、[tables.json](/ja/spec/versions#tablesjson) の `dependencies` が決める名前で、ほかのデータセットのテーブルを読むことがあります。そのビューを読む前に、読み手は `dependencies` のそれぞれを上の手順でその名前で ATTACH します。`version` があればそのバージョンを、無ければ `main` が指すバージョンを ATTACH します。

```sql
ATTACH 'ducklake:https://example.org/census/versions/019a2f10-…/metadata.db' AS census (
  DATA_PATH 'https://example.org/census/tables/', OVERRIDE_DATA_PATH true, READ_ONLY
);
```

そのビューを読まない読み手は、ATTACH しなくてかまいません。

## データパス

メタデータの `data_path` は、書き手が公開したデータフォルダの絶対 URL です。読み手は `DATA_PATH '<root><data_dir>'` と `OVERRIDE_DATA_PATH true` を渡さなければなりません。`<root>` は、読み手がデータセットを読んでいる場所です。こうすれば、元でもミラーでも同じ文で読め、別のホストに写したデータセットもメタデータを変えずに読めます。

## メタデータの確認

メタデータはデータセットに書き込める人が書きます。DuckDB は、メタデータが指すファイルをすべて読み手の認証情報で読みます。データセットの外にも届く認証情報でメタデータを ATTACH する読み手は、その前に次のことを確認するべきです。

- メタデータの sha256 が `version.json` の `metadata.sha256` と一致する
- メタデータが指すデータファイルはすべてデータパスからの相対パスで記録されていて、空・`.`・`..` の要素を含まない

絶対パスや `data_dir` の外のファイルを指すメタデータを開くと、読み手はその指す先を取得してしまいます。

HTTP では、読み手はトークンをデータセットと同じオリジンにだけ送り、読み手自身のマシン以外には `https` でしか送りません。別のオリジンへのリダイレクトにはトークンを付けません。

## キャッシュ

- バージョンのファイルは、[バージョン](/ja/spec/versions)に書いた例外を除いて変わらないので、メタデータはいつまでもキャッシュしてよい
- `HEAD`、`refs/`、`refs.json` はキャッシュしてはいけない
- `expired.json` はあとから増えることがある。増えたら、バージョンのフォルダのキャッシュを捨てるのがよい

HTTP では、`HEAD` と `refs/` を `Content-Type: text/plain; charset=utf-8`、`Cache-Control: no-store` で返します。

## 決まった URL のメタデータ

ホストは、FDL を知らない読み手のために、`HEAD` が指すバージョンのメタデータを `<root>/metadata.db` で出してもかまいません。その場合は、レスポンスヘッダー `FDL-Version` にバージョン ID を付けなければなりません。このメタデータは配信するだけで、置き場には置きません。置き場には `versions/` の外のメタデータはありません。以前 `<root>/ducklake.duckdb` で出していたホストは、そこでも出し続けてかまいません。
