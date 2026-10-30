"""Reading refs, and moving them only on condition.

A ref holds a version ID and a newline. HEAD alone holds ``ref: <name>``
instead. Every move is a conditional write: create when absent, replace when
the ETag is still the one read. A writer that loses the race is told so and
has to build on what won, which is what keeps two pushes from erasing each
other without a server in between.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import layout
from .store import Store, etag_of, is_conflict, is_missing


class RefError(RuntimeError):
    """A ref that is malformed, or names something that is not there."""


class RefConflict(RefError):
    """The ref moved after it was read."""


@dataclass(frozen=True)
class Ref:
    name: str
    #: A version ID, or for HEAD the ref it points at.
    target: str
    etag: str | None


def read(store: Store, name: str) -> Ref | None:
    """The ref as stored, with the ETag a move has to name. ``None`` if absent.

    The ETag is read first: if the ref moves in between, the content is newer
    than the ETag and the move that follows is refused, which is the safe way
    round.
    """
    headers = store.head(name)
    if headers is None:
        # Some stores answer a HEAD they may not read (403) as absent. Taken at
        # its word, a branch nobody can read looks unborn, and the version
        # built on it loses its parent. A GET says which it is: missing is
        # 404, anything else is raised.
        try:
            store.get(name)
        except Exception as exc:  # noqa: BLE001 - stores raise their own types
            if is_missing(exc):
                return None
            raise
        raise RefError(f"{name} is there but its ETag could not be read")
    try:
        text = store.get(name).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if is_missing(exc):
            return None
        raise
    if not text.endswith("\n") or text.count("\n") != 1:
        raise RefError(f"{name} is not one line ending in a newline")
    target = text[:-1]
    if name == layout.HEAD:
        if not target.startswith(layout.SYMBOLIC):
            raise RefError(f"HEAD does not point at a ref: {target!r}")
        target = layout.check_ref(target[len(layout.SYMBOLIC) :])
    return Ref(name=name, target=target, etag=etag_of(headers))


def resolve(store: Store, name: str = layout.HEAD) -> tuple[str, str | None]:
    """The ref ``name`` stands for and the version it holds (``None`` if unborn)."""
    if name == layout.HEAD:
        head = read(store, layout.HEAD)
        name = head.target if head else layout.DEFAULT_BRANCH
    ref = read(store, layout.check_ref(name))
    return name, ref.target if ref else None


def write(store: Store, name: str, version_id: str, *, expected: Ref | None) -> str:
    """Point ``name`` at ``version_id``, on condition it still is ``expected``.

    ``expected`` is what the caller read: ``None`` to create the ref. Returns
    the new ETag.
    """
    layout.check_ref(name)
    condition = (
        {"if_match": expected.etag} if expected is not None else {"if_none_match": "*"}
    )
    if expected is not None and not expected.etag:
        raise RefError(f"{name} was read without an ETag, so it cannot be moved safely")
    try:
        return store.put(
            name,
            f"{version_id}\n".encode(),
            content_type="text/plain; charset=utf-8",
            **condition,
        )
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if is_conflict(exc):
            raise RefConflict(f"{name} moved since it was read") from exc
        raise


def write_head(store: Store, branch: str = layout.DEFAULT_BRANCH) -> None:
    """Create HEAD pointing at ``branch``. Left alone when it is already there."""
    try:
        store.put(
            layout.HEAD,
            f"{layout.SYMBOLIC}{layout.check_ref(branch)}\n".encode(),
            content_type="text/plain; charset=utf-8",
            if_none_match="*",
        )
    except Exception as exc:  # noqa: BLE001 - stores raise their own types
        if not is_conflict(exc):
            raise
