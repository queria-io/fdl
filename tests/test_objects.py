"""Assets: written by sha256 beside the data, named by rows or by the tree, checked on push."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from fdl import catalog, objects, repo
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


@pytest.fixture
def repository(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    remote, working = tmp_path / "remote", tmp_path / "lake.sqlite"
    run("init", remote)
    run("checkout", remote, working)
    return LocalStore(remote), working


def test_the_readme_and_docs_go_in_the_tree(tmp_path: Path, repository) -> None:
    store, working = repository
    (tmp_path / "docs" / "img").mkdir(parents=True)
    readme = "# Towns\n\n![map](docs/img/map.png)\n"
    (tmp_path / "README.md").write_text(readme)
    (tmp_path / "docs" / "img" / "map.png").write_bytes(b"\x89PNG")
    (tmp_path / "docs" / ".hidden").write_text("x")
    (tmp_path / "dataset.yml").write_text("name: towns\ntitle: Towns\n")
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")

    commit(working, "-m", "first", "--author", "t")

    version = repo.head(store, "refs/heads/main")
    assert version.tree is not None
    entries = json.loads(store.get(f"versions/{version.id}/tree.json"))["entries"]
    paths = {item["path"]: item["sha256"] for item in entries}
    assert set(paths) == {"README.md", "docs/img/map.png"}
    # Stored as written: links are resolved through the tree, not rewritten.
    assert store.get(objects.asset_key(paths["README.md"])).decode() == readme
    assert store.get(objects.asset_key(paths["docs/img/map.png"])) == b"\x89PNG"
    assert "attachments" not in json.loads(store.get(f"versions/{version.id}/dataset.json"))

    # The same tree again is nothing to commit; a changed README is a new version.
    assert "Nothing to commit" in commit(working, "-m", "same", "--author", "t")
    (tmp_path / "README.md").write_text(readme + "More.\n")
    commit(working, "-m", "readme", "--author", "t")
    assert repo.head(store, "refs/heads/main").tree.sha256 != version.tree.sha256


def test_a_version_without_a_readme_has_no_tree(tmp_path: Path, repository) -> None:
    store, working = repository
    (tmp_path / "dataset.yml").write_text("name: towns\ntitle: Towns\n")
    write(working, store, "CREATE TABLE lake.main.towns AS SELECT 1 AS id")
    commit(working, "-m", "first", "--author", "t")
    version = repo.head(store, "refs/heads/main")
    assert version.tree is None
    assert f"versions/{version.id}/tree.json" not in set(store.list(f"versions/{version.id}/"))


@pytest.mark.parametrize(
    "entries",
    [
        {"README.md": "a" * 64},
        [{"path": "../x", "sha256": "a" * 64, "bytes": 1}],
        [{"path": "README.md", "sha256": "A" * 64, "bytes": 1}],
        [{"path": "a", "sha256": "a" * 64, "bytes": 1}, {"path": "a", "sha256": "b" * 64, "bytes": 1}],
    ],
)
def test_a_tree_the_format_does_not_take_is_refused(entries) -> None:
    with pytest.raises(objects.ObjectError):
        objects.check_tree(entries)


def test_assets_are_uploaded_from_the_folder_and_checked(tmp_path: Path, repository) -> None:
    store, working = repository
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"%PDF-a")
    (folder / "b.pdf").write_bytes(b"%PDF-b")
    listed = list(csv.DictReader(io.StringIO(run("assets", folder))))
    assert [row["path"] for row in listed] == ["a.pdf", "b.pdf"]
    (tmp_path / "files.csv").write_text(run("assets", folder))
    (tmp_path / "dataset.yml").write_text(
        "name: reports\ntitle: Reports\ntables:\n"
        "  - schema: main\n    name: reports\n    fields:\n      - name: sha256\n        asset: true\n"
    )
    write(
        working,
        store,
        f"CREATE TABLE lake.main.reports AS SELECT path, sha256, media_type "
        f"FROM read_csv('{tmp_path / 'files.csv'}', all_varchar = true)",
    )

    # Without the folder, the files are nowhere to be had.
    run("commit", working, "-m", "m", "--author", "t")
    assert "neither in the repository" in fails("push", working)
    commit(working, "-m", "m", "--author", "t", "--assets", folder)

    for row in listed:
        assert store.get(objects.asset_key(row["sha256"])) == (folder / row["path"]).read_bytes()

    # The next version finds them in the repository and needs no folder.
    write(working, store, "INSERT INTO lake.main.reports SELECT * FROM lake.main.reports LIMIT 1")
    commit(working, "-m", "again", "--author", "t")


def test_an_asset_column_holds_only_sha256(tmp_path: Path, repository) -> None:
    store, working = repository
    (tmp_path / "dataset.yml").write_text(
        "name: reports\ntitle: Reports\ntables:\n"
        "  - schema: main\n    name: reports\n    fields:\n      - name: sha256\n        asset: true\n"
    )
    write(working, store, "CREATE TABLE lake.main.reports AS SELECT 'report.pdf' AS sha256")
    assert "64 lower-case hex" in fails("commit", working, "-m", "m", "--author", "t")


def test_an_asset_column_is_varchar(tmp_path: Path, repository) -> None:
    store, working = repository
    (tmp_path / "dataset.yml").write_text(
        "name: reports\ntitle: Reports\ntables:\n"
        "  - schema: main\n    name: reports\n    fields:\n      - name: n\n        asset: true\n"
    )
    write(working, store, "CREATE TABLE lake.main.reports AS SELECT 1 AS n")
    assert "has to be varchar" in fails("commit", working, "-m", "m", "--author", "t")


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


def test_withdrawal_deletes_the_assets_only_the_withdrawn_versions_reach(tmp_path: Path, repository) -> None:
    store, working = repository
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "kept.pdf").write_bytes(b"%PDF-kept")
    (folder / "leak.pdf").write_bytes(b"%PDF-leak")
    (tmp_path / "files.csv").write_text(run("assets", folder))
    (tmp_path / "README.md").write_text("# Reports\n")
    (tmp_path / "dataset.yml").write_text(
        "name: reports\ntitle: Reports\ntables:\n"
        "  - schema: main\n    name: reports\n    fields:\n      - name: sha256\n        asset: true\n"
    )
    write(
        working,
        store,
        f"CREATE TABLE lake.main.reports AS SELECT path, sha256 FROM read_csv('{tmp_path / 'files.csv'}', all_varchar = true)",
    )
    commit(working, "-m", "leak", "--author", "t", "--assets", folder)
    leaked = repo.head(store, "refs/heads/main")
    write(working, store, "DELETE FROM lake.main.reports WHERE path = 'leak.pdf'")
    commit(working, "-m", "clean", "--author", "t")
    rows = {row["path"]: row["sha256"] for row in csv.DictReader(io.StringIO(run("assets", folder)))}
    readme = objects.asset_key(objects.sha256_of(tmp_path / "README.md"))

    plan = repo.plan_withdrawal(store, [leaked.id])
    assert objects.asset_key(rows["leak.pdf"]) in plan.deleted
    assert {objects.asset_key(rows["kept.pdf"]), readme} <= set(plan.still_read)

    run("withdraw", store.root, leaked.id, "--yes")
    assert store.head(objects.asset_key(rows["leak.pdf"])) is None
    assert store.head(objects.asset_key(rows["kept.pdf"])) is not None
    assert store.head(readme) is not None
