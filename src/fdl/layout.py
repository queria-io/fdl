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
DEFAULT_BRANCH = "refs/heads/main"
DEFAULT_DATA_DIR = "data/"

VERSION_RECORD = "version.json"
CATALOG = "ducklake.duckdb"
EXPIRED = "expired.json"
#: Other formats of the same catalog, under the version (optional).
CATALOGS_DIR = "catalogs/"

#: What a symbolic ref starts with. HEAD is the only one.
SYMBOLIC = "ref: "

_REF_PART = re.compile(r"[a-z0-9][a-z0-9._-]*")
_REF_KINDS = ("heads", "tags", "requests")
_REF_MAX = 128


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


def version_dir(version_id: str) -> str:
    return f"versions/{version_id}/"


def version_record(version_id: str) -> str:
    return version_dir(version_id) + VERSION_RECORD


def version_catalog(version_id: str) -> str:
    return version_dir(version_id) + CATALOG


def version_expired(version_id: str) -> str:
    return version_dir(version_id) + EXPIRED
