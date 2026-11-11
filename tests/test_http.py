"""A repository over HTTP: the store, and the whole round trip through it."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from fdl import catalog, layout, repo, stores
from fdl.cli import app
from fdl.stores import HttpStore, LocalStore, StoreError

from http_repo import TOKEN, serve

runner = CliRunner()


@pytest.fixture(autouse=True)
def described(tmp_path: Path, monkeypatch) -> None:
    """Every commit reads `dataset.json` from where it runs."""
    (tmp_path / "dataset.json").write_text('{"name": "towns", "title": "Towns"}')
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def served(tmp_path: Path, monkeypatch):
    local = LocalStore(tmp_path / "served")
    repo.init(local)
    server = serve(local)
    monkeypatch.setenv("FDL_TOKEN", TOKEN)
    yield local, f"http://127.0.0.1:{server.server_address[1]}/", server
    server.shutdown()


def run(*args: object) -> str:
    result = runner.invoke(app, [str(a) for a in args])
    assert result.exit_code == 0, result.output
    return result.output


def commit(working, *args) -> str:
    """Commit and push, as one step used to."""
    return run("commit", working, *args) + run("push", working)


def write(working: Path, sql: str) -> None:
    state = json.loads(working.with_name(working.name + ".fdl.json").read_text())
    data = Path(state["data"]).resolve().as_posix() + "/"
    con = duckdb.connect()
    for statement in catalog.attach_sql(working, alias="lake", data_path=data):
        if statement:
            con.execute(statement)
    con.execute(sql)
    con.close()


def test_conditions_parts_and_listing(served) -> None:
    _, url, _ = served
    store = HttpStore(url, part_size=8)
    etag = store.put("a", b"1", if_none_match="*")
    with pytest.raises(StoreError) as refused:
        store.put("a", b"2", if_none_match="*")
    assert refused.value.status == 412
    store.put("a", b"2", if_match=etag)
    assert store.get("a") == b"2"
    assert store.head("nothing") is None

    big = Path(__file__).parent / "http_repo.py"
    store.put_file("x/big", big, if_none_match="*")
    assert store.get("x/big") == big.read_bytes()
    assert "x/big" in store.list("x/")


def test_writes_need_the_token(served, monkeypatch) -> None:
    _, url, _ = served
    monkeypatch.delenv("FDL_TOKEN")
    with pytest.raises(StoreError) as refused:
        HttpStore(url, token=None).put("a", b"1")
    assert refused.value.status == 401


def test_the_round_trip_over_http(served, tmp_path: Path) -> None:
    local, url, server = served
    working = tmp_path / "work" / "lake.sqlite"
    run("checkout", url, working)
    write(working, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "tester")

    # A second working catalog reads the published file where it is, and
    # writes its new one locally.
    again = tmp_path / "again" / "lake.sqlite"
    run("checkout", url, again)
    write(again, "CREATE TABLE lake.main.roads AS SELECT count(*) AS towns FROM lake.main.towns")
    commit(again, "-m", "second", "--author", "tester")

    first, second = reversed(list(repo.log(local)))
    described = json.loads(local.get(layout.version_dir(first.id) + "dataset.json"))
    assert described["title"] == "Towns"
    assert [t["name"] for t in described["tables"]] == ["towns"]
    frozen = tmp_path / "frozen.duckdb"
    frozen.write_bytes(local.get(layout.version_catalog(second.id)))
    assert catalog.absolute_paths(frozen) == []
    assert len(catalog.data_files(frozen)) == 2
    # Nothing was deleted or listed beyond what push needs.
    assert all(method != "DELETE" for method, _ in server.requests)

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(
        f"ATTACH 'ducklake:{frozen}' AS v (READ_ONLY, DATA_PATH '{repo.data_url(local)}', OVERRIDE_DATA_PATH true)"
    )
    assert con.execute("SELECT towns FROM v.main.roads").fetchone() == (1,)


def test_requests_name_fdl(monkeypatch) -> None:
    sent = []

    def urlopen(request):
        sent.append(request)
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(stores, "_open", urlopen)
    with pytest.raises(StoreError):
        HttpStore("https://example.invalid/repo/").get("fdl.json")
    assert sent[0].get_header("User-agent").startswith("fdl/")


def test_data_files_go_straight_to_storage_when_the_host_signs_urls(tmp_path: Path, monkeypatch) -> None:
    local = LocalStore(tmp_path / "signed")
    repo.init(local)
    server = serve(local, batch=True)
    monkeypatch.setenv("FDL_TOKEN", TOKEN)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    working = tmp_path / "work" / "lake.sqlite"
    run("checkout", url, working)
    write(working, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "tester")
    server.shutdown()

    data_puts = [key for method, key in server.requests if method == "PUT" and ".parquet" in key]
    assert data_puts and all(key.startswith("storage/") for key in data_puts)
    assert [v.message for v in repo.log(local)] == ["first"]


def test_a_file_the_host_will_not_sign_goes_through_the_host(tmp_path: Path) -> None:
    local = LocalStore(tmp_path / "signed")
    server = serve(local, batch=True, sign_up_to=3)
    store = HttpStore(f"http://127.0.0.1:{server.server_address[1]}/", token=TOKEN)
    small, large, there = (tmp_path / name for name in ("small", "large", "there"))
    small.write_bytes(b"ab")
    large.write_bytes(b"abcdef")
    there.write_bytes(b"xy")
    local.put("data/there.parquet", b"xy")
    store.put_files([("data/small.parquet", small), ("data/large.parquet", large), ("data/there.parquet", there)])
    server.shutdown()

    assert local.get("data/small.parquet") == b"ab" and local.get("data/large.parquet") == b"abcdef"
    puts = [key for method, key in server.requests if method == "PUT"]
    # Uploads run at once, so they may arrive in any order.
    assert sorted(puts) == ["data/large.parquet", "storage/data/small.parquet"]


def test_a_rebuild_of_the_same_rows_over_http_is_nothing_to_commit(served, tmp_path: Path) -> None:
    local, url, _ = served
    working = tmp_path / "work" / "lake.sqlite"
    run("checkout", url, working)
    write(working, "CREATE SCHEMA lake.s; CREATE TABLE lake.s.towns AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "tester")
    # The way dbt rebuilds a table: a new table beside it, then renames.
    again = tmp_path / "again" / "lake.sqlite"
    run("checkout", url, again)
    write(
        again,
        "CREATE TABLE lake.s.towns__dbt_tmp AS SELECT 1 AS id; "
        "ALTER TABLE lake.s.towns RENAME TO towns__dbt_backup; "
        "ALTER TABLE lake.s.towns__dbt_tmp RENAME TO towns; DROP TABLE lake.s.towns__dbt_backup",
    )
    working = again
    result = runner.invoke(app, ["commit", str(working), "-m", "again", "--author", "tester"])
    assert result.exit_code == 0, result.output
    assert "Nothing to commit" in result.output
    assert "could not tell" not in result.output
    assert len(list(repo.log(local))) == 1


def test_a_busy_storage_answer_is_tried_again(tmp_path: Path, monkeypatch) -> None:
    answers = [500, 503, None]
    sent = []

    def urlopen(request):
        sent.append(request.full_url)
        code = answers.pop(0)
        if code is not None:
            raise urllib.error.HTTPError(request.full_url, code, "busy", {}, io.BytesIO(b"InternalError"))
        return io.BytesIO()

    monkeypatch.setattr(stores, "_open", urlopen)
    monkeypatch.setattr(HttpStore, "RETRY_DELAY", 0)
    path = tmp_path / "a.parquet"
    path.write_bytes(b"ab")
    HttpStore("https://example.invalid/repo/")._send_one("data/a.parquet", path, {"href": "https://storage.invalid/a"}, None)
    assert sent == ["https://storage.invalid/a"] * 3


def test_a_refused_storage_answer_is_not_tried_again(tmp_path: Path, monkeypatch) -> None:
    sent = []

    def urlopen(request):
        sent.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 403, "denied", {}, io.BytesIO(b""))

    monkeypatch.setattr(stores, "_open", urlopen)
    monkeypatch.setattr(HttpStore, "RETRY_DELAY", 0)
    path = tmp_path / "a.parquet"
    path.write_bytes(b"ab")
    with pytest.raises(StoreError):
        HttpStore("https://example.invalid/repo/")._send_one("data/a.parquet", path, {"href": "https://storage.invalid/a"}, None)
    assert len(sent) == 1


def _redirecting(to: str):
    """A server that answers GET /fdl.json with a redirect to ``to`` (on itself
    when it starts with ``/``) and any other path with ``{}``. It records the
    Authorization each request carried."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen: list[str | None] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:
            seen.append(self.headers.get("Authorization"))
            if to and self.path == "/fdl.json":
                self.send_response(302)
                self.send_header("Location", to)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def test_the_token_does_not_follow_a_redirect_to_another_origin() -> None:
    other, other_seen = _redirecting("")
    # localhost and 127.0.0.1 are different origins.
    first, first_seen = _redirecting(f"http://localhost:{other.server_address[1]}/moved")
    try:
        store = HttpStore(f"http://127.0.0.1:{first.server_address[1]}/", token=TOKEN)
        assert store.get("fdl.json") == b"{}"
    finally:
        first.shutdown()
        other.shutdown()
    assert first_seen == [f"Bearer {TOKEN}"]
    assert other_seen == [None]


