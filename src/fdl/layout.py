"""Where each file of a repository sits, relative to its root.

The same names are used on the remote and in a local `.fdl/`, so syncing the
two is copying files.
"""

from __future__ import annotations

import re

#: The version of the format this package writes and reads.
FDL_VERSION = "0.1"

CONFIG = "fdl.json"
HEAD = "HEAD"
#: The list of refs, for readers that cannot list the store.
REFS_INDEX = "refs.json"
DEFAULT_BRANCH = "refs/heads/main"
DEFAULT_DATA_DIR = "data/"

VERSION_RECORD = "version.json"
CATALOG = "ducklake.duckdb"
#: What the version holds, described (see `fdl.dataset`).
DATASET = "dataset.json"
#: The index of each table's files, made from the catalog (optional).
FILES = "files.json"
EXPIRED = "expired.json"
#: Other formats of the same catalog, under the version (optional).
CATALOGS_DIR = "catalogs/"
#: The other files of a version (its README, say), by path to the asset that holds each (optional).
TREE = "tree.json"
#: A summary of each table, made from the data, under the version (optional).
PROFILES_DIR = "profiles/"
#: Files the rows of a table point at, by sha256 (shared).
ASSETS_DIR = "assets/"

#: What a symbolic ref starts with. HEAD is the only one.
SYMBOLIC = "ref: "

_REF_PART = re.compile(r"[a-z0-9][a-z0-9._-]*")
_REF_KINDS = ("heads", "tags", "requests")
#: The kinds `refs.json` lists. Requests are the host's own.
LISTED_REFS = ("refs/heads/", "refs/tags/")
_REF_MAX = 128

#: A version ID: a lower-case UUIDv7.
_VERSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")

#: The names of shared objects (see `is_shared_object`).
_DATA_FILE = re.compile(r"ducklake-[^/]+\.parquet")
_ASSET = re.compile(r"assets/[0-9a-f]{64}")


class LayoutError(ValueError):
    """A name that this format does not allow."""


def check_ref(name: str) -> str:
    """Return ``name`` if it is a full ref name (``refs/heads/main``), or refuse."""
    parts = name.split("/")
    if len(parts) < 3 or parts[0] != "refs" or parts[1] not in _REF_KINDS:
        raise LayoutError(f"not a ref: {name!r} (refs/heads/<name>, refs/tags/<name>)")
    short = "/".join(parts[2:])
    if len(short) > _REF_MAX or not all(_REF_PART.fullmatch(p) for p in parts[2:]):
        raise LayoutError(
            f"not a ref name: {short!r} (lower case letters, digits and ._-, "
            f"up to {_REF_MAX} characters)"
        )
    return name


def is_version_id(value: object) -> bool:
    return isinstance(value, str) and bool(_VERSION_ID.fullmatch(value))


def check_version_id(value: object) -> str:
    """Return ``value`` if it is a version ID, or refuse. The ID becomes part of a path."""
    if not is_version_id(value):
        raise LayoutError(f"not a version ID: {value!r}")
    return value


def check_file_name(value: object) -> str:
    """Return ``value`` if it is a plain file name in a version's folder, or refuse."""
    if (
        not isinstance(value, str)
        or not value
        or value in (".", "..")
        or "/" in value
        or "\\" in value
    ):
        raise LayoutError(f"not a file name in a version's folder: {value!r}")
    return value


def is_shared_object(key: str, data_dir: str) -> bool:
    """Whether ``key`` is named as a shared object: a data file or an asset.

    Deleting is only for these, so a stray file under the data directory is
    left alone.
    """
    if _ASSET.fullmatch(key):
        return True
    return key.startswith(data_dir) and bool(_DATA_FILE.fullmatch(key.rsplit("/", 1)[-1]))


def version_dir(version_id: str) -> str:
    return f"versions/{version_id}/"


def version_record(version_id: str) -> str:
    return version_dir(version_id) + VERSION_RECORD


def version_catalog(version_id: str) -> str:
    return version_dir(version_id) + CATALOG


def version_expired(version_id: str) -> str:
    return version_dir(version_id) + EXPIRED
