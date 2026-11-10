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

from . import catalog, dataset, layout, objects, refs, repo
from . import diff as diffs
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
    objects.ObjectError,
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
    assets_dir: Path | None = typer.Option(
        None,
        "--assets",
        exists=True,
        file_okay=False,
        help="A folder with the files the asset columns point at, to upload those the repository lacks",
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
        description = describe or dataset.find()
        declared = dataset.gather(description)
        root = Path(description).resolve().parent
        listed = objects.tree(root)
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
                tree=listed,
                allow_empty=allow_empty,
                working_data=Path(state["data"]).resolve().as_posix() + "/" if state.get("data") else None,
            )

        try:
            version = make(allow_empty)
        except repo.CannotCompare as exc:
            typer.echo(f"warning: could not tell whether anything changed ({exc}); committing", err=True)
            version = make(True)
        local = Path(state["data"]) if state.get("data") else None
        reading = Path(state["data"]).resolve().as_posix() + "/" if local else repo.data_url(store)
        setup = _read_setup(store)
        described = json.loads((out / version.id / layout.DATASET).read_text(encoding="utf-8"))
        held = objects.asset_values(
            working, objects.asset_columns(described), data_path=reading, setup=setup
        )
        try:
            repo.write_profiles(store, out / version.id, parent, working, data_path=reading, setup=setup)
        except Exception as exc:  # noqa: BLE001 - a profile is never worth a failed commit
            typer.echo(f"warning: tables were not profiled ({exc})", err=True)
        local_objects = objects.ByContent(
            {item["sha256"]: root / item["path"] for item in listed},
            assets_dir,
        )
        repo.push(
            store,
            out / version.id,
            version,
            ref=state["ref"],
            local_data=local,
            assets=held,
            local_objects=local_objects,
        )
    except repo.NothingToCommit as exc:
        # The branch may have moved on since the checkout, so say what the
        # working catalog matches rather than where the branch is.
        typer.echo(f"Nothing to commit: {exc}; {state['ref']} was not moved")
        return
    except ERRORS as exc:
        raise _fail(exc) from exc
    finally:
        shutil.rmtree(out, ignore_errors=True)
    if state.get("data"):
        _drop_uploaded(store, working, Path(state["data"]), version)
    _write_state(working, {**state, "base": version.id})
    typer.echo(f"{state['ref']} -> {version.id} (version {version.seq})")


def _drop_uploaded(store, working: Path, local: Path, version) -> None:
    """Free the disk the uploaded data files took.

    The working catalog is made again from the version just pushed, so it
    reads those files from the repository, and the local copies go.
    """
    fresh = working.with_name(f".{working.name}.{version.id}.sqlite")
    try:
        repo.checkout(store, fresh, local_data=local, version=version)
    except ERRORS as exc:
        typer.echo(f"warning: kept the local data files ({exc})", err=True)
        fresh.unlink(missing_ok=True)
        return
    for sidecar in (working.with_name(working.name + suffix) for suffix in ("-wal", "-shm")):
        sidecar.unlink(missing_ok=True)
    fresh.replace(working)
    shutil.rmtree(local, ignore_errors=True)
    local.mkdir(parents=True, exist_ok=True)


def _read_setup(store) -> list[str]:
    """What DuckDB needs to read the repository's data files: the token, over HTTP."""
    token = getattr(store, "token", None)
    if not token:
        return []
    scope = store.url.replace("'", "''")
    secret = token.replace("'", "''")
    return [f"CREATE SECRET fdl_remote (TYPE http, BEARER_TOKEN '{secret}', SCOPE '{scope}')"]


@app.command()
def assets(folder: Path = typer.Argument(..., help="The folder of files the rows will point at")) -> None:
    """List the files below a folder as CSV: path, sha256, bytes, media_type.

    Read it into the table that points at them (DuckDB's read_csv, say), and
    mark its sha256 column with ``asset: true``. `fdl commit --assets <folder>`
    then uploads the files the repository lacks.
    """
    import csv
    import sys

    try:
        found = objects.manifest(folder)
    except ERRORS as exc:
        raise _fail(exc) from exc
    writer = csv.DictWriter(sys.stdout, fieldnames=["path", "sha256", "bytes", "media_type"], lineterminator="\n")
    writer.writeheader()
    writer.writerows(found)


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
def url(
    remote: str = Remote,
    ref: str = typer.Option(layout.HEAD),
    attach: bool = typer.Option(False, "--attach", help="Print the whole ATTACH statement, with the data path"),
    alias: str = typer.Option("lake", help="The name to ATTACH it as, with --attach"),
) -> None:
    """Where the catalog of the version a ref holds is, to ATTACH."""
    store = open_store(remote)
    try:
        version = repo.readable(store, ref)
        where = store.url.rstrip("/") + "/" + layout.version_catalog(version.id)
        data = repo.data_url(store)
    except ERRORS as exc:
        raise _fail(exc) from exc
    if not attach:
        typer.echo(where)
        return
    for statement in _read_setup(store):
        typer.echo(statement + ";")
    where, data = (value.replace("'", "''") for value in (where, data))
    typer.echo(
        f"ATTACH 'ducklake:{where}' AS {alias} (DATA_PATH '{data}', OVERRIDE_DATA_PATH true, READ_ONLY);"
    )


