"""The record of a version (`version.json`) and how its ID is made."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from dataclasses import dataclass, field

from . import layout

_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


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
    return bool(_ID.fullmatch(value))


@dataclass(frozen=True)
class Catalog:
    path: str
    storage: str
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
    catalog: Catalog
    ducklake: DuckLake
    changes: tuple[dict, ...] = ()
    inputs: tuple[dict, ...] = ()
    extensions: dict = field(default_factory=dict)
    fdl: str = layout.FDL_VERSION

    def to_json(self) -> bytes:
        record = {
            "fdl": self.fdl,
            "id": self.id,
            "seq": self.seq,
            "parents": list(self.parents),
            "created_at": self.created_at,
            "author": self.author,
            "message": self.message,
            "catalog": {
                "path": self.catalog.path,
                "storage": self.catalog.storage,
                "bytes": self.catalog.bytes,
                "sha256": self.catalog.sha256,
            },
            "ducklake": {
                "format": self.ducklake.format,
                "snapshot_id": self.ducklake.snapshot_id,
                "snapshots": list(self.ducklake.snapshots),
            },
            "changes": list(self.changes),
            "inputs": list(self.inputs),
            "extensions": self.extensions,
        }
        return (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode()


def parse(data: bytes, *, expected_id: str | None = None) -> Version:
    """Read a `version.json`, refusing what a reader of this format must not open."""
    try:
        record = json.loads(data)
        major = str(record["fdl"]).split(".")[0]
        if major != layout.FDL_VERSION.split(".")[0]:
            raise VersionError(f"written for fdl {record['fdl']}, this reads {layout.FDL_VERSION}")
        if expected_id is not None and record["id"] != expected_id:
            raise VersionError(f"records id {record['id']} but sits under {expected_id}")
        catalog = record["catalog"]
        ducklake = record["ducklake"]
        return Version(
            fdl=record["fdl"],
            id=record["id"],
            seq=int(record["seq"]),
            parents=tuple(record["parents"]),
            created_at=record["created_at"],
            author=dict(record["author"]),
            message=record["message"],
            catalog=Catalog(
                path=catalog["path"],
                storage=catalog["storage"],
                bytes=int(catalog["bytes"]),
                sha256=catalog["sha256"],
            ),
            ducklake=DuckLake(
                format=ducklake["format"],
                snapshot_id=int(ducklake["snapshot_id"]),
                snapshots=tuple(ducklake["snapshots"]),
            ),
            changes=tuple(record.get("changes", ())),
            inputs=tuple(record.get("inputs", ())),
            extensions=dict(record.get("extensions", {})),
        )
    except VersionError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise VersionError(f"not a version record: {exc}") from exc


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
