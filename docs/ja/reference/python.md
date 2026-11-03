---
title: Python API
description: fdl パッケージの置き場、ref、端末を使わないリポジトリの操作。
---

```python
from fdl import repo, refs
from fdl.stores import open_store

store = open_store("s3://my-bucket/towns/")
for version in repo.log(store):
    print(version.seq, version.id, version.message)
```

| モジュール | 中身 |
| --- | --- |
| `fdl.repo` | 操作: `init`、`checkout`、`commit`、`push`、`log`、`expire`、`prune` |
| `fdl.refs` | ref を読み、条件付きで動かす |
| `fdl.version` | `Version`（`version.json` の記録）と新しい ID |
| `fdl.catalog` | DuckDB と SQLite の DuckLake カタログと、その間の写し |
| `fdl.layout` | リポジトリの各ファイルの場所 |
| `fdl.store`、`fdl.stores` | 置き場に求めることと、フォルダ・S3 の置き場 |

## 置き場

`open_store(url)` は、フォルダの置き場、S3 の置き場、[HTTP で push を受け付ける](/ja/spec/http)ホストの置き場のどれかを返します。次のメソッドを持つオブジェクトなら、代わりに使えます。

| メソッド | すること |
| --- | --- |
| `put(key, data, *, content_type, if_match, if_none_match)` | 書いて ETag を返す。条件が合わなければ `status` が 412 のエラーを出す |
| `put_file(key, path, …)` | 同じことをファイルから |
| `get(key)` | 読む。無ければ `status` が 404 のエラーを出す |
| `head(key)` | オブジェクトのヘッダー（`ETag` を含む）か `None` |
| `list(prefix)` | プレフィックスの下のキー |
| `delete(keys)` | 消す。既に無いキーはエラーにしない |
| `put_files(files, *, content_type)` | 任意。一度だけ書く `(key, path)` の組を書く。HTTP の置き場はホストにアップロード用の URL を求め（[batch](/ja/spec/http#ストレージへ直接アップロードする)）、ストレージへ直接送る |

## fdl.repo

```python
repo.init(store, *, data_dir="data/") -> Config
```

リポジトリを作ります。既にあればそのままにして、設定を返します。

```python
repo.checkout(store, working, *, ref="HEAD") -> Version | None
```

`ref` が指すバージョンから、`working` に作業カタログを作ります。そのバージョンを返し、最初のバージョンの前は `None` を返します。

```python
repo.commit(working, out_dir, *, parent, data_path, author, message, inputs=(), annotations=None, dataset=None) -> Version
```

作業カタログを `out_dir/<ID>/` に固めます。`dataset`（読み込んだ `dataset.json`）をカタログと照らし合わせ、カタログから書き足します。`data_path` は `repo.data_url(store)` です。アップロードはしません。

```python
repo.record(frozen, out_dir, *, parent, author, message, inputs=(), annotations=None, dataset=None) -> Version
```

固め終えたカタログからバージョンを作ります。自分でカタログを固める書き手向けです。

```python
repo.push(store, local, version, *, ref="refs/heads/main", move_ref=True) -> None
```

コミットしたバージョンをアップロードし、条件付きで `ref` を動かします。ref が動いていれば `NotFastForward`、データファイルが足りなければ `RepoError` を出します。`move_ref=False` ではアップロードだけして止まります。ref を自分で動かすホスト向けです。

```python
repo.log(store, name="HEAD") -> Iterator[Version]
repo.head(store, name="HEAD") -> Version | None
repo.is_ancestor(store, ancestor_id, descendant) -> bool
```

最初の親をたどる、ref が指すバージョンを読む、あるバージョンが別のバージョンより前にあるかを確かめる、の 3 つです。

```python
repo.readable(store, name="HEAD") -> Version
```

ref が指すバージョンを、読み手がカタログを開く前に行う確認を済ませてから返します。最初のバージョンの前、読めない形式の `version.json`、保持期間を過ぎたか取り下げたバージョンでは `RepoError` を出します。`fdl url` はこれを使います。

```python
repo.expire(store, version_id, *, reason="retention") -> None
repo.prune(store, newest, *, keep=1, since=None, reason="retention") -> list[str]
```

バージョン 1 つの保持期間を終わらせるか、`newest` より古く保持期間から外れるバージョンをまとめて終わらせます。ref が指すバージョンは残します。

```python
repo.tag(store, name, version_id) -> None
repo.delete_tag(store, name) -> None
repo.tags(store) -> dict[str, str]
```

バージョンに `refs/tags/<name>` を作る、消す、タグと指すバージョンを並べる、の 3 つです。タグは動かしません。同じ名前のタグが別のバージョンにあれば、`tag` は `RepoError` を出します。

```python
repo.update_refs_index(store) -> None
```

ref を読み直して `refs.json` を書き直します。`push`・`tag`・`delete_tag` は ref を動かしたあとにこれを呼びます。`refs.json` を自分で書くホストが 403 で断ったときは、済んだものとして扱います。

```python
repo.plan_withdrawal(store, version_ids) -> Withdrawal
repo.withdraw(store, version_ids, *, plan=None) -> Withdrawal
```

公開してはいけないデータを持つバージョンを取り下げます。`plan_withdrawal` は何も変えずに、消すタグ、消すデータファイル、残すバージョンが読むので残すファイルを返します。`withdraw` はタグ、データファイル、カタログの順に消します。ブランチかリクエストが該当するバージョンを指しているあいだは、どちらも `RepoError` を出します。

## fdl.refs

```python
refs.read(store, name) -> Ref | None
refs.write(store, name, version_id, *, expected) -> str
```

`read` は、動かすときに渡す ETag を付けて ref を返します。`write` は、`expected` が `None` なら ref を作り、そうでなければ動かします。条件が合わなければ `RefConflict` を出します。

```python
refs.listed(store) -> dict[str, str]
refs.write_index(store) -> dict
```

`listed` はブランチとタグをすべて読み直します。`write_index` はそれと `HEAD` から `refs.json` を書きます。
