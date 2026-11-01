"""A repository over HTTP: the store, and the whole round trip through it."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from fdl import catalog, layout, repo
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
    run("commit", working, "-m", "first", "--author", "tester")

    # A second working catalog reads the published file where it is, and
    # writes its new one locally.
    again = tmp_path / "again" / "lake.sqlite"
    run("checkout", url, again)
    write(again, "CREATE TABLE lake.main.roads AS SELECT count(*) AS towns FROM lake.main.towns")
    run("commit", again, "-m", "second", "--author", "tester")

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
