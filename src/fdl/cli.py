"""The ``fdl`` command: the operations in `fdl.repo`, on a terminal.

A working metadata file is a DuckLake metadata file in SQLite that you write to with any
DuckDB client. Beside it sits the folder ``<working>.fdl/``, as ``.git/`` sits
in a Git working tree: ``state.json`` remembers the dataset it came from and
the version it was cloned at, so a commit knows what it builds on; ``data/``
holds the new data files until they are pushed, for a dataset DuckDB cannot
write to; and a commit waits in ``pending/`` until it is pushed.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import typer

from . import catalog, config, layout, objects, profiles, refs, repo
from . import declared as declaring
from . import merge as merging
from . import diff as diffs
from .stores import S3Store, StoreError, open_store
from .version import VersionError, is_id
from .version import parse as parse_version

app = typer.Typer(no_args_is_help=True, pretty_exceptions_short=True)

Remote = typer.Argument(help="The dataset: s3://bucket/prefix/, https://host/path/ or a folder")
Working = typer.Argument(help="The working metadata (.sqlite)")


#: What `fdl clone` names the folder it keeps beside the working metadata after.
FOLDER_SUFFIX = ".fdl"


def _folder(working: Path) -> Path:
    """The folder fdl keeps beside the working metadata."""
    return working.with_name(working.name + FOLDER_SUFFIX)


def _state_path(working: Path) -> Path:
    return _folder(working) / "state.json"


def _read_state(working: Path) -> dict:
    path = _state_path(working)
    if not path.is_file():
        if working.with_name(working.name + ".fdl.json").is_file():
            # An earlier fdl kept the state in <working>.fdl.json, a commit not pushed yet
            # in <working>.pending/ and new data files in <working>.data/.
            raise typer.BadParameter(
                f"{working} was cloned by an earlier fdl, which kept its state in {working.name}.fdl.json: "
                f"push it with that fdl, or clone again"
            )
        raise typer.BadParameter(f"{working} was not made by `fdl clone`")
    return json.loads(path.read_text())


def _write_state(working: Path, state: dict) -> None:
    path = _state_path(working)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def _fail(exc: Exception) -> typer.Exit:
    typer.echo(f"error: {exc}", err=True)
    return typer.Exit(1)


ERRORS = (
    config.ConfigError,
    repo.RepoError,
    refs.RefError,
    layout.LayoutError,
    VersionError,
    StoreError,
    declaring.DeclarationError,
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
    """Start a dataset (fdl.json and HEAD). Harmless on one that exists."""
    try:
        config = repo.init(_store(remote), data_dir=data_dir)
    except ERRORS as exc:
        raise _fail(exc) from exc
    typer.echo(f"fdl {config.fdl} dataset, data in {config.data_dir}")


def _default_working(remote: str) -> Path:
    """Where `fdl clone` puts the working metadata when not told: <name>.sqlite, as git names the folder."""
    if "://" in remote:
        name = remote.rstrip("/").rsplit("/", 1)[-1]
    else:
        # A folder given as `.` or `..` is named by where it is.
        name = Path(remote).resolve().name
    return Path(f"{name or 'lake'}.sqlite")


@app.command()
def clone(
    remote: str = Remote,
    working: Path | None = typer.Argument(
        None, help="The working metadata (.sqlite). Default: <the dataset's last path segment>.sqlite here"
    ),
    branch: str = typer.Option(layout.DEFAULT_BRANCH.removeprefix("refs/heads/"), "--branch", "-b", help="The branch to build on: a name such as main, or refs/heads/<name>"),
    storage: str | None = typer.Option(
        None,
        help="The dataset's root in its object storage (s3://…), to write new data files there directly instead of uploading them on commit",
    ),
) -> None:
    """Make a working metadata file from the version a branch holds."""
    ref = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
    working = working or _default_working(remote)
    store = _store(remote)
    # A dataset over HTTP takes no writes from DuckDB: new data files go to
    # a folder beside the working metadata until the commit uploads them, or
    # straight to the dataset's storage when the writer has keys to it.
    local = (
        None
        if storage or repo.writes_data_files(store)
        else _folder(working) / "data"
    )
    try:
        # Read before the clone: a data_dir moved meanwhile then fails the commit.
        config = repo.read_config(store)
        version = repo.clone(store, working, ref=ref, local_data=local, storage=storage)
        if storage:
            data = repo.storage_data_path(storage, config)
        else:
            data = repo.data_url(store) if local is None else local.resolve().as_posix() + "/"
        # What the version declares; a commit takes it over, with what fdl declare changes.
        facts = repo.declarations(store, version) if version else declaring.Facts()
    except ERRORS as exc:
        raise _fail(exc) from exc
    state = {
        "remote": remote,
        "ref": ref,
        "base": version.id if version else None,
        "data_dir": config.data_dir,
        "declared": facts.to_json(),
    }
    if local:
        state["data"] = str(local)
    if storage:
        state["storage"] = storage
    _write_state(working, state)
    typer.echo(f"{working} at {version.id if version else 'empty metadata'}")
    typer.echo("Write to it with:")
    typer.echo("  " + catalog.attach_sql(working, alias="lake", data_path=data)[-1] + ";")


@app.command("config")
def config_command(
    key: str = typer.Argument(help="author.name or author.email"),
    value: str | None = typer.Argument(None, help="The value to set. Without it, print the one set"),
) -> None:
    """Set or print who makes the versions you commit, kept in ~/.config/fdl/config.toml.

    FDL_AUTHOR_NAME and FDL_AUTHOR_EMAIL override them, and --author overrides both.
    """
    try:
        if value is None:
            typer.echo(config.get(key) or "")
        else:
            config.set_value(key, value)
    except config.ConfigError as exc:
        raise _fail(exc) from exc

@app.command()
def commit(
    working: Path = Working,
    message: str = typer.Option(..., "-m", "--message", help="What changed"),
    author: str | None = typer.Option(None, help="Who made it (default: fdl config author.name)"),
    allow_empty: bool = typer.Option(
        False, "--allow-empty", help="Make a version even if nothing changed since the last one"
    ),
    assets_dir: Path | None = typer.Option(
        None,
        "--assets",
        exists=True,
        file_okay=False,
        help="A folder with the files the asset columns point at, to upload those the dataset lacks",
    ),
) -> None:
    """Freeze the working metadata into a version, kept beside it until you push it.

    The declarations (fdl declare) are checked against the tables and views
    first. Nothing is made when the tables, their rows, the views and the
    declarations are what the last pushed version holds. Committing again before pushing replaces the version not
    pushed yet.
    """
    state = _read_state(working)
    store = _store(state["remote"])
    try:
        version = _commit(
            store,
            working,
            state,
            message=message,
            author=config.author(author),
            assets_dir=assets_dir,
            allow_empty=allow_empty,
        )
    except repo.NothingToCommit as exc:
        typer.echo(f"Nothing to commit: {exc}")
        return
    except ERRORS as exc:
        raise _fail(exc) from exc
    typer.echo(f"Committed {version.id} (version {version.seq}). Push it to {state['ref']} with:")
    typer.echo(f"  fdl push {working}")


def _pending_dir(working: Path) -> Path:
    """Where the committed version not pushed yet waits, as ``<ID>/``."""
    return _folder(working) / "pending"


def _commit(
    store,
    working: Path,
    state: dict,
    *,
    message: str,
    author: dict,
    assets_dir: Path | None,
    allow_empty: bool,
    facts: declaring.Facts | None = None,
    merged=None,
):
    """Freeze the working metadata into a version beside it, to push later.

    Its parent is the version the working metadata was cloned at (or last
    pushed). Any version committed before and not pushed is replaced. ``facts``
    are the declarations to commit instead of those kept beside the working
    metadata (a revert's or a merge's); they are kept there after.
    """
    _check_data_dir(store, working, state)
    out = Path(tempfile.mkdtemp(prefix="commit-", dir=_folder(working).resolve()))
    try:
        parent = repo.read_version(store, state["base"]) if state["base"] else None
        facts = facts if facts is not None else _declared(store, state, working)
        inherited = repo.declarations(store, parent) if parent else None
        written = _written(store, state)
        reading, setup = _reading(store, state)

        def make(allow_empty: bool):
            return repo.commit(
                working,
                out,
                parent=parent,
                data_path=repo.data_url(store),
                author=author,
                message=message,
                allow_empty=allow_empty,
                working_data=written,
                setup=setup,
                facts=facts,
                inherited=inherited,
                merged=merged,
            )

        try:
            version = make(allow_empty)
        except repo.CannotCompare as exc:
            typer.echo(f"warning: could not tell whether anything changed ({exc}); committing", err=True)
            version = make(True)
        index = json.loads((out / version.id / layout.TABLES).read_text(encoding="utf-8"))
        held = objects.asset_values(
            working, objects.indexed_asset_columns(index), data_path=reading, setup=setup
        )
        pending = _pending_dir(working)
        shutil.rmtree(pending, ignore_errors=True)
        out.rename(pending)
    except repo.NothingToCommit:
        # The working metadata holds what the last pushed version holds, so a
        # version committed before and not pushed no longer matches it.
        # It also declares what the last pushed version declares.
        _drop_pending(working, state, declared=(inherited or declaring.Facts()).to_json())
        raise
    finally:
        shutil.rmtree(out, ignore_errors=True)
    _write_state(
        working,
        {
            **state,
            # What the version declares: a table dropped took its declarations with it,
            # so a table made again under its name does not get them back.
            "declared": declaring.from_index(index).to_json(),
            "pending": {
                "id": version.id,
                "assets": sorted(held),
                "assets_dir": str(assets_dir.resolve()) if assets_dir else None,
            },
        },
    )
    return version


def _declared(store, state: dict, working: Path) -> declaring.Facts:
    """The declarations kept beside the working metadata.

    A working metadata cloned before they were kept there declares what its base declares.
    """
    if "declared" in state:
        return declaring.Facts.from_json(state["declared"])
    return repo.declarations(store, repo.read_version(store, state["base"])) if state.get("base") else declaring.Facts()


def _check_data_dir(store, working: Path, state: dict) -> None:
    """Refuse a working metadata cloned before fdl.json moved the data files.

    Its new data files went to the old place, where no version reads them.
    """
    now = repo.read_config(store).data_dir
    if state.get("data_dir") != now:
        raise repo.RepoError(
            f"data_dir in fdl.json is now {now!r}, but {working} was cloned with "
            f"{state.get('data_dir')!r}: its new data files are not where versions read them. "
            "Clone again and rebuild"
        )


def _drop_pending(working: Path, state: dict, **changes) -> None:
    """Forget the version committed and not pushed, and delete it."""
    _write_state(working, {key: value for key, value in state.items() if key != "pending"} | changes)
    shutil.rmtree(_pending_dir(working), ignore_errors=True)


def _written(store, state: dict) -> str | None:
    """Where the working metadata writes its new data files, when not where it records."""
    if state.get("storage"):
        return repo.storage_data_path(state["storage"], repo.read_config(store))
    if state.get("data"):
        return Path(state["data"]).resolve().as_posix() + "/"
    return None


def _reading(store, state: dict) -> tuple[str, list[str]]:
    """Where the working metadata's data files are read from, and the setup to read them."""
    return _written(store, state) or repo.data_url(store), _read_setup(store) + _storage_setup(state.get("storage"))


