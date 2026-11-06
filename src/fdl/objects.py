"""Assets: files stored by their sha256 beside the data.

The rows of a table point at them through a column marked ``asset``, and a
version's `tree.json` names them by path (its README, say). They sit at
``assets/<sha256>`` at the root, are written once and shared by versions, as
Git LFS stores the files its pointers name.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from . import catalog, layout

SHA256 = re.compile(r"[0-9a-f]{64}")
MARKDOWN = "text/markdown"
#: What `fdl commit` puts in the tree, beside the description: a README and everything below docs/.
README = "README.md"
DOCS_DIR = "docs"


class ObjectError(ValueError):
    """A file that cannot be published or pointed at."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


#: Fixed here, not left to `mimetypes`: that reads the machine's own tables (and
#: the registry on Windows), and Python 3.10 knows neither .webp nor .avif.
MEDIA_TYPES = {
    ".md": MARKDOWN,
    ".markdown": MARKDOWN,
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".svg": "image/svg+xml",
}


def media_type(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in MEDIA_TYPES:
        return MEDIA_TYPES[suffix]
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def entry(root: Path, path: str) -> dict[str, Any]:
    """A line of a manifest for ``root/path``."""
    local = Path(root) / path
    if not local.is_file():
        raise ObjectError(f"{path} is not a file under {root}")
    return {
        "path": path,
        "sha256": sha256_of(local),
        "bytes": local.stat().st_size,
        "media_type": media_type(path),
    }


def check_path(path: Any) -> str:
    """``path`` if it is relative, separated by ``/``, with no empty, `.` or `..` part."""
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
        raise ObjectError(f"not a relative path: {path!r}")
    if any(part in ("", ".", "..") for part in path.split("/")):
        raise ObjectError(f"not a relative path: {path!r}")
    return path


def tree(root: Path) -> list[dict[str, Any]]:
    """The files `fdl commit` publishes beside the data: ``README.md`` and every file below ``docs/``.

    Each is an entry of `tree.json` (path, sha256, bytes), with its path from
    ``root``, the folder of the description. Hidden files and folders are
    left out. Links between them stay as written: a reader resolves them
    through the tree, as Git does.
    """
    root = Path(root)
    found = [entry(root, README)] if (root / README).is_file() else []
    if (root / DOCS_DIR).is_dir():
        found += [
            {**item, "path": f"{DOCS_DIR}/{item['path']}"} for item in manifest(root / DOCS_DIR)
        ]
    return [{key: item[key] for key in ("path", "sha256", "bytes")} for item in found]


def check_tree(entries: Any) -> None:
    """Refuse a `tree.json` the format does not take."""
    if not isinstance(entries, list):
        raise ObjectError("tree.json lists its files under entries")
    seen = set()
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "bytes"}:
            raise ObjectError("each entry of tree.json has path, sha256 and bytes")
        check_path(item["path"])
        if not SHA256.fullmatch(str(item["sha256"])):
            raise ObjectError(f"{item['path']}: sha256 has to be 64 lower-case hex digits")
        if item["path"] in seen:
            raise ObjectError(f"{item['path']} is in the tree twice")
        seen.add(item["path"])


def manifest(folder: Path) -> list[dict[str, Any]]:
    """Every file below ``folder`` (hidden ones left out), as entries, by path."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ObjectError(f"{folder} is not a folder")
    files = sorted(
        path
        for path in folder.rglob("*")
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(folder).parts)
    )
    return [entry(folder, path.relative_to(folder).as_posix()) for path in files]


class ByContent(Mapping[str, Path]):
    """Files by their sha256: those in ``known``, then those below ``folder``.

    The folder is hashed the first time a sha256 ``known`` lacks is looked up,
    so a push that finds everything in the repository reads none of it.
    """

    def __init__(self, known: Mapping[str, Path], folder: Path | None = None) -> None:
        self._known = dict(known)
        self._folder = folder
        self._found: dict[str, Path] | None = None

    def _below(self) -> dict[str, Path]:
        if self._found is None:
            self._found = (
                {}
                if self._folder is None
                else {item["sha256"]: self._folder / item["path"] for item in manifest(self._folder)}
            )
        return self._found

    def __getitem__(self, sha256: str) -> Path:
        if sha256 in self._known:
            return self._known[sha256]
        return self._below()[sha256]

    def __iter__(self) -> Iterator[str]:
        return iter({**self._known, **self._below()})

    def __len__(self) -> int:
        return len({**self._known, **self._below()})


def asset_columns(described: dict[str, Any]) -> list[tuple[str, str, str]]:
    """The columns `dataset.json` marks as ``asset``: (schema, table, column)."""
    return [
        (table["schema"], table["name"], field["name"])
        for table in described.get("tables", [])
        if table.get("kind") != "view"
        for field in table.get("fields", [])
        if field.get("asset") is True
    ]


def asset_values(
    working: Path, columns: Iterable[tuple[str, str, str]], *, data_path: str
) -> set[str]:
    """The sha256 the marked columns hold in the working catalog's last snapshot."""
    import duckdb

    columns = list(columns)
    if not columns:
        return set()

    def q(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    found: set[str] = set()
    con = duckdb.connect()
    try:
        for statement in catalog.attach_sql(Path(working), alias="lake", data_path=data_path, read_only=True):
            if statement:
                con.execute(statement)
        for schema, table, column in columns:
            rows = con.execute(
                f"SELECT DISTINCT {q(column)} FROM lake.{q(schema)}.{q(table)} "
                f"WHERE {q(column)} IS NOT NULL"
            ).fetchall()
            for (value,) in rows:
                if not isinstance(value, str) or not SHA256.fullmatch(value):
                    raise ObjectError(
                        f"{schema}.{table}.{column} is an asset column, but holds {value!r}: "
                        f"each value has to be a sha256 in 64 lower-case hex digits"
                    )
                found.add(value)
    except duckdb.Error as exc:
        # A data file the working catalog reads may be where DuckDB cannot reach
        # (a private repository over HTTP, say): an error, not a traceback.
        raise ObjectError(f"could not read the asset columns: {exc}") from exc
    finally:
        con.close()
    return found


def asset_key(sha256: str) -> str:
    return layout.ASSETS_DIR + sha256