def test_the_token_follows_a_redirect_on_the_same_origin() -> None:
    server, seen = _redirecting("/moved")
    try:
        store = HttpStore(f"http://127.0.0.1:{server.server_address[1]}/", token=TOKEN)
        assert store.get("fdl.json") == b"{}"
    finally:
        server.shutdown()
    assert seen == [f"Bearer {TOKEN}"] * 2


def test_plain_http_is_only_for_this_machine() -> None:
    with pytest.raises(StoreError, match="https"):
        HttpStore("http://example.org/repo/", token=TOKEN)
    HttpStore("http://localhost:8000/repo/", token=TOKEN)
    HttpStore("http://[::1]:8000/repo/", token=TOKEN)


def test_a_commit_over_http_frees_the_local_data_files(served, tmp_path: Path) -> None:
    local, url, _ = served
    working = tmp_path / "work" / "lake.sqlite"
    run("checkout", url, working)
    write(working, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "tester")
    folder = working.with_name(working.name + ".data")
    assert folder.is_dir() and not any(p.is_file() for p in folder.rglob("*"))

    # The same working catalog goes on: the first file is read from the repository.
    write(working, "INSERT INTO lake.main.towns SELECT id + 1 FROM lake.main.towns")
    commit(working, "-m", "second", "--author", "tester")
    second = repo.head(local)
    assert second.seq == 2
    frozen = tmp_path / "frozen.duckdb"
    frozen.write_bytes(local.get(layout.version_catalog(second.id)))
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(
        f"ATTACH 'ducklake:{frozen}' AS v (READ_ONLY, DATA_PATH '{repo.data_url(local)}', OVERRIDE_DATA_PATH true)"
    )
    assert con.execute("SELECT id FROM v.main.towns ORDER BY id").fetchall() == [(1,), (2,)]