@app.command()
def push(
    working: Path = Working,
    no_profile: bool = typer.Option(
        False, "--no-profile", help="Upload the version without profiling its tables"
    ),
) -> None:
    """Upload the committed version and move the branch to it.

    First it profiles the version's tables and views (a summary of each,
    measured from the data), reading only the tables whose data files or
    columns changed since the parent, and the views when any table or view
    changed, and uploads the profiles with the version.

    Refused when someone else moved the branch since the clone: clone
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
        _check_data_dir(store, working, state)
        version = parse_version((folder / layout.VERSION_RECORD).read_bytes(), expected_id=pending["id"])
        if not no_profile:
            _profile_pending(store, working, state, folder, version)
        assets_dir = pending.get("assets_dir")
        repo.push(
            store,
            folder,
            version,
            ref=state["ref"],
            local_data=Path(state["data"]) if state.get("data") else None,
            assets=pending["assets"],
            local_objects=objects.ByContent(Path(assets_dir) if assets_dir else None),
        )
    except (*ERRORS, OSError) as exc:
        raise _fail(exc) from exc
    # The version is pushed: record it before anything else can fail.
    _drop_pending(working, {key: value for key, value in state.items() if key != "forward"}, base=version.id)
    if state.get("data"):
        _drop_uploaded(store, working, Path(state["data"]), version)
    typer.echo(f"{state['ref']} -> {version.id} (version {version.seq})")


def _push_forward(working: Path, state: dict) -> None:
    """Move the branch to the version a fast-forward merge rebuilt the working metadata from."""
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
    author: str | None = typer.Option(None, help="Who made it (default: fdl config author.name)"),
) -> None:
    """Commit a new version that holds what an earlier version held.

    The working metadata is written back to the earlier version's tables, and
    the new version builds on the one it was cloned at, so the branch
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
        # By first parent: a revert brings back an earlier version of this branch.
        if base is None or not repo.in_history(store, target.id, base):
            raise repo.RepoError(f"{target.id} is not in the first-parent history of {base.id if base else 'the working metadata'}")
        reading, setup = _reading(store, state)
        # Read from the version's own metadata: a frozen metadata holds only its last snapshot.
        with tempfile.TemporaryDirectory(prefix="fdl-revert-") as tmp:
            frozen = Path(tmp) / "target.duckdb"
            frozen.write_bytes(store.get(layout.version_metadata(target)))
            repo.check_catalog(frozen, target)
            # What the earlier version declared comes back with its tables.
            facts = repo.declarations(store, target)
            catalog.restore(
                working, frozen, data_path=reading, dataset_data=repo.data_url(store), setup=setup
            )
        version = _commit(
            store,
            working,
            state,
            message=message or f"Revert to version {target.seq}",
            author=config.author(author),
            assets_dir=None,
            allow_empty=False,
            facts=facts,
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
    author: str | None = typer.Option(None, help="Who made it (default: fdl config author.name)"),
) -> None:
    """Merge another branch into the working metadata's branch.

    Tables and rows only the other branch changed come over. A table both
    branches changed is merged cell by cell, by the primary key both declare
    for it; anything both changed differently is a conflict, and then nothing
    is changed. The merge version waits beside the working
    metadata until you push it.
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
            raise repo.RepoError(f"the working metadata has changes not committed: commit and push them, or clone again")
        if _declared(store, state, working) != repo.declarations(store, ours):
            raise repo.RepoError("the declarations have changes not committed (fdl declare): commit and push them, or clone again")
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
        version, plan = _merge(store, working, state, ours, theirs, base, message=message or f"Merge {theirs_name}", author=config.author(author))
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
    for slot in plan.comments:
        typer.echo(f"warning: both sides changed the comment of {slot} differently; ours is kept", err=True)
    typer.echo(f"Committed {version.id} (version {version.seq}), merging {theirs.id}. Push it with:")
    typer.echo(f"  fdl push {working}")


def _fast_forward(store, working: Path, state: dict, theirs) -> None:
    """Rebuild the working metadata from ``theirs``, for push to move the branch there."""
    local = Path(state["data"]) if state.get("data") else None
    repo.clone(store, working, local_data=local, version=theirs, storage=state.get("storage"))
    facts = repo.declarations(store, theirs)
    _write_state(working, {**state, "base": theirs.id, "forward": True, "declared": facts.to_json()})
    typer.echo(f"Fast-forward to {theirs.id} (version {theirs.seq}). Move {state['ref']} there with:")
    typer.echo(f"  fdl push {working}")


def _merge(store, working: Path, state: dict, ours, theirs, base, *, message: str, author: dict):
    """Write the merge into the working metadata and commit it, or raise `merging.Conflicts`."""
    with tempfile.TemporaryDirectory(prefix="fdl-merge-") as tmp:
        frozen = {}
        for name, version in (("base", base), ("theirs", theirs)):
            frozen[name] = Path(tmp) / f"{name}.duckdb"
            frozen[name].write_bytes(store.get(layout.version_metadata(version)))
            repo.check_catalog(frozen[name], version)
        said = {
            "base": repo.declarations(store, base),
            "ours": repo.declarations(store, ours),
            "theirs": repo.declarations(store, theirs),
        }
        held = {"base": repo.tables_of(store, base), "ours": repo.tables_of(store, ours), "theirs": repo.tables_of(store, theirs)}
        # What one side changed of the declarations since the base comes over.
        facts, found = declaring.merged(said["base"], said["ours"], said["theirs"])
        conflicts = [f"declarations: {item}" for item in found]
        plan = merging.tables(
            working,
            frozen["base"],
            frozen["theirs"],
            keys=merging.primary_keys(*((said[side], held[side]) for side in ("base", "ours", "theirs"))),
            data_path=_written(store, state),
            dataset_data=repo.data_url(store),
            setup=_read_setup(store) + _storage_setup(state.get("storage")),
            # Written only when a version can be committed from it.
            write=not conflicts,
        )
    conflicts += plan.conflicts
    if conflicts:
        raise merging.Conflicts(conflicts)
    try:
        version = _commit(
            store,
            working,
            state,
            message=message,
            author=author,
            assets_dir=None,
            allow_empty=True,
            facts=facts,
            merged=theirs,
        )
    except declaring.DeclarationError as exc:
        # The merge holds what the declarations refuse: one side dropped a column the other made a key, say.
        repo.clone(store, working, local_data=Path(state["data"]) if state.get("data") else None, version=ours, storage=state.get("storage"))
        raise merging.Conflicts([f"declarations: {problem}" for problem in exc.problems]) from exc
    return version, plan


def _drop_uploaded(store, working: Path, local: Path, version) -> None:
    """Free the disk the uploaded data files took.

    The working metadata is made again from the version just pushed, so it
    reads those files from the dataset, and the local copies go. The
    conversion replaces the working metadata only once it is complete, so on
    failure the working metadata and its local files stay as they were.
    """
    try:
        repo.clone(store, working, local_data=local, version=version)
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
    """What DuckDB needs to read the dataset's data files: the token over HTTP, the ``AWS_*`` keys on S3."""
    if isinstance(store, S3Store):
        return _storage_setup(store.url)
    token = getattr(store, "token", None)
    if not token:
        return []
    scope = store.url.replace("'", "''")
    secret = token.replace("'", "''")
    return [f"CREATE SECRET fdl_remote (TYPE http, BEARER_TOKEN '{secret}', SCOPE '{scope}')"]


