---
title: DuckDB を使わずに読む
description: files.json を使い、表の Parquet ファイルを JavaScript で読む方法。Workers・ブラウザ・Node で動きます。
---

Cloudflare Worker や Web ページのように DuckDB を動かせない読み手も、バージョンを読めます。`files.json` に表ごとのデータファイルが並んでいるので、Parquet ファイルを範囲指定で読めば済みます。JavaScript では [`@queria-io/fdl-js`](https://www.npmjs.com/package/@queria-io/fdl-js) がこれを行います。

```bash
npm install @queria-io/fdl-js
```

```ts
import { httpStore, open, openParquet, readWhere, tableFiles } from "@queria-io/fdl-js";

const store = httpStore("https://data.example.org/towns/");
const version = await open(store); // HEAD、ブランチ、タグ、バージョン ID
const [file] = tableFiles(version, "main.towns") ?? [];
const rows = await readWhere(await openParquet(store, file), "town_code", "131016");
```

`readWhere` は、min/max にその値が入りうる行グループだけを読みます。読むときの列で並べた表なら、小さな読み取り数回で済みます。

## 読めるもの

[読み方](/ja/spec/reading)の手順にカタログの手前まで従い、カタログの代わりに `files.json` を読みます。

- `files.json` の無いバージョンは読めません。fdl-cli 0.16 より前の書き手は作りません
- ファイルに `deletes` がある表と、`renamed_columns` が `true` の表は断ります（[files.json](/ja/spec/versions#filesjson) を参照）
- `expired.json` のあるバージョンは断ります

`CREATE OR REPLACE TABLE` のように、バージョンごとに表をまるごと作り直す書き方なら、削除も列の改名も起きません。

## 置き場

| 置き場 | 読む先 |
| --- | --- |
| `httpStore(root)` | URL（`Range` リクエスト） |
| `r2Store(bucket, prefix)` | Cloudflare Worker の R2 バインディング |
| `@queria-io/fdl-js/node` の `fileStore(dir)` | 手元のフォルダ |

ref は動くので、開いたバージョンを覚えておくのは数分までにしてください。データファイルは変わらないので、Parquet のフッターはずっとキャッシュしてかまいません。