def test_a_checkout_with_storage_writes_data_files_there(served, tmp_path: Path) -> None:
    local, url, _ = served
    working = tmp_path / "work" / "lake.sqlite"
    out = run("checkout", url, working, "--storage", local.root)
    data = f"{Path(local.root).resolve().as_posix()}/data/"
    assert f"DATA_PATH '{data}'" in out
    assert not working.with_name(working.name + ".data").exists()

    def build(sql: str) -> None:
        con = duckdb.connect()
        for statement in catalog.attach_sql(working, alias="lake", data_path=data):
            if statement:
                con.execute(statement)
        con.execute(sql)
        con.close()

    build("CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    # Written into the repository before the commit: nothing to upload.
    assert list((Path(local.root) / "data").rglob("*.parquet"))
    commit(working, "-m", "first", "--author", "tester")
    build("INSERT INTO lake.main.towns VALUES (2)")
    commit(working, "-m", "second", "--author", "tester")

    second = repo.head(local)
    frozen = tmp_path / "frozen.duckdb"
    frozen.write_bytes(local.get(layout.version_catalog(second.id)))
    assert not catalog.absolute_paths(frozen)
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{frozen}' AS v (READ_ONLY, DATA_PATH '{repo.data_url(local)}', OVERRIDE_DATA_PATH true)")
    assert con.execute("SELECT id FROM v.main.towns ORDER BY id").fetchall() == [(1,), (2,)]


def test_the_storage_secret_comes_from_the_aws_variables(monkeypatch) -> None:
    from fdl.cli import _storage_setup

    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    assert _storage_setup("s3://b/p/") == []
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "id")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "se'cret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "tok")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://acct.r2.cloudflarestorage.com")
    [statement] = _storage_setup("s3://b/p/")
    assert "SECRET 'se''cret'" in statement and "SESSION_TOKEN 'tok'" in statement
    assert "ENDPOINT 'acct.r2.cloudflarestorage.com'" in statement and "SCOPE 's3://b/p/'" in statement
    duckdb.connect().execute("INSTALL httpfs; LOAD httpfs;").execute(statement)


