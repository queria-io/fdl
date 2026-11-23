"""The record of a version (`version.json`) and how its ID is made."""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from dataclasses import dataclass

from . import layout



class VersionError(ValueError):
    """A version record this format cannot read."""


def new_id(now_ms: int | None = None) -> str:
    """A UUIDv7: ordered by the time it was made, and safe to make without
    asking anyone, so two writers never collide.

    By hand because `uuid.uuid7` arrived in Python 3.14.
    """
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= (int.from_bytes(os.urandom(2), "big") & 0x0FFF) << 64
    value |= 0b10 << 62
    value |= int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    text = f"{value:032x}"
    return f"{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}"


def is_id(value: str) -> bool:
    return layout.is_version_id(value)


@dataclass(frozen=True)
class Metadata:
    """The version's DuckLake metadata file (`metadata.db`): its storage format, size and hash."""

    storage: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class File:
    """A file of the version beside its metadata (`tables.json`), pinned by its hash."""

    bytes: int
    sha256: str


@dataclass(frozen=True)
class DuckLake:
    format: str
    snapshot_id: int
    #: The first and last snapshot this version holds past its parent's.
    snapshots: tuple[int, int]


@dataclass(frozen=True)
class Version:
    id: str
    seq: int
    parents: tuple[str, ...]
    created_at: str
    author: dict
    message: str
    metadata: Metadata
    ducklake: DuckLake
    #: What changed since the first parent, as `fdl diff --json` prints it. Optional.
    diff: dict | None = None
    #: The version's `tables.json`: each table's files and columns, each view, and the
    #: declarations. None only in the record of a version past retention written before it.
    tables: File | None = None
    fdl: str = layout.FDL_VERSION

    @property
    def changes(self) -> tuple[dict, ...]:
        """The column differences from the first parent (``diff.columns``)."""
        return tuple((self.diff or {}).get("columns", ()))

    def to_json(self) -> bytes:
        record = {
            "fdl": self.fdl,
            "id": self.id,
            "seq": self.seq,
            "parents": list(self.parents),
            "created_at": self.created_at,
            "author": self.author,
            "message": self.message,
            "metadata": {
                "storage": self.metadata.storage,
                "bytes": self.metadata.bytes,
                "sha256": self.metadata.sha256,
            },
            "ducklake": {
                "format": self.ducklake.format,
                "snapshot_id": self.ducklake.snapshot_id,
                "snapshots": list(self.ducklake.snapshots),
            },
            **({"tables": _entry(self.tables)} if self.tables else {}),
            **({"diff": self.diff} if self.diff is not None else {}),
        }
        return (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode()


def _entry(file: File) -> dict:
    return {"bytes": file.bytes, "sha256": file.sha256}


def parse(data: bytes, *, expected_id: str | None = None) -> Version:
    """Read a `version.json`, refusing what a reader of this format must not open."""
    try:
        record = json.loads(data)
        major = str(record["fdl"]).split(".")[0]
        if major != layout.FDL_VERSION.split(".")[0]:
            raise VersionError(f"written for fdl {record['fdl']}, this reads {layout.FDL_VERSION}")
        if expected_id is not None and record["id"] != expected_id:
            raise VersionError(f"records id {record['id']} but sits under {expected_id}")
        metadata = record["metadata"]
        ducklake = record["ducklake"]
        tables = record.get("tables")
        return Version(
            fdl=record["fdl"],
            id=layout.check_version_id(record["id"]),
            seq=int(record["seq"]),
            parents=tuple(layout.check_version_id(p) for p in record["parents"]),
            created_at=record["created_at"],
            author=dict(record["author"]),
            message=record["message"],
            # The files have fixed names: a `path` an earlier writer gave is not read.
            metadata=Metadata(
                storage=metadata["storage"],
                bytes=int(metadata["bytes"]),
                sha256=metadata["sha256"],
            ),
            ducklake=DuckLake(
                format=ducklake["format"],
                snapshot_id=int(ducklake["snapshot_id"]),
                snapshots=tuple(ducklake["snapshots"]),
            ),
            diff=record.get("diff"),
            tables=(
                File(bytes=int(tables["bytes"]), sha256=tables["sha256"])
                if tables
                else None
            ),
        )
    except VersionError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise VersionError(f"not a version record: {exc}") from exc


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
