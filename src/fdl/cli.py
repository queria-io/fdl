"""The ``fdl`` command: the operations in `fdl.repo`, on a terminal.

A working catalog is a DuckLake catalog in SQLite that you write to with any
DuckDB client. Beside it sits ``<working>.fdl.json``, which remembers the
repository it came from and the version it was checked out at, so a commit
knows what it builds on.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import typer

from . import catalog, layout, refs, repo
from .stores import StoreError, open_store
from .version import VersionError

app = typer.Typer(no_args_is_help=True, pretty_exceptions_short=True)

Remote = typer.Argument(help="The repository: s3://bucket/prefix/ or a folder")
Working = typer.Argument(help="The working catalog (.sqlite)")


def _state_path(working: Path) -> Path:
    return working.with_name(working.name + ".fdl.json")


def _read_state(working: Path) -> dict:
    path = _state_path(working)
    if not path.is_file():
        raise typer.BadParameter(f"{working} was not made by `fdl checkout`")
    return json.loads(path.read_text())


def _write_state(working: Path, state: dict) -> None:
    _state_path(working).write_text(json.dumps(state, indent=2) + "\n")


def _fail(exc: Exception) -> typer.Exit:
    typer.echo(f"error: {exc}", err=True)
    return typer.Exit(1)


ERRORS = (repo.RepoError, refs.RefError, layout.LayoutError, VersionError, StoreError)


@app.command()
def init(
    remote: str = Remote,
    data_dir: str = typer.Option(layout.DEFAULT_DATA_DIR, help="Where data files go, under the root"),
) -> None:
    """Start a repository (fdl.json and HEAD). Harmless on one that exists."""
    try:
        config = repo.init(open_store(remote), data_dir=data_dir)
    except ERRORS as exc:
        raise _fail(exc) from exc
    typer.echo(f"fdl {config.fdl} repository, data in {config.data_dir}")


@app.command()
def checkout(
    remote: str = Remote,
    working: Path = Working,
    ref: str = typer.Option(layout.DEFAULT_BRANCH, help="The branch to build on"),
) -> None:
    """Make a working catalog from the version a branch holds."""
    store = open_store(remote)
    try:
        version = repo.checkout(store, working, ref=ref)
        data = repo.data_url(store)
    except ERRORS as exc:
        raise _fail(exc) from exc
    _write_state(working, {"remote": remote, "ref": ref, "base": version.id if version else None})
    typer.echo(f"{working} at {version.id if version else 'an empty catalog'}")
    typer.echo("Write to it with:")
    typer.echo("  " + catalog.attach_sql(working, alias="lake", data_path=data)[-1] + ";")


@app.command()
def commit(
    working: Path = Working,
    message: str = typer.Option(..., "-m", "--message", help="What changed"),
    author: str = typer.Option(..., help="Who made it"),
) -> None:
    """Freeze the working catalog into a version and push it to its branch."""
    state = _read_state(working)
    store = open_store(state["remote"])
    out = Path(tempfile.mkdtemp(prefix="fdl-"))
    try:
        parent = repo.read_version(store, state["base"]) if state["base"] else None
        version = repo.commit(
            working,
            out,
            parent=parent,
            data_path=repo.data_url(store),
            author={"name": author},
            message=message,
        )
        repo.push(store, out / version.id, version, ref=state["ref"])
    except ERRORS as exc:
        raise _fail(exc) from exc
    finally:
        shutil.rmtree(out, ignore_errors=True)
    _write_state(working, {**state, "base": version.id})
    typer.echo(f"{state['ref']} -> {version.id} (version {version.seq})")


@app.command()
def log(remote: str = Remote, ref: str = typer.Option(layout.HEAD)) -> None:
    """Versions from the one a ref holds back to the first."""
    store = open_store(remote)
    try:
        for version in repo.log(store, ref):
            mark = " (expired)" if repo.is_expired(store, version.id) else ""
            typer.echo(f"{version.seq:>4}  {version.id}  {version.created_at}  {version.author['name']}{mark}")
            typer.echo(f"      {version.message}")
    except ERRORS as exc:
        raise _fail(exc) from exc


@app.command()
def url(remote: str = Remote, ref: str = typer.Option(layout.HEAD)) -> None:
    """Where the catalog of the version a ref holds is, to ATTACH."""
    store = open_store(remote)
    try:
        version = repo.head(store, ref)
    except ERRORS as exc:
        raise _fail(exc) from exc
    if version is None:
        raise _fail(repo.RepoError(f"{ref} holds no version yet"))
    typer.echo(store.url.rstrip("/") + "/" + layout.version_catalog(version.id))


@app.command()
def expire(
    remote: str = Remote,
    keep: int = typer.Option(1, help="How many of the newest versions to keep"),
    since: str | None = typer.Option(None, help="Also keep those made at or after this time (ISO 8601)"),
    ref: str = typer.Option(layout.DEFAULT_BRANCH),
) -> None:
    """Take older versions out of retention: mark them and drop their catalogs.

    Data files are left in place; versions that are kept still share them.
    """
    store = open_store(remote)
    try:
        newest = repo.head(store, ref)
        expired = repo.prune(store, newest, keep=keep, since=since) if newest else []
    except ERRORS as exc:
        raise _fail(exc) from exc
    typer.echo(f"Expired {len(expired)} version(s).")


def main() -> None:
    app()
