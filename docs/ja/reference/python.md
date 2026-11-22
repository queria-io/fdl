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
| `fdl.repo` | 操作: `init`、`clone`、`commit`、`push`、`log`、`expire`、`prune` |
| `fdl.declared` | バージョンの[宣言](/ja/spec/versions#宣言)（主キー・アセット列・profile・dependencies）、その確認とマージ |
| `fdl.diff` | 2 つのバージョンの違い（`diff.versions`）と作業メタデータの変更（`diff.working`） |
| `fdl.refs` | ref を読み、条件付きで動かす |
| `fdl.version` | `Version`（`version.json` の記録）と新しい ID |
| `fdl.catalog` | DuckDB と SQLite の DuckLake メタデータと、その間の写し |
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
repo.clone(store, working, *, ref="HEAD", local_data=None, version=None) -> Version | None
```

`ref` が指すバージョン、または `version` を渡したときはそのバージョンから、`working` に作業メタデータを作ります。そのバージョンを返し、最初のバージョンの前は `None` を返します。HTTP の置き場では、新しいデータファイルを書くフォルダ `local_data` が要ります。メタデータの sha256 が `version.json` の記録と違うときと、メタデータがデータフォルダの外のデータファイルを指しているときは断ります。

```python
repo.commit(working, out_dir, *, parent, data_path, author, message, annotations=None, allow_empty=True, setup=(), facts=None, inherited=None, merged=None) -> Version
```

作業メタデータを `out_dir/<ID>/` に固め、宣言 `facts`（`declared.Facts`）を持つ `tables.json` を書きます。その前に宣言をテーブル・ビューと照らし合わせて確認し、問題があれば `declared.DeclarationError` を投げます。`data_path` は `repo.data_url(store)` です。`inherited` は親の宣言（`repo.declarations(store, parent)`）で、変更の有無の判定と diff に使います。アップロードはしません。

```python
repo.record(frozen, out_dir, *, parent, author, message, annotations=None, described=None, inherited=None, merged=None) -> Version
```

固め終えたメタデータからバージョンを作ります。自分でメタデータを固める書き手向けです。

```python
repo.declarations(store, version) -> declared.Facts
repo.tables_of(store, version) -> set[str]
```

push したバージョンの宣言と、テーブルとビューを、その `tables.json` から返します。

```python
repo.push(store, local, version, *, ref="refs/heads/main", move_ref=True) -> None
```

コミットしたバージョンをアップロードし、条件付きで `ref` を動かします。ref が動いていれば `NotFastForward`、データファイルが足りないときや `tables.json` が `version.json` とメタデータに合わないときは `RepoError` を出します。`move_ref=False` ではアップロードだけして止まります。ref を自分で動かすホスト向けです。

```python
repo.log(store, name="HEAD") -> Iterator[Version]
repo.head(store, name="HEAD") -> Version | None
repo.is_ancestor(store, ancestor_id, descendant) -> bool
```

最初の親をたどる、ref が指すバージョンを読む、あるバージョンが別のバージョンより前にあるかをすべての親をたどって確認する、の 3 つです。

```python
repo.merge_bases(store, ours, theirs) -> list[Version]
repo.forward(store, ref, version) -> None
```

`merge_bases` は、2 つのバージョンの共通の祖先のうち、ほかの共通の祖先の祖先でないものを返します。マージにはちょうど 1 つ要ります。`forward` は、リポジトリにすでにあり、いまの位置の子孫であるバージョンへブランチを動かします。基点がブランチ自身のバージョンであるマージ向けです。

```python
merge.primary_keys(base, ours, theirs) -> dict[tuple[str, str], list[str] | None]
merge.tables(working, base, theirs, *, keys, repository_data, data_path=None, setup=(), write=True) -> Plan
```

`fdl merge` の部品です。`primary_keys` は、それぞれのバージョンが宣言することと持つテーブル（`(repo.declarations(store, version), repo.tables_of(store, version))`）から、テーブルごとの主キーを返します。主キーが 3 つで違うテーブルは `None` で、衝突になります。返さないテーブルには主キーが無く、行を多重集合としてマージします。`tables` は、固めたメタデータ `theirs` が `base` から変えた内容を、作業メタデータに 1 つのスナップショットで書きます。衝突があるときは何も書きません。`Plan` は衝突と取り込んだものを持ちます。マージのバージョンは、`repo.commit` と `repo.record` に `merged=<theirs>` を渡して作ります。

```python
repo.readable(store, name="HEAD") -> Version
```

ref が指すバージョンを、読み手がメタデータを開く前に行う確認を済ませてから返します。最初のバージョンの前、読めない形式の `version.json`、保持期間を過ぎたか取り下げたバージョンでは `RepoError` を出します。`fdl url` はこれを使います。

```python
repo.expire(store, version_id, *, reason="retention") -> None
repo.plan_retention(store, *, keep=1, since=None, grace=timedelta(days=7), now=None) -> list[str]
repo.prune(store, *, keep=1, since=None, grace=timedelta(days=7), reason="retention", by=None) -> list[str]
```

バージョン 1 つの保持期間を終わらせるか、`plan_retention` が選ぶバージョンをまとめて終わらせます（[保持期間を終わらせるバージョンの選び方](/ja/spec/data-files#保持期間を終わらせるバージョンの選び方)）。`plan_retention` は一覧を返すだけです。ref が指すバージョンは残します。

```python
repo.tag(store, name, version_id) -> None
repo.delete_tag(store, name) -> None
repo.tags(store) -> dict[str, str]
```

バージョンに `refs/tags/<name>` を作る、消す、タグと指すバージョンを並べる、の 3 つです。タグは動かしません。同じ名前のタグが別のバージョンにあれば、`tag` は `RepoError` を出します。

```python
repo.branch(store, name, version_id) -> None
repo.delete_branch(store, name) -> None
repo.branches(store) -> dict[str, str]
```

`refs/heads/<name>` に対して同じことをします。`delete_branch` は `HEAD` が指すブランチを残します。

```python
repo.update_refs_index(store) -> None
```

ref を読み直して `refs.json` を書き直します。`push`・`tag`・`delete_tag` は ref を動かしたあとにこれを呼びます。`refs.json` を自分で書くホストが 403 で断ったときは、済んだものとして扱います。

```python
repo.plan_withdrawal(store, version_ids, *, objects=None, refuse_refs=True) -> Withdrawal
repo.withdraw(store, version_ids, *, plan=None) -> Withdrawal
```

公開してはいけないデータを持つバージョンを取り下げます。`plan_withdrawal` は何も変えずに、消すタグ、消すデータファイル、残すバージョンが読むので残すファイルを返します。`withdraw` はタグ、データファイル、メタデータの順に消します。ブランチかリクエストが該当するバージョンを指しているあいだは、どちらも `RepoError` を出します。`refuse_refs=False` のときは、`plan_withdrawal` はそれらを `blocked` に並べ、`withdraw` が `RepoError` を出します。`objects`（`expired.json` の `deleted` と同じ `{"data": [...], "assets": [...]}`）は、写し元のリポジトリで行った取り下げを適用します。それらを参照する有効なバージョンも該当するバージョンになります。

## fdl.declared

```python
declared.Facts(primary_keys={}, assets={}, dependencies={}, unprofiled=frozenset())
declared.from_index(index) -> Facts
declared.check(facts, found) -> list[str]
declared.merged(base, ours, theirs) -> tuple[Facts, list[str]]
declared.changed(old, new, names) -> list[str]
declared.dependencies_changed(old, new) -> list[str]
```

`Facts` はバージョンの宣言を持ちます。`primary_keys` と `assets` は `schema.table` ごと、`dependencies` は名前ごと（`{"url": ..., "version": ...}`）、`unprofiled` は `profile` が `false` のテーブルとビューです。`of(name)` は 1 つのテーブルかビューの宣言を `tables.json` と同じ形で返し、`to_json` と `from_json` はファイルに保存するためのものです。`from_index` は `tables.json` から宣言を読みます。`check` は、作業メタデータのテーブルとビュー（`found_in(working, data_path=...)`）に照らして、[決まり](/ja/spec/versions#宣言)に通らないものを返します。`merged` は 3 方向でマージし、衝突も返します。`changed` と `dependencies_changed` は diff に並べる名前を返します。

## fdl.diff

```python
diff.versions(store, old, new) -> dict
diff.working(working, base) -> dict
diff.lines(result) -> list[str]
```

`versions` は 2 つのバージョンを、古い方が親なら新しい方の `diff` から、それ以外は `tables.json` から比べます（最初のバージョンの前は `old` を `None` にします）。`working` は作業メタデータを、clone したときのバージョン `base` と比べます。どちらも `tables`（`added`・`dropped`）・`rows`・`columns`・`data` を返し、`fdl diff --json` と同じ形です。`versions` は `declarations` と `dependencies` も返します。`lines` は `fdl diff` が表示する行を返します。

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
