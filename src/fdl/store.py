"""What FDL needs from the storage a repository lives in.

Read, write, and write on condition. A store without the last one can be read
from but not pushed to, because the last writer would silently replace someone
else's version.

`fdl.stores` has two: a local folder and S3. Anything else with the same
methods works too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, Sequence


class Store(Protocol):
    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> str:
        """Write ``data`` and return the stored ETag. A failed condition raises
        an error whose ``status`` is 412."""

    def put_file(
        self,
        key: str,
        path: Path,
        *,
        content_type: str | None = None,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> str: ...

    def get(self, key: str) -> bytes:
        """Read the object. A missing one raises an error whose ``status`` is 404."""

    def head(self, key: str) -> dict[str, str] | None:
        """The object's headers (``ETag`` among them), or ``None`` when missing."""

    def list(self, prefix: str = "") -> list[str]: ...

    def delete(self, keys: Sequence[str]) -> None:
        """Remove these keys. One already gone is not an error."""


def is_conflict(error: BaseException) -> bool:
    """Whether a write was refused by its condition."""
    return getattr(error, "status", None) == 412


def is_missing(error: BaseException) -> bool:
    return getattr(error, "status", None) == 404


def etag_of(headers: dict[str, str]) -> str | None:
    """The ETag out of `Store.head`, whichever case the store wrote it in."""
    found = {key.lower(): value for key, value in headers.items()}
    return found.get("etag")
