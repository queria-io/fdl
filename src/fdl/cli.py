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

from . import catalog, dataset, layout, refs, repo
from .stores import StoreError, open_store
from .version import VersionError, is_id

app = typer.Typer(no_args_is_help=True, pretty_exceptions_short=True)

Remote = typer.Argument(help="The repository: s3://bucket/prefix/, https://host/path/ or a folder")
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


ERRORS = (
    repo.RepoError,
    refs.RefError,
    layout.LayoutError,
    VersionError,
    StoreError,
    dataset.DatasetError,
)


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
    # A repository over HTTP takes no writes from DuckDB: new data files go to
    # a folder beside the working catalog until the commit uploads them.
    local = None if repo.writes_data_files(store) else working.with_name(working.name + ".data")
    try:
        version = repo.checkout(store, working, ref=ref, local_data=local)
        data = repo.data_url(store) if local is None else local.resolve().as_posix() + "/"
    except ERRORS as exc:
        raise _fail(exc) from exc
    state = {"remote": remote, "ref": ref, "base": version.id if version else None}
    _write_state(working, {**state, **({"data": str(local)} if local else {})})
    typer.echo(f"{working} at {version.id if version else 'an empty catalog'}")
    typer.echo("Write to it with:")
    typer.echo("  " + catalog.attach_sql(working, alias="lake", data_path=data)[-1] + ";")


@app.command()
def commit(
    working: Path = Working,
    message: str = typer.Option(..., "-m", "--message", help="What changed"),
    author: str = typer.Option(..., help="Who made it"),
    describe: Path | None = typer.Option(
        None,
        "--dataset",
        help="The description of what the version holds (default: dataset.json or dataset.yml here), with any *.table.yml below it",
    ),
    allow_empty: bool = typer.Option(
        False, "--allow-empty", help="Make a version even if nothing changed since the last one"
    ),
) -> None:
    """Freeze the working catalog into a version and push it to its branch.

    Nothing is made when the tables, their rows and the description are what
    the last version holds.
    """
    state = _read_state(working)
    store = open_store(state["remote"])
    out = Path(tempfile.mkdtemp(prefix="fdl-"))
    try:
        declared = dataset.gather(describe or dataset.find())
        parent = repo.read_version(store, state["base"]) if state["base"] else None

        def make(allow_empty: bool):
            return repo.commit(
                working,
                out,
                parent=parent,
                data_path=repo.data_url(store),
                author={"name": author},
                message=message,
                dataset=declared,
                allow_empty=allow_empty,
                working_data=Path(state["data"]).resolve().as_posix() + "/" if state.get("data") else None,
            )

        try:
            version = make(allow_empty)
        except repo.CannotCompare as exc:
            typer.echo(f"warning: could not tell whether anything changed ({exc}); committing", err=True)
            version = make(True)
        local = Path(state["data"]) if state.get("data") else None
        repo.push(store, out / version.id, version, ref=state["ref"], local_data=local)
    except repo.NothingToCommit as exc:
        # The branch may have moved on since the checkout, so say what the
        # working catalog matches rather than where the branch is.
        typer.echo(f"Nothing to commit: {exc}; {state['ref']} was not moved")
        return
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
        version = repo.readable(store, ref)
    except ERRORS as exc:
        raise _fail(exc) from exc
    typer.echo(store.url.rstrip("/") + "/" + layout.version_catalog(version.id))


@app.command()
def tag(
    remote: str = Remote,
    name: str | None = typer.Argument(None, help="The tag, without refs/tags/. Omit to list the tags"),
    target: str = typer.Argument(layout.HEAD, help="A version ID, or the ref whose version to tag"),
    delete: bool = typer.Option(False, "-d", "--delete", help="Delete the tag"),
) -> None:
    """List tags, tag a version, or delete a tag. Tags never move."""
    store = open_store(remote)
    try:
        if name is None:
            for tag_name, version_id in repo.tags(store).items():
                typer.echo(f"{tag_name}  {version_id}")
            return
        if delete:
            repo.delete_tag(store, name)
            typer.echo(f"Deleted refs/tags/{name}")
            return
        version_id = target if is_id(target) else refs.resolve(store, target)[1]
        if version_id is None:
            raise repo.RepoError(f"{target} holds no version yet")
        repo.tag(store, name, version_id)
    except ERRORS as exc:
        raise _fail(exc) from exc
    typer.echo(f"refs/tags/{name} -> {version_id}")


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


@app.command()
def withdraw(
    remote: str = Remote,
    versions: list[str] = typer.Argument(..., help="The versions that hold the data"),
    yes: bool = typer.Option(False, "--yes", help="Do it. Without this, only say what would be done"),
) -> None:
    """Remove versions that hold data that must not be published.

    Move every branch to a version without the data first. This deletes the
    tags that point at the versions, the data files no kept version reads,
    and the versions' catalogs, at once. Their version.json stays.
    """
    store = open_store(remote)
    try:
        plan = repo.plan_withdrawal(store, versions)
        for name in plan.tags:
            typer.echo(f"delete tag   {name}")
        for key in plan.deleted:
            typer.echo(f"delete file  {key}")
        for version_id in plan.versions:
            typer.echo(f"withdraw     {version_id}")
        for key in plan.still_read:
            typer.echo(f"keep file    {key} (a kept version reads it)")
        if not yes:
            typer.echo("Nothing was changed. Run again with --yes to do this.")
            return
        repo.withdraw(store, versions, plan=plan)
    except ERRORS as exc:
        raise _fail(exc) from exc
    typer.echo(f"Withdrew {len(plan.versions)} version(s) and deleted {len(plan.deleted)} file(s).")


def main() -> None:
    app()