def test_a_refused_token_is_asked_for_again(tmp_path: Path, monkeypatch) -> None:
    answers = iter(["run-out", TOKEN])
    asked: list[str] = []

    def helper(url: str) -> str:
        asked.append(url)
        return next(answers)

    monkeypatch.setattr(stores, "credentials_for", helper)
    server = serve(LocalStore(tmp_path))
    try:
        store = HttpStore(f"http://127.0.0.1:{server.server_address[1]}/")
        store.put("a.txt", b"a")
        assert len(asked) == 2 and store.token == TOKEN
        # A token it was handed is not asked for again.
        handed = HttpStore(f"http://127.0.0.1:{server.server_address[1]}/", token="run-out")
        with pytest.raises(StoreError) as refused:
            handed.put("b.txt", b"b")
        assert refused.value.status == 401 and len(asked) == 2
    finally:
        server.shutdown()
    assert (tmp_path / "a.txt").read_bytes() == b"a"
    assert not (tmp_path / "b.txt").exists()


def test_a_merge_over_http(served, tmp_path: Path) -> None:
    local, url, _ = served
    main, feature = tmp_path / "main" / "lake.sqlite", tmp_path / "feature" / "lake.sqlite"
    run("checkout", url, main)
    write(main, "CREATE TABLE lake.main.towns AS SELECT * FROM range(3) t(id)")
    commit(main, "-m", "first", "--author", "tester")
    run("branch", url, "feature")
    run("checkout", url, feature, "--ref", "refs/heads/feature")
    write(feature, "INSERT INTO lake.main.towns VALUES (3)")
    commit(feature, "-m", "feature", "--author", "tester")
    write(main, "CREATE TABLE lake.main.roads AS SELECT 1 AS id")
    commit(main, "-m", "main", "--author", "tester")

    assert "from theirs  main.towns" in run("merge", "refs/heads/feature", main, "--author", "tester")
    run("push", main)

    merged = repo.head(local, "refs/heads/main")
    assert len(merged.parents) == 2
    frozen = tmp_path / "frozen.duckdb"
    frozen.write_bytes(local.get(layout.version_catalog(merged.id)))
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(
        f"ATTACH 'ducklake:{frozen}' AS v (READ_ONLY, DATA_PATH '{repo.data_url(local)}', OVERRIDE_DATA_PATH true)"
    )
    assert con.execute("SELECT count(*) FROM v.main.towns").fetchone() == (4,)
    assert con.execute("SELECT count(*) FROM v.main.roads").fetchone() == (1,)
