"""The ``fdl`` command: the operations in `fdl.repo`, on a terminal.

A working catalog is a DuckLake catalog in SQLite that you write to with any
DuckDB client. Beside it sits ``<working>.fdl.json``, which remembers the
repository it came from and the version it was checked out at, so a commit
knows what it builds on. A commit waits in ``<working>.pending/`` until it is
pushed.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import typer

from . import catalog, dataset, layout, objects, refs, repo
from . import merge as merging
from . import diff as diffs
from .stores import StoreError, open_store
from .version import VersionError, is_id
from .version import parse as parse_version

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


def _store(remote: str):
    """The store at ``remote``, or a clean error for one it refuses (plain http, say)."""
    try:
        return open_store(remote)
    except StoreError as exc:
        raise _fail(exc) from exc


@app.command()
def init(
    remote: str = Remote,
    data_dir: str = typer.Option(layout.DEFAULT_DATA_DIR, help="Where data files go, under the root"),
) -> None:
    """Start a repository (fdl.json and HEAD). Harmless on one that exists."""
    try:
        config = repo.init(_store(remote), data_dir=data_dir)
    except ERRORS as exc:
        raise _fail(exc) from exc
    typer.echo(f"fdl {config.fdl} repository, data in {config.data_dir}")


@app.command()
def checkout(
    remote: str = Remote,
    working: Path = Working,
    ref: str = typer.Option(layout.DEFAULT_BRANCH, help="The branch to build on"),
    storage: str | None = typer.Option(
        None,
        help="The repository's root in its object storage (s3://…), to write new data files there directly instead of uploading them on commit",
    ),
) -> None:
    """Make a working catalog from the version a branch holds."""
    store = _store(remote)
    # A repository over HTTP takes no writes from DuckDB: new data files go to
    # a folder beside the working catalog until the commit uploads them, or
    # straight to the repository's storage when the writer has keys to it.
    local = (
        None
        if storage or repo.writes_data_files(store)
        else working.with_name(working.name + ".data")
    )
    try:
        version = repo.checkout(store, working, ref=ref, local_data=local, storage=storage)
        if storage:
            data = repo.storage_data_path(storage, repo.read_config(store))
        else:
            data = repo.data_url(store) if local is None else local.resolve().as_posix() + "/"
    except ERRORS as exc:
        raise _fail(exc) from exc
    state = {"remote": remote, "ref": ref, "base": version.id if version else None}
    if local:
        state["data"] = str(local)
    if storage:
        state["storage"] = storage
    _write_state(working, state)
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
    """Freeze the working catalog into a version, kept beside it until you push it.

    Nothing is made when the tables, their rows and the description are what
    the last pushed version holds. Committing again before pushing replaces
    the version not pushed yet.
    """
    state = _read_state(working)
    store = _store(state["remote"])
    try:
        description = describe or dataset.find()
        declared = dataset.gather(description)
        root = Path(description).resolve().parent
        listed = objects.tree(root)
        version = _commit(
            store,
            working,
            state,
            message=message,
            author=author,
            declared=declared,
            listed=listed,
            local_objects={item["sha256"]: str(root / item["path"]) for item in listed},
            assets_dir=assets_dir,
            allow_empty=allow_empty,
        )
    except repo.NothingToCommit as exc:
        typer.echo(f"Nothing to commit: {exc}")
        return
    except ERRORS as exc:
        raise _fail(exc) from exc
    typer.echo(f"Committed {version.id} (version {version.seq}). Profile its tables and push it to {state['ref']} with:")
    typer.echo(f"  fdl profile {working}")
    typer.echo(f"  fdl push {working}")


def _pending_dir(working: Path) -> Path:
    """Where the committed version not pushed yet waits, as ``<ID>/``."""
    return working.with_name(working.name + ".pending")


def _commit(
    store,
    working: Path,
    state: dict,
    *,
    message: str,
    author: str,
    declared: dict,
    listed: list[dict],
    local_objects: dict[str, str],
    assets_dir: Path | None,
    allow_empty: bool,
    merged=None,
):
    """Freeze the working catalog into a version beside it, to push later.

    Its parent is the version the working catalog was checked out at (or last
    pushed). Any version committed before and not pushed is replaced.
    """
    out = Path(tempfile.mkdtemp(prefix=".fdl-commit-", dir=working.resolve().parent))
    try:
        parent = repo.read_version(store, state["base"]) if state["base"] else None
        parent_dataset = diffs.described(store, parent) if parent else None
        written = _written(store, state)

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
                working_data=written,
                parent_dataset=parent_dataset,
                merged=merged,
            )

        try:
            version = make(allow_empty)
        except repo.CannotCompare as exc:
            typer.echo(f"warning: could not tell whether anything changed ({exc}); committing", err=True)
            version = make(True)
        reading = written or repo.data_url(store)
        setup = _read_setup(store) + _storage_setup(state.get("storage"))
        described = json.loads((out / version.id / layout.DATASET).read_text(encoding="utf-8"))
        held = objects.asset_values(
            working, objects.asset_columns(described), data_path=reading, setup=setup
        )
        pending = _pending_dir(working)
        shutil.rmtree(pending, ignore_errors=True)
        out.rename(pending)
    except repo.NothingToCommit:
        # The working catalog holds what the last pushed version holds, so a
        # version committed before and not pushed no longer matches it.
        _drop_pending(working, state)
        raise
    finally:
        shutil.rmtree(out, ignore_errors=True)
    _write_state(
        working,
        {
            **state,
            "pending": {
                "id": version.id,
                "assets": sorted(held),
                "objects": local_objects,
                "assets_dir": str(assets_dir.resolve()) if assets_dir else None,
            },
        },
    )
    return version


def _drop_pending(working: Path, state: dict, **changes) -> None:
    """Forget the version committed and not pushed, and delete it."""
    _write_state(working, {key: value for key, value in state.items() if key != "pending"} | changes)
    shutil.rmtree(_pending_dir(working), ignore_errors=True)


def _written(store, state: dict) -> str | None:
    """Where the working catalog writes its new data files, when not where it records."""
    if state.get("storage"):
        return repo.storage_data_path(state["storage"], repo.read_config(store))
    if state.get("data"):
        return Path(state["data"]).resolve().as_posix() + "/"
    return None


@app.command()
def push(working: Path = Working) -> None:
    """Upload the committed version and move the branch to it.

    Refused when someone else moved the branch since the checkout: check out
    again, redo the work, commit and push, or merge the branch into your work.
    """
    state = _read_state(working)
    pending = state.get("pending")
    if not pending:
        if state.get("forward"):
            _push_forward(working, state)
            return
        typer.echo("Nothing to push: commit first")
        return
    store = _store(state["remote"])
    folder = _pending_dir(working) / pending["id"]
    try:
        version = parse_version((folder / layout.VERSION_RECORD).read_bytes(), expected_id=pending["id"])
        assets_dir = pending.get("assets_dir")
        repo.push(
            store,
            folder,
            version,
            ref=state["ref"],
            local_data=Path(state["data"]) if state.get("data") else None,
            assets=pending["assets"],
            local_objects=objects.ByContent(
                {sha: Path(path) for sha, path in pending["objects"].items()},
                Path(assets_dir) if assets_dir else None,
            ),
        )
    except (*ERRORS, OSError) as exc:
        raise _fail(exc) from exc
    # The version is pushed: record it before anything else can fail.
    _drop_pending(working, {key: value for key, value in state.items() if key != "forward"}, base=version.id)
    if state.get("data"):
        _drop_uploaded(store, working, Path(state["data"]), version)
    typer.echo(f"{state['ref']} -> {version.id} (version {version.seq})")


def _push_forward(working: Path, state: dict) -> None:
    """Move the branch to the version a fast-forward merge checked out."""
    store = _store(state["remote"])
    try:
        version = repo.read_version(store, state["base"])
        repo.forward(store, state["ref"], version)
    except ERRORS as exc:
        raise _fail(exc) from exc
    _write_state(working, {key: value for key, value in state.items() if key != "forward"})
    typer.echo(f"{state['ref']} -> {version.id} (version {version.seq})")


@app.command()
def revert(
    version_id: str = typer.Argument(help="The version whose contents come back: a version ID, or a ref"),
    working: Path = Working,
    message: str | None = typer.Option(None, "-m", "--message", help="Why (default: Revert to version <seq>)"),
    author: str = typer.Option(..., help="Who made it"),
) -> None:
    """Commit a new version that holds what an earlier version held.

    The working catalog is written back to the earlier version's tables, and
    the new version builds on the one it was checked out at, so the branch
    moves forward when you push: the versions in between stay in its history.
    Changes not committed are lost. To take out data that must not be
    published, use withdraw instead.
    """
    state = _read_state(working)
    store = _store(state["remote"])
    try:
        target = _version(store, version_id)
        if repo.is_expired(store, target.id):
            raise repo.RepoError(f"{target.id} is past retention or withdrawn and cannot come back")
        base = repo.read_version(store, state["base"]) if state["base"] else None
        # By first parent: the snapshots of a version merged in belong to another catalog.
        if base is None or not repo.in_history(store, target.id, base):
            raise repo.RepoError(f"{target.id} is not in the first-parent history of {base.id if base else 'the working catalog'}")
        # Read before the working catalog is written back, so a refusal leaves it as it was.
        declared = diffs.described(store, target)
        if declared is None:
            raise repo.RepoError(f"{target.id} has no dataset.json to bring back")
        listed = (diffs.listed(store, target) or {}).get("entries", [])
        catalog.restore(
            working,
            target.ducklake.snapshot_id,
            data_path=_written(store, state) or repo.data_url(store),
            setup=_read_setup(store) + _storage_setup(state.get("storage")),
        )
        version = _commit(
            store,
            working,
            state,
            message=message or f"Revert to version {target.seq}",
            author=author,
            declared=declared,
            listed=listed,
            local_objects={},
            assets_dir=None,
            allow_empty=False,
        )
    except repo.NothingToCommit:
        typer.echo(f"Nothing to revert: {base.id} already holds what {target.id} (version {target.seq}) held")
        return
    except (*ERRORS, catalog.CatalogError) as exc:
        raise _fail(exc) from exc
    typer.echo(f"Committed {version.id} (version {version.seq}), holding what version {target.seq} held. Push it with:")
    typer.echo(f"  fdl push {working}")


@app.command()
def merge(
    theirs_name: str = typer.Argument(..., metavar="THEIRS", help="What to merge: a ref such as refs/heads/<name>, or a version ID"),
    working: Path = Working,
    message: str | None = typer.Option(None, "-m", "--message", help="What the merge brings (default: Merge <THEIRS>)"),
    author: str = typer.Option(..., help="Who made it"),
) -> None:
    """Merge another branch into the working catalog's branch.

    Tables, rows and descriptions only the other branch changed come over.
    Rows of a table both branches changed are merged by the primary key both
    declare in dataset.json; anything both changed differently is a conflict,
    and then nothing is changed. The merge version waits beside the working
    catalog until you push it.
    """
    state = _read_state(working)
    store = _store(state["remote"])
    if state.get("pending"):
        raise _fail(repo.RepoError(f"a version is committed and not pushed: fdl push {working} first"))
    try:
        theirs = _version(store, theirs_name)
        ours = repo.read_version(store, state["base"]) if state["base"] else None
        if ours is None:
            raise repo.RepoError(f"{state['ref']} holds no version yet, so there is nothing to merge into")
        if catalog.last_snapshot(working) != ours.ducklake.snapshot_id:
            raise repo.RepoError(f"the working catalog has changes not committed: commit and push them, or check out again")
        bases = repo.merge_bases(store, ours, theirs)
        if not bases:
            raise repo.RepoError(f"{ours.id} and {theirs.id} have no version in common")
        if len(bases) > 1:
            raise repo.RepoError(f"{ours.id} and {theirs.id} have more than one merge base: " + ", ".join(v.id for v in bases))
        base = bases[0]
        if base.id == theirs.id:
            typer.echo(f"Already up to date: {theirs.id} (version {theirs.seq}) is in the history of {ours.id}")
            return
        if base.id == ours.id:
            _fast_forward(store, working, state, theirs)
            return
        for version in (base, theirs):
            if repo.is_expired(store, version.id):
                raise repo.RepoError(f"{version.id} is past retention, so the merge cannot read it")
        version, plan = _merge(store, working, state, ours, theirs, base, message=message or f"Merge {theirs_name}", author=author)
    except merging.Conflicts as exc:
        typer.echo(f"Merging {theirs_name} into {state['ref']}: {len(exc.conflicts)} conflict(s). Nothing was changed.", err=True)
        for conflict in exc.conflicts:
            typer.echo(f"  conflict  {conflict}", err=True)
        raise typer.Exit(1) from exc
    except (*ERRORS, catalog.CatalogError) as exc:
        raise _fail(exc) from exc
    for key in plan.taken:
        typer.echo(f"  from theirs  {'.'.join(key)}")
    for key in plan.dropped:
        typer.echo(f"  dropped      {'.'.join(key)}")
    for key, count in plan.rows.items():
        typer.echo(f"  rows merged  {'.'.join(key)} ({count:,} row(s) added, changed or deleted by theirs)")
    if plan.described:
        typer.echo(
            f"warning: theirs changed {' and '.join(plan.described)}. Bring those changes into your local "
            "description before the next fdl commit, which reads it from there",
            err=True,
        )
    typer.echo(f"Committed {version.id} (version {version.seq}), merging {theirs.id}. Push it with:")
    typer.echo(f"  fdl push {working}")


def _fast_forward(store, working: Path, state: dict, theirs) -> None:
    """Check the working catalog out at ``theirs``, for push to move the branch there."""
    local = Path(state["data"]) if state.get("data") else None
    repo.checkout(store, working, local_data=local, version=theirs, storage=state.get("storage"))
    _write_state(working, {**state, "base": theirs.id, "forward": True})
    typer.echo(f"Fast-forward to {theirs.id} (version {theirs.seq}). Move {state['ref']} there with:")
    typer.echo(f"  fdl push {working}")


def _merge(store, working: Path, state: dict, ours, theirs, base, *, message: str, author: str):
    """Write the merge into the working catalog and commit it, or raise `merging.Conflicts`."""
    described = {name: diffs.described(store, version) for name, version in (("base", base), ("ours", ours), ("theirs", theirs))}
    listed = {name: diffs.listed(store, version) for name, version in (("base", base), ("ours", ours), ("theirs", theirs))}
    declared, conflicts = merging.values(*(merging.declared(described[name]) for name in ("base", "ours", "theirs")))
    tree, tree_conflicts = merging.values(*(listed[name] or merging.MISSING for name in ("base", "ours", "theirs")))
    conflicts = [f"dataset.json: {item}" for item in conflicts] + [f"tree.json: {item}" for item in tree_conflicts]
    with tempfile.TemporaryDirectory(prefix="fdl-merge-") as tmp:
        frozen = {}
        for name, version in (("base", base), ("theirs", theirs)):
            frozen[name] = Path(tmp) / f"{name}.duckdb"
            frozen[name].write_bytes(store.get(layout.version_catalog(version.id)))
            repo.check_catalog(frozen[name], version)
        plan = merging.tables(
            working,
            frozen["base"],
            frozen["theirs"],
            keys=merging.primary_keys(described["base"], described["ours"], described["theirs"]),
            data_path=_written(store, state),
            repository_data=repo.data_url(store),
            setup=_read_setup(store) + _storage_setup(state.get("storage")),
            # Written only when a version can be committed from it.
            write=not conflicts and declared is not merging.MISSING,
        )
    conflicts += plan.conflicts
    if conflicts:
        raise merging.Conflicts(conflicts)
    if declared is merging.MISSING:
        raise repo.RepoError(f"{ours.id} has no dataset.json")
    version = _commit(
        store,
        working,
        state,
        message=message,
        author=author,
        declared=declared,
        listed=[] if tree is merging.MISSING else tree.get("entries", []),
        local_objects={},
        assets_dir=None,
        allow_empty=True,
        merged=theirs,
    )
    plan.described = [
        name
        for name, merged, own in (
            ("dataset.json", declared, merging.declared(described["ours"])),
            ("tree.json", tree, listed["ours"] or merging.MISSING),
        )
        if merged != own
    ]
    return version, plan


def _drop_uploaded(store, working: Path, local: Path, version) -> None:
    """Free the disk the uploaded data files took.

    The working catalog is made again from the version just pushed, so it
    reads those files from the repository, and the local copies go. The
    conversion replaces the working catalog only once it is complete, so on
    failure the working catalog and its local files stay as they were.
    """
    try:
        repo.checkout(store, working, local_data=local, version=version)
    except (*ERRORS, catalog.CatalogError, OSError) as exc:
        typer.echo(f"warning: kept the local data files ({exc})", err=True)
        return
    shutil.rmtree(local, ignore_errors=True)
    local.mkdir(parents=True, exist_ok=True)


def _storage_setup(storage: str | None) -> list[str]:
    """A DuckDB secret for an ``s3://`` storage, from the ``AWS_*`` variables.

    None without ``AWS_ACCESS_KEY_ID``: DuckDB then uses a secret of the
    writer's own, if any.
    """
    env = os.environ
    if not storage or not storage.startswith("s3://") or not env.get("AWS_ACCESS_KEY_ID"):
        return []

    def quoted(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    endpoint = urlparse(env.get("AWS_ENDPOINT_URL_S3") or env.get("AWS_ENDPOINT_URL") or "")
    options = [
        f"KEY_ID {quoted(env['AWS_ACCESS_KEY_ID'])}",
        f"SECRET {quoted(env.get('AWS_SECRET_ACCESS_KEY', ''))}",
        *([f"SESSION_TOKEN {quoted(env['AWS_SESSION_TOKEN'])}"] if env.get("AWS_SESSION_TOKEN") else []),
        f"REGION {quoted(env.get('AWS_REGION') or env.get('AWS_DEFAULT_REGION') or 'auto')}",
        *(
            [f"ENDPOINT {quoted(endpoint.netloc)}", "URL_STYLE 'path'", f"USE_SSL {str(endpoint.scheme != 'http').lower()}"]
            if endpoint.netloc
            else []
        ),
    ]
    return [f"CREATE SECRET fdl_storage (TYPE s3, {', '.join(options)}, SCOPE {quoted(storage)})"]


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
    store = _store(remote)
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
    store = _store(remote)
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
    store = _store(remote)
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
    store = _store(remote)
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
    for merged in version.parents[1:]:
        typer.echo(f"merged    {merged}")
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
    """What the working catalog changed since it was checked out, or since the commit not pushed yet."""
    state = _read_state(working)
    store = _store(state["remote"])
    pending_id = (state.get("pending") or {}).get("id")
    try:
        base = repo.read_version(store, state["base"]) if state["base"] else None
        pending = (
            parse_version(
                (_pending_dir(working) / pending_id / layout.VERSION_RECORD).read_bytes(),
                expected_id=pending_id,
            )
            if pending_id
            else None
        )
        result = diffs.working(working, pending or base)
    except (*ERRORS, catalog.CatalogError, OSError) as exc:
        raise _fail(exc) from exc
    if as_json:
        typer.echo(json.dumps({"pending": pending_id, **result}, ensure_ascii=False, indent=2))
        return
    typer.echo(f"On {state['ref']} of {state['remote']}, from " + (f"{base.id} (version {base.seq})" if base else "no version yet"))
    if pending:
        typer.echo(f"Committed, not pushed: {pending.id} (version {pending.seq}); fdl push {working}")
    elif state.get("forward"):
        typer.echo(f"Fast-forwarded, not pushed: fdl push {working} moves {state['ref']} there")
    for line in diffs.lines(result) or ["Nothing to commit"]:
        typer.echo(line)


@app.command()
def branch(
    remote: str = Remote,
    name: str | None = typer.Argument(None, help="The branch, without refs/heads/. Omit to list the branches"),
    target: str = typer.Argument(layout.HEAD, help="A version ID, or the ref whose version to start at"),
    delete: bool = typer.Option(False, "-d", "--delete", help="Delete the branch"),
) -> None:
    """List branches, start a branch at a version, or delete a branch."""
    store = _store(remote)
    try:
        if name is None:
            for branch_name, version_id in repo.branches(store).items():
                typer.echo(f"{branch_name}  {version_id}")
            return
        if delete:
            repo.delete_branch(store, name)
            typer.echo(f"Deleted refs/heads/{name}")
            return
        version_id = target if is_id(target) else refs.resolve(store, target)[1]
        if version_id is None:
            raise repo.RepoError(f"{target} holds no version yet")
        repo.branch(store, name, version_id)
    except ERRORS as exc:
        raise _fail(exc) from exc
    typer.echo(f"refs/heads/{name} -> {version_id}")


@app.command()
def tag(
    remote: str = Remote,
    name: str | None = typer.Argument(None, help="The tag, without refs/tags/. Omit to list the tags"),
    target: str = typer.Argument(layout.HEAD, help="A version ID, or the ref whose version to tag"),
    delete: bool = typer.Option(False, "-d", "--delete", help="Delete the tag"),
) -> None:
    """List tags, tag a version, or delete a tag. Tags never move."""
    store = _store(remote)
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
def profile(
    target: str = typer.Argument(
        help="A working catalog (.sqlite), to profile the version committed there before pushing it; "
        "or a repository, to add the profiles a pushed version lacks"
    ),
    ref: str = typer.Option(layout.DEFAULT_BRANCH, help="With a repository: the branch or tag whose version to profile"),
) -> None:
    """Write a profile of each table of a version, measured from its data.

    Given a working catalog, it profiles the version committed there, for
    `fdl push` to upload with it: run it between commit and push. A table
    whose data files and columns are the parent's keeps the parent's profile.

    Given a repository, it profiles the tables of the version the ref holds
    that have no profile, and puts those profiles beside it: for a version made
    before fdl wrote profiles. Profiles already there are left as they are.
    """
    working = Path(target)
    if _state_path(working).is_file():
        _profile_pending(working)
        return
    store = _store(target)
    try:
        version = repo.readable(store, ref)
        put = repo.add_profiles(store, version, setup=_read_setup(store))
    except (*ERRORS, catalog.CatalogError) as exc:
        raise _fail(exc) from exc
    for relative in put:
        typer.echo(f"put {relative}")
    typer.echo(f"Profiled {len(put)} table(s) of {version.id} (version {version.seq}).")


def _profile_pending(working: Path) -> None:
    """Profile the version committed in ``working`` and not pushed, beside it."""
    state = _read_state(working)
    pending = state.get("pending")
    if not pending:
        raise _fail(repo.RepoError(f"no version is committed and not pushed: fdl commit {working} first"))
    store = _store(state["remote"])
    folder = _pending_dir(working) / pending["id"]
    try:
        version = parse_version((folder / layout.VERSION_RECORD).read_bytes(), expected_id=pending["id"])
        # The profiles are measured from the working catalog, so it must still hold what was committed.
        if catalog.last_snapshot(working) != version.ducklake.snapshot_id:
            raise repo.RepoError("the working catalog changed after the commit: commit again, then profile")
        parent = repo.read_version(store, version.parents[0]) if version.parents else None
        reading = _written(store, state) or repo.data_url(store)
        setup = _read_setup(store) + _storage_setup(state.get("storage"))
        made = repo.write_profiles(store, folder, parent, working, data_path=reading, setup=setup)
    except (*ERRORS, catalog.CatalogError, OSError) as exc:
        raise _fail(exc) from exc
    typer.echo(f"Profiled {made} table(s) of {version.id}; the others keep their parent's profile.")
    typer.echo(f"Push it with:\n  fdl push {working}")


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
    store = _store(remote)
    try:
        newest = repo.head(store, ref)
        expired = repo.prune(store, newest, keep=keep, since=since) if newest else []
    except ERRORS as exc:
        raise _fail(exc) from exc
    typer.echo(f"Expired {len(expired)} version(s).")


def _duration(value: str) -> dt.timedelta:
    """``7d``, ``24h`` or ``30m``."""
    match = re.fullmatch(r"(\d+)([dhm])", value.strip())
    if match is None:
        raise typer.BadParameter(f"expected a number and d, h or m, such as 7d: {value!r}")
    unit = {"d": "days", "h": "hours", "m": "minutes"}[match[2]]
    return dt.timedelta(**{unit: int(match[1])})


@app.command()
def gc(
    remote: str = Remote,
    grace: str = typer.Option("7d", help="Keep objects written more recently than this: 7d, 24h or 30m"),
    yes: bool = typer.Option(False, "--yes", help="Do it. Without this, only say what would be deleted"),
) -> None:
    """Delete data files and assets no live version reaches, once past the grace period.

    These are files of versions past retention, and files a working catalog
    wrote and never pushed. Run it after expire.
    """
    store = _store(remote)
    try:
        plan = repo.plan_collection(store, grace=_duration(grace), setup=_read_setup(store))
        for key in plan.deleted:
            typer.echo(f"delete file  {key}")
        if not yes:
            typer.echo(f"{len(plan.deleted)} file(s) would be deleted. Run again with --yes to delete them.")
            return
        deleted = repo.collect(store, plan=plan, setup=_read_setup(store))
    except (*ERRORS, catalog.CatalogError) as exc:
        raise _fail(exc) from exc
    typer.echo(f"Deleted {len(deleted)} file(s).")


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
    store = _store(remote)
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