declare_app = typer.Typer(no_args_is_help=True)
app.add_typer(
    declare_app,
    name="declare",
    help="Set what the next commit declares of the tables and views: primary keys, asset columns, "
    "profiles and the other datasets the views read. A commit starts from its parent's declarations.",
)

Table = typer.Argument(help="The table or view, as schema.name")


def _change_declarations(working: Path, change) -> None:
    """Apply ``change`` to the declarations kept beside the working metadata, refusing
    what the tables and views refuse that they did not refuse before."""
    state = _read_state(working)
    store = _store(state["remote"])
    try:
        before = _declared(store, state, working)
        after = change(before)
        reading, setup = _reading(store, state)
        found = declaring.found_in(working, data_path=reading, setup=setup)
        old = set(declaring.check(before, found))
        problems = [p for p in declaring.check(after, found) if p not in old]
        if problems:
            raise declaring.DeclarationError(problems)
    except (*ERRORS, catalog.CatalogError) as exc:
        raise _fail(exc) from exc
    _write_state(working, {**state, "declared": after.to_json()})


def _with(found: dict, name: str, value) -> dict:
    return {**{k: v for k, v in found.items() if k != name}, **({name: value} if value else {})}


@declare_app.command("list")
def declare_list(
    working: Path = Working,
    as_json: bool = typer.Option(False, "--json", help="Print them as JSON"),
) -> None:
    """What the next commit declares, one declaration a line."""
    state = _read_state(working)
    store = _store(state["remote"])
    try:
        facts = _declared(store, state, working)
    except ERRORS as exc:
        raise _fail(exc) from exc
    if as_json:
        typer.echo(json.dumps(facts.to_json(), ensure_ascii=False, indent=2))
        return
    for name, columns in sorted(facts.primary_keys.items()):
        typer.echo(f"{name} primary_key {' '.join(columns)}")
    for name, columns in sorted(facts.assets.items()):
        typer.echo(f"{name} assets {' '.join(columns)}")
    for name in sorted(facts.unprofiled):
        typer.echo(f"{name} profile false")
    for name, entry in sorted(facts.dependencies.items()):
        typer.echo(f"dependency {name} {entry['url']}" + (f" {entry['version']}" if entry.get("version") else ""))


