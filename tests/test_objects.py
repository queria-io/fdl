"""Assets: written by sha256 beside the data, named by rows, checked on push."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from fdl import catalog, layout, objects, repo
from fdl.cli import app
from fdl.stores import LocalStore

runner = CliRunner()


def run(*args) -> str:
    result = runner.invoke(app, [str(a) for a in args])
    assert result.exit_code == 0, result.output
    return result.output


def commit(working, *args) -> str:
    """Commit and push, as one step used to."""
    return run("commit", working, *args) + run("push", working)


def fails(*args) -> str:
    result = runner.invoke(app, [str(a) for a in args])
    assert result.exit_code == 1, result.output
    return result.output


def write(working: Path, store: LocalStore, sql: str) -> None:
    con = duckdb.connect()
    for statement in catalog.attach_sql(working, alias="lake", data_path=repo.data_url(store)):
        if statement:
            con.execute(statement)
    con.execute(sql)
    con.close()


def declare_assets(working: Path, table: str, column: str) -> None:
    run("declare", "assets", working, f"main.{table}", column)


@pytest.fixture
def dataset(tmp_path: Path, monkeypatch):
    (tmp_path / "about").mkdir()
    monkeypatch.chdir(tmp_path / "about")
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    run("init", remote)
    run("clone", remote, working)
    return LocalStore(remote), working


def test_a_table_of_documents_points_at_assets(tmp_path: Path, dataset) -> None:
    store, working = dataset
    folder = tmp_path / "docs"
    (folder / "img").mkdir(parents=True)
    readme = "# Towns\n\n![map](img/map.png)\n"
    (folder / "README.md").write_text(readme)
    (folder / "img" / "map.png").write_bytes(b"\x89PNG")
    (tmp_path / "docs.csv").write_text(run("assets", folder))
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    write(
        working,
        store,
        f"CREATE SCHEMA lake.docs; CREATE TABLE lake.docs.files AS "
        f"SELECT path, sha256, bytes::BIGINT AS bytes, media_type FROM read_csv('{tmp_path / 'docs.csv'}', all_varchar = true)",
    )
    run("declare", "assets", working, "docs.files", "sha256")

    commit(working, "-m", "first", "--author", "t", "--assets", folder)

    version = repo.head(store, "refs/heads/main")
    index = json.loads(store.get(f"versions/{version.id}/tables.json"))
    assert index["tables"]["docs.files"]["assets"] == ["sha256"]
    paths = {row["path"]: row["sha256"] for row in csv.DictReader(io.StringIO(run("assets", folder)))}
    # Stored as written: links are resolved through the paths of the table, not rewritten.
    assert store.get(objects.asset_key(paths["README.md"])).decode() == readme
    assert store.get(objects.asset_key(paths["img/map.png"])) == b"\x89PNG"


def test_assets_are_uploaded_from_the_folder_and_checked(tmp_path: Path, dataset) -> None:
    store, working = dataset
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"%PDF-a")
    (folder / "b.pdf").write_bytes(b"%PDF-b")
    listed = list(csv.DictReader(io.StringIO(run("assets", folder))))
    assert [row["path"] for row in listed] == ["a.pdf", "b.pdf"]
    (tmp_path / "files.csv").write_text(run("assets", folder))
    write(
        working,
        store,
        f"CREATE TABLE lake.main.reports AS SELECT path, sha256, media_type "
        f"FROM read_csv('{tmp_path / 'files.csv'}', all_varchar = true)",
    )
    declare_assets(working, "reports", "sha256")

    # Without the folder, the files are nowhere to be had.
    run("commit", working, "-m", "m", "--author", "t")
    assert "neither in the dataset" in fails("push", working)
    commit(working, "-m", "m", "--author", "t", "--assets", folder)

    for row in listed:
        assert store.get(objects.asset_key(row["sha256"])) == (folder / row["path"]).read_bytes()

    # The next version finds them in the dataset and needs no folder.
    write(working, store, "INSERT INTO lake.main.reports SELECT * FROM lake.main.reports LIMIT 1")
    commit(working, "-m", "again", "--author", "t")


def test_an_asset_column_holds_only_sha256(tmp_path: Path, dataset) -> None:
    store, working = dataset
    write(working, store, "CREATE TABLE lake.main.reports AS SELECT 'report.pdf' AS sha256")
    declare_assets(working, "reports", "sha256")
    assert "64 lower-case hex" in fails("commit", working, "-m", "m", "--author", "t")


def test_an_asset_column_is_varchar(tmp_path: Path, dataset) -> None:
    store, working = dataset
    write(working, store, "CREATE TABLE lake.main.reports AS SELECT 1 AS n")
    assert "has to be VARCHAR" in fails("declare", "assets", working, "main.reports", "n")


def test_assets_of_a_folder_that_is_missing_are_refused(tmp_path: Path) -> None:
    with pytest.raises(objects.ObjectError):
        objects.manifest(tmp_path / "missing")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("docs/photo.webp", "image/webp"),
        ("docs/photo.AVIF", "image/avif"),
        ("README.markdown", "text/markdown"),
        ("docs/report.pdf", "application/pdf"),
        ("docs/blob.unknownext", "application/octet-stream"),
    ],
)
def test_media_type_does_not_depend_on_the_machine(path: str, expected: str) -> None:
    assert objects.media_type(path) == expected


def test_asset_columns_that_cannot_be_read_are_an_object_error(tmp_path: Path) -> None:
    with pytest.raises(objects.ObjectError, match="could not read the asset columns"):
        objects.asset_values(tmp_path / "missing.ducklake", [("main", "t", "c")], data_path=str(tmp_path) + "/")


def test_withdrawal_deletes_the_assets_only_the_withdrawn_versions_reach(tmp_path: Path, dataset) -> None:
    store, working = dataset
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "kept.pdf").write_bytes(b"%PDF-kept")
    (folder / "leak.pdf").write_bytes(b"%PDF-leak")
    (folder / "README.md").write_text("# Reports\n")
    (tmp_path / "files.csv").write_text(run("assets", folder))
    write(
        working,
        store,
        f"CREATE TABLE lake.main.reports AS SELECT path, sha256 FROM read_csv('{tmp_path / 'files.csv'}', all_varchar = true) "
        "WHERE path <> 'README.md';"
        f"CREATE SCHEMA lake.docs; CREATE TABLE lake.docs.files AS SELECT path, sha256 FROM read_csv('{tmp_path / 'files.csv'}', all_varchar = true) "
        "WHERE path = 'README.md'",
    )
    declare_assets(working, "reports", "sha256")
    run("declare", "assets", working, "docs.files", "sha256")
    commit(working, "-m", "leak", "--author", "t", "--assets", folder)
    leaked = repo.head(store, "refs/heads/main")
    write(working, store, "DELETE FROM lake.main.reports WHERE path = 'leak.pdf'")
    commit(working, "-m", "clean", "--author", "t")
    rows = {row["path"]: row["sha256"] for row in csv.DictReader(io.StringIO(run("assets", folder)))}
    readme = objects.asset_key(rows["README.md"])

    plan = repo.plan_withdrawal(store, [leaked.id])
    assert objects.asset_key(rows["leak.pdf"]) in plan.deleted
    assert {objects.asset_key(rows["kept.pdf"]), readme} <= set(plan.still_read)

    run("withdraw", store.root, leaked.id, "--yes")
    assert store.head(objects.asset_key(rows["leak.pdf"])) is None
    assert store.head(objects.asset_key(rows["kept.pdf"])) is not None
    assert store.head(readme) is not None
    mark = json.loads(store.get(layout.version_expired(leaked.id)))
    assert mark["deleted"]["assets"] == [rows["leak.pdf"]]
