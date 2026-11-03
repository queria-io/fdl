"""Check a repository against what the specification requires of writers.

    uv run python conformance/check.py <root>

<root> is a folder, ``https://…/`` or ``s3://…/``. Run it on what a writer
left behind, or on a host's copy, to see that the files it serves are a valid
repository. It only reads. Versions are found from the refs and their
parents, so it works over HTTP too, where nothing can be listed.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

import duckdb

from fdl.store import is_missing
from fdl.stores import open_store

ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
VERSION_FIELDS = ("fdl", "id", "seq", "parents", "created_at", "author", "message", "catalog", "ducklake")


class Checker:
    def __init__(self, root: str) -> None:
        self.store = open_store(root)
        self.problems: list[str] = []
        self.notes: list[str] = []

    def fail(self, where: str, problem: str) -> None:
        self.problems.append(f"{where}: {problem}")

    def get(self, key: str) -> bytes | None:
        try:
            return self.store.get(key)
        except Exception as exc:  # noqa: BLE001 - stores raise their own types
            if is_missing(exc):
                return None
            raise

    def note(self, where: str, note: str) -> None:
        self.notes.append(f"{where}: {note}")

    def json(self, key: str) -> dict | None:
        data = self.get(key)
        if data is None:
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            self.fail(key, "is not JSON")
            return None

    def run(self) -> list[str]:
        config = self.json("fdl.json")
        if config is None:
            self.fail("fdl.json", "is missing")
            return self.problems
        if str(config.get("fdl", "")).split(".")[0] != "0":
            self.fail("fdl.json", f"fdl is {config.get('fdl')!r}")
        self.data_dir = config.get("data_dir", "data/")
        if not self.data_dir.endswith("/") or self.data_dir.startswith("/") or ".." in self.data_dir.split("/"):
            self.fail("fdl.json", f"data_dir {self.data_dir!r} is not a folder under the root")
        refs = self.check_refs()
        seen: set[str] = set()
        pending = list(refs.values())
        while pending:
            version_id = pending.pop()
            if version_id in seen:
                continue
            seen.add(version_id)
            pending.extend(self.check_version(version_id, pointed=version_id in refs.values()))
        self.check_root()
        return self.problems

    def check_refs(self) -> dict[str, str]:
        head = self.get("HEAD")
        if head is None:
            self.fail("HEAD", "is missing")
        elif not head.decode().startswith("ref: refs/heads/"):
            self.fail("HEAD", f"does not name a branch: {head[:80]!r}")
        index = self.json("refs.json")
        names = self.listed_refs(index)
        refs = {}
        for name in names:
            data = self.get(name)
            if data is None:
                # refs.json may lag behind the refs (Refs, refs.json).
                self.note(name, "is listed in refs.json but missing")
                continue
            value = data.decode().strip()
            if not ID.match(value):
                self.fail(name, f"does not hold a version ID: {value[:80]!r}")
                continue
            refs[name] = value
        if index is not None:
            if index.get("refs") != refs:
                self.note("refs.json", f"lags behind the refs: says {index.get('refs')}, the refs hold {refs}")
            if head is not None and index.get("head") != head.decode().removeprefix("ref: ").strip():
                self.note("refs.json", f"head is {index.get('head')!r}")
        return refs

    def listed_refs(self, index: dict | None) -> list[str]:
        listing = getattr(self.store, "list", None)
        try:
            names = [key for key in listing("refs/") if not key.endswith("/")] if listing else None
        except Exception:  # noqa: BLE001 - a store over HTTP cannot list
            names = None
        if names is None:
            if index is not None:
                names = list(index.get("refs", {}))
            else:
                self.note("refs.json", "is missing and refs/ cannot be listed: only the branch HEAD names is checked")
                head = self.get("HEAD")
                names = [head.decode().removeprefix("ref: ").strip()] if head else []
        return sorted(names)

    def check_version(self, version_id: str, *, pointed: bool) -> list[str]:
        where = f"versions/{version_id}"
        record = self.json(f"{where}/version.json")
        expired = self.json(f"{where}/expired.json")
        if record is None:
            if expired is None or expired.get("reason") != "withdrawn":
                self.fail(where, "has no version.json")
            return []
        if pointed and expired is not None:
            self.fail(where, "is expired but a ref points at it")
        for field in VERSION_FIELDS:
            if field not in record:
                self.fail(f"{where}/version.json", f"has no {field}")
        if str(record.get("fdl", "")).split(".")[0] != "0":
            self.fail(f"{where}/version.json", f"fdl is {record.get('fdl')!r}")
        if record.get("id") != version_id:
            self.fail(f"{where}/version.json", f"records id {record.get('id')!r}")
        if not ID.match(version_id):
            self.fail(where, "is not named by a UUIDv7")
        if not UTC.match(str(record.get("created_at", ""))):
            self.fail(f"{where}/version.json", f"created_at {record.get('created_at')!r} is not UTC ISO 8601")
        if not isinstance(record.get("author"), dict) or not record["author"].get("name"):
            self.fail(f"{where}/version.json", "author has no name")
        parents = record.get("parents", [])
        self.check_seq(where, record, parents)
        if expired is None:
            for field in ("catalog", "dataset", "files"):
                if isinstance(record.get(field), dict):
                    self.check_file(where, record[field], field)
            self.check_catalog(where, record)
        return [p for p in parents if isinstance(p, str)]

    def check_seq(self, where: str, record: dict, parents: list) -> None:
        if not parents:
            if record.get("seq") != 1:
                self.fail(f"{where}/version.json", f"seq is {record.get('seq')} with no parent")
            return
        parent = self.json(f"versions/{parents[0]}/version.json")
        if parent is not None and record.get("seq") != parent.get("seq", 0) + 1:
            self.fail(f"{where}/version.json", f"seq is {record.get('seq')}, its parent's is {parent.get('seq')}")

    def check_file(self, where: str, entry: dict, field: str) -> None:
        key = f"{where}/{entry.get('path')}"
        data = self.get(key)
        if data is None:
            self.fail(key, f"is named by {field} but missing")
            return
        if entry.get("bytes") != len(data):
            self.fail(key, f"is {len(data)} bytes, {field}.bytes says {entry.get('bytes')}")
        if entry.get("sha256") != hashlib.sha256(data).hexdigest():
            self.fail(key, f"does not match {field}.sha256")

    def check_catalog(self, where: str, record: dict) -> None:
        data = self.get(f"{where}/{record.get('catalog', {}).get('path', 'ducklake.duckdb')}")
        if data is None:
            return
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.duckdb"
            path.write_bytes(data)
            con = duckdb.connect(str(path), read_only=True)
            try:
                self.check_catalog_rows(where, record, con)
            finally:
                con.close()

    def check_catalog_rows(self, where: str, record: dict, con) -> None:
        snapshot = record.get("ducklake", {}).get("snapshot_id")
        if not con.execute("SELECT 1 FROM ducklake_snapshot WHERE snapshot_id = ?", [snapshot]).fetchone():
            self.fail(f"{where}/ducklake.duckdb", f"has no snapshot {snapshot}")
        live = "begin_snapshot <= $s AND (end_snapshot IS NULL OR end_snapshot > $s)"
        # A relative path is relative to its table's, and that to its schema's.
        files = con.execute(
            f"""
            WITH f AS (
                SELECT table_id, path, path_is_relative FROM ducklake_data_file WHERE {live}
                UNION ALL
                SELECT table_id, path, path_is_relative FROM ducklake_delete_file WHERE {live}
            ),
            t AS (SELECT * FROM ducklake_table WHERE {live}),
            sc AS (SELECT * FROM ducklake_schema WHERE {live})
            SELECT
                CASE WHEN NOT f.path_is_relative THEN f.path
                     WHEN NOT t.path_is_relative THEN t.path || f.path
                     WHEN NOT sc.path_is_relative THEN sc.path || t.path || f.path
                     ELSE sc.path || t.path || f.path END,
                f.path_is_relative AND t.path_is_relative AND sc.path_is_relative
            FROM f JOIN t USING (table_id) JOIN sc ON sc.schema_id = t.schema_id
            """,
            {"s": snapshot},
        ).fetchall()
        tables = {
            name: con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            for (name,) in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE starts_with(table_name, 'ducklake_inlined_data_') "
                "AND table_name <> 'ducklake_inlined_data_tables'"
            ).fetchall()
        }
        for name, rows in tables.items():
            if rows:
                self.fail(f"{where}/ducklake.duckdb", f"holds rows inline ({name})")
        for path, relative in files:
            if not relative:
                self.fail(f"{where}/ducklake.duckdb", f"names a data file by an absolute path: {path}")
                continue
            key = self.data_dir + path
            if ".." in key.split("/"):
                self.fail(f"{where}/ducklake.duckdb", f"names a data file outside data_dir: {path}")
            elif self.store.head(key) is None:
                self.fail(key, "is named by the catalog but missing")

    def check_root(self) -> None:
        listing = getattr(self.store, "list", None)
        if listing is None:
            return
        try:
            keys = listing("")
        except Exception:  # noqa: BLE001 - a store over HTTP cannot list
            return
        for key in keys:
            if "/" in key:
                continue
            if key.endswith((".duckdb", ".sqlite", ".db", ".wal")) or key.startswith("ducklake"):
                self.fail(key, "is a catalog at the root, outside versions/")


def main(root: str) -> int:
    checker = Checker(root)
    problems = checker.run()
    for note in checker.notes:
        print(f"note {note}")
    for problem in problems:
        print(f"FAIL {problem}")
    print(f"{len(problems)} problems" if problems else "ok")
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