@declare_app.command("primary-key")
def declare_primary_key(
    working: Path = Working,
    name: str = Table,
    columns: list[str] = typer.Argument(None, help="The columns that identify a row. None removes the primary key"),
) -> None:
    """Declare the columns that identify a row of a table, used to merge it cell by cell."""
    _change_declarations(
        working, lambda f: dataclasses.replace(f, primary_keys=_with(f.primary_keys, name, list(columns or [])))
    )


@declare_app.command("assets")
def declare_assets(
    working: Path = Working,
    name: str = Table,
    columns: list[str] = typer.Argument(None, help="The VARCHAR columns that hold a file's sha256. None removes them"),
) -> None:
    """Declare the columns of a table that point at files by sha256 (see fdl assets)."""
    _change_declarations(working, lambda f: dataclasses.replace(f, assets=_with(f.assets, name, list(columns or []))))


@declare_app.command("profile")
def declare_profile(
    working: Path = Working,
    name: str = Table,
    profiled: bool = typer.Argument(..., help="false: make no profile for it"),
) -> None:
    """Declare whether a push profiles a table or view."""
    _change_declarations(
        working,
        lambda f: dataclasses.replace(f, unprofiled=f.unprofiled - {name} if profiled else f.unprofiled | {name}),
    )


@declare_app.command("dependency")
def declare_dependency(
    working: Path = Working,
    name: str = typer.Argument(help="The name the views' SQL reads the other dataset under"),
    url: str | None = typer.Argument(None, help="The URL of its root, ending in /. Without it, the dependency is removed"),
    version: str | None = typer.Option(None, "--version", help="The version to read. Without it, the one main holds"),
) -> None:
    """Declare another dataset the views read (a dependency)."""
    if version and not url:
        raise _fail(declaring.DeclarationError(["--version needs the url"]))
    entry = {"url": url, **({"version": version} if version else {})} if url else None
    _change_declarations(working, lambda f: dataclasses.replace(f, dependencies=_with(f.dependencies, name, entry)))