def _version(store, target: str):
    """The version ``target`` names: a version ID, or a ref."""
    if is_id(target):
        return repo.read_version(store, target)
    found = repo.head(store, target)
    if found is None:
        raise repo.RepoError(f"{target} holds no version yet")
    return found


def _print_diff(result: dict, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    for line in diffs.lines(result) or ["No changes"]:
        typer.echo(line)


@app.command()
def diff(
    remote: str = Remote,
    target: str = typer.Argument(layout.HEAD, help="A version ID, or a ref"),
    against: str | None = typer.Option(None, help="The version or ref to compare with (default: the target's parent)"),
    as_json: bool = typer.Option(False, "--json", help="Print the differences as JSON"),
) -> None:
    """What changed in a version: tables, rows, columns, data files and descriptions.

    Compared with the version's parent, or with --against.
    """
    store = open_store(remote)
    try:
        new = _version(store, target)
        old = _version(store, against) if against else (repo.read_version(store, new.parents[0]) if new.parents else None)
        result = diffs.versions(store, old, new)
    except ERRORS as exc:
        raise _fail(exc) from exc
    _print_diff(result, as_json)


@app.command()
def show(
    remote: str = Remote,
    target: str = typer.Argument(layout.HEAD, help="A version ID, or a ref"),
    as_json: bool = typer.Option(False, "--json", help="Print version.json and the differences as JSON"),
) -> None:
    """A version: its record, its tables, and what changed from its parent."""
    store = open_store(remote)
    try:
        version = _version(store, target)
        parent = repo.read_version(store, version.parents[0]) if version.parents else None
        expired = repo.is_expired(store, version.id)
        described = None if expired else diffs.described(store, version)
        result = None if expired else diffs.versions(store, parent, version)
    except ERRORS as exc:
        raise _fail(exc) from exc
    if as_json:
        typer.echo(json.dumps(
            {"version": json.loads(version.to_json()), "expired": expired, "diff": result},
            ensure_ascii=False, indent=2,
        ))
        return
    typer.echo(f"version {version.seq}  {version.id}" + ("  (expired)" if expired else ""))
    typer.echo(f"parent    {version.parents[0] if version.parents else '-'}")
    typer.echo(f"author    {version.author.get('name', '')}")
    typer.echo(f"date      {version.created_at}")
    typer.echo("")
    typer.echo(f"    {version.message}")
    if described:
        typer.echo("")
        for table in described.get("tables", []):
            rows = f"{table['rows']:,} row{'' if table['rows'] == 1 else 's'}" if isinstance(table.get("rows"), int) else table.get("kind", "")
            typer.echo(f"  {table.get('schema')}.{table.get('name')}  {rows}")
    if result is not None:
        typer.echo("")
        for line in diffs.lines(result) or ["No changes from the parent"]:
            typer.echo(line)


@app.command()
def status(
    working: Path = Working,
    as_json: bool = typer.Option(False, "--json", help="Print the differences as JSON"),
) -> None:
    """What the working catalog changed since it was checked out."""
    state = _read_state(working)
    store = open_store(state["remote"])
    try:
        base = repo.read_version(store, state["base"]) if state["base"] else None
        result = diffs.working(working, base)
    except (*ERRORS, catalog.CatalogError) as exc:
        raise _fail(exc) from exc
    if as_json:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    typer.echo(f"On {state['ref']} of {state['remote']}, from " + (f"{base.id} (version {base.seq})" if base else "no version yet"))
    for line in diffs.lines(result) or ["Nothing to commit"]:
        typer.echo(line)


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
    tags that point at the versions, the data files and assets no kept version
    reaches, and the versions' catalogs, at once. Their version.json stays. A
    version already past retention has no catalog, so then every data file and
    asset no live version reaches is deleted.
    """
    store = open_store(remote)
    try:
        plan = repo.plan_withdrawal(store, versions, setup=_read_setup(store))
        for name in plan.tags:
            typer.echo(f"delete tag   {name}")
        for key in plan.deleted:
            typer.echo(f"delete file  {key}")
        for version_id in plan.versions:
            typer.echo(f"withdraw     {version_id}")
        for key in plan.still_read:
            typer.echo(f"keep file    {key} (a kept version reaches it)")
        if not yes:
            typer.echo("Nothing was changed. Run again with --yes to do this.")
            return
        repo.withdraw(store, versions, plan=plan)
    except ERRORS as exc:
        raise _fail(exc) from exc
    typer.echo(f"Withdrew {len(plan.versions)} version(s) and deleted {len(plan.deleted)} file(s).")


def main() -> None:
    app()