@app.command()
def assets(folder: Path = typer.Argument(..., help="The folder of files the rows will point at")) -> None:
    """List the files below a folder as CSV: path, sha256, bytes, media_type.

    Read it into a table (DuckDB's read_csv, say) and declare its sha256 column
    an asset column with `fdl declare assets`. `fdl commit --assets <folder>`
    then uploads the files the dataset lacks.
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
    """Where the metadata of the version a ref holds is, to ATTACH."""
    store = _store(remote)
    try:
        version = repo.readable(store, ref)
        where = store.url.rstrip("/") + "/" + layout.version_metadata(version)
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
    """What changed in a version: tables, rows, columns and data files.

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
        listed = None if expired else _tables_of(store, version)
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
    if listed:
        typer.echo("")
        for name, rows in listed:
            shown = f"{rows:,} row{'' if rows == 1 else 's'}" if isinstance(rows, int) else rows
            typer.echo(f"  {name}  {shown}")
    if result is not None:
        typer.echo("")
        for line in diffs.lines(result) or ["No changes from the parent"]:
            typer.echo(line)


def _tables_of(store, version) -> list[tuple[str, int | str]]:
    """Each table with its rows, and each view, of a version, from its `tables.json`."""
    index = diffs.indexed(store, version) or {}
    tables = [
        (name, sum(f["rows"] - f.get("deletes", {}).get("rows", 0) for f in table.get("files", [])))
        for name, table in index.get("tables", {}).items()
    ]
    return sorted(tables) + [(name, "view") for name in sorted(index.get("views", {}))]


@app.command()
def status(
    working: Path = Working,
    as_json: bool = typer.Option(False, "--json", help="Print the differences as JSON"),
) -> None:
    """What the working metadata changed since it was cloned, or since the commit not pushed yet."""
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
        if pending:
            committed = declaring.from_index(json.loads((_pending_dir(working) / pending_id / layout.TABLES).read_text()))
        else:
            committed = repo.declarations(store, base) if base else declaring.Facts()
        now = _declared(store, state, working)
        named = {*committed.primary_keys, *committed.assets, *committed.unprofiled, *now.primary_keys, *now.assets, *now.unprofiled}
        result["declarations"] = declaring.changed(committed, now, named)
        result["dependencies"] = declaring.dependencies_changed(committed, now)
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
    remote: str = Remote,
    ref: str = typer.Option(layout.DEFAULT_BRANCH, help="The branch or tag whose version to profile"),
) -> None:
    """Profile the tables of a pushed version that have no profile.

    `fdl push` profiles the version it uploads. This is for a version pushed
    without them: made before fdl wrote profiles, pushed with --no-profile,
    or whose tables could not be read then. Profiles already there are left
    as they are.
    """
    store = _store(remote)
    try:
        version = repo.readable(store, ref)
        put, unread = repo.add_profiles(store, version, setup=_read_setup(store))
    except (*ERRORS, catalog.CatalogError) as exc:
        raise _fail(exc) from exc
    for relative in put:
        typer.echo(f"put {relative}")
    _warn_unread(unread)
    typer.echo(f"Profiled {len(put)} table(s) of {version.id} (version {version.seq}).")


def _warn_unread(tables: list[str]) -> None:
    if tables:
        typer.echo(f"warning: tables were not profiled, as they could not be read: {', '.join(tables)}", err=True)


def _profile_pending(store, working: Path, state: dict, folder: Path, version) -> None:
    """Profile the version committed in ``working``, beside it, for the push to upload.

    A profile is never worth a failed push: when the working metadata no longer
    holds what was committed, or the tables cannot be read, it warns and the
    version goes without them (`fdl profile <remote>` adds them later).
    """
    def unprofiled(wanted: list[dict]) -> list[str]:
        return [
            f"{table['schema']}.{table['name']}"
            for table in wanted
            if not (folder / profiles.path(table["schema"], table["name"])).is_file()
        ]

    try:
        reading, setup = _reading(store, state)
        index = json.loads((folder / layout.TABLES).read_text(encoding="utf-8"))
        wanted = profiles.indexed(index, declaring.from_index(index).unprofiled)
        if not unprofiled(wanted):
            return  # no tables, or all profiled by a push that stopped before it finished
        if catalog.last_snapshot(working) != version.ducklake.snapshot_id:
            typer.echo(
                "warning: tables were not profiled, as the working metadata changed after the commit; "
                f"commit again, or run fdl profile {state['remote']} after the push",
                err=True,
            )
            return
        parent = repo.read_version(store, version.parents[0]) if version.parents else None
        repo.write_profiles(store, folder, parent, working, data_path=reading, setup=setup)
    except Exception as exc:  # noqa: BLE001 - a profile is never worth a failed push
        typer.echo(f"warning: tables were not profiled ({exc})", err=True)
        return
    _warn_unread(unprofiled(wanted))


@app.command()
def expire(
    remote: str = Remote,
    keep: int = typer.Option(1, help="How many of the newest versions to keep on each branch"),
    since: str | None = typer.Option(None, help="Also keep those made at or after this time (ISO 8601)"),
    grace: str = typer.Option("7d", help="Also keep those made more recently than this: 7d, 24h or 30m"),
    by: str | None = typer.Option(None, help="Who or what takes them out, recorded in expired.json"),
    message: str | None = typer.Option(None, "-m", "--message", help="Why they are taken out, recorded in expired.json"),
) -> None:
    """Take older versions out of retention: mark them and delete their metadata.

    Kept are the newest on each branch, the versions refs point at and their
    merge bases with main. Data files are left in place; `fdl gc` deletes them.
    """
    store = _store(remote)
    try:
        expired = repo.prune(store, keep=keep, since=since, grace=_duration(grace), by=by, message=message)
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

    These are files of versions past retention, and files a working metadata file
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


def _withdrawn_objects(path: Path) -> dict[str, list[str]]:
    """The names `--objects` gives: `deleted` of an expired.json, or that object itself."""
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise repo.RepoError(f"{path}: {exc}") from exc
    named = raw.get("deleted", raw) if isinstance(raw, dict) else None
    if not isinstance(named, dict) or not any(kind in named for kind in repo.DELETED_KINDS):
        raise repo.RepoError(f"{path} names no objects: give `deleted` of an expired.json, or that file")
    for kind in repo.DELETED_KINDS:
        names = named.get(kind, [])
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise repo.RepoError(f"{path}: `{kind}` is not a list of names")
    return {kind: named.get(kind, []) for kind in repo.DELETED_KINDS}


@app.command()
def withdraw(
    remote: str = Remote,
    versions: list[str] = typer.Argument(None, help="The versions that hold the data"),
    objects: Path | None = typer.Option(
        None,
        "--objects",
        help="A JSON file naming objects another dataset withdrew, as `deleted` of its expired.json (or that file itself). Every version that reaches one is withdrawn too",
    ),
    yes: bool = typer.Option(False, "--yes", help="Do it. Without this, only say what would be done"),
    by: str | None = typer.Option(None, help="Who withdraws them, recorded in expired.json"),
    message: str | None = typer.Option(None, "-m", "--message", help="Why they are withdrawn, recorded in expired.json"),
    as_json: bool = typer.Option(False, "--json", help="Print the plan as JSON, listing the refs that block it instead of failing"),
) -> None:
    """Remove versions that hold data that must not be published.

    Move every branch to a version without the data first. This deletes the
    tags that point at the versions, the data files and assets no kept version
    reaches, and the versions' metadata files, at once. Their version.json stays. A
    version already past retention has no metadata, so then every data file and
    asset no live version reaches is deleted.

    With --objects, a mirror applies a withdrawal made in the
    dataset it was copied from.
    """
    store = _store(remote)
    try:
        named = None
        if objects is not None:
            named = _withdrawn_objects(objects)
        if not versions and named is None:
            raise repo.RepoError("Name the versions, or the objects with --objects")
        plan = repo.plan_withdrawal(
            store, versions or [], setup=_read_setup(store), objects=named, refuse_refs=not as_json
        )
        if as_json:
            typer.echo(
                json.dumps(
                    {
                        "versions": plan.versions,
                        "tags": plan.tags,
                        "deleted": plan.deleted,
                        "still_read": plan.still_read,
                        "blocked": plan.blocked,
                    }
                )
            )
        else:
            for name in plan.tags:
                typer.echo(f"delete tag   {name}")
            for key in plan.deleted:
                typer.echo(f"delete file  {key}")
            for version_id in plan.versions:
                typer.echo(f"withdraw     {version_id}")
            for key in plan.still_read:
                typer.echo(f"keep file    {key} (a kept version reaches it)")
        if not yes:
            if not as_json:
                typer.echo("Nothing was changed. Run again with --yes to do this.")
            return
        repo.withdraw(store, plan.versions, plan=plan, by=by, message=message)
    except ERRORS as exc:
        raise _fail(exc) from exc
    if not as_json:
        typer.echo(f"Withdrew {len(plan.versions)} version(s) and deleted {len(plan.deleted)} file(s).")


def main() -> None:
    app()
