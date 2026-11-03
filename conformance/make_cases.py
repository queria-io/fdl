"""Build the sample repositories in conformance/cases/ with the fdl CLI.

    uv run python conformance/make_cases.py

Each case is a folder with ``repo/`` (an FDL repository) and ``expect.json``
(what a reader has to report for each ref, see README.md). The cases are
committed, so implementations in other languages read them without fdl.
Version IDs change on every run; expect.json is written from what was built.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases"
FDL = [str(Path(sys.executable).with_name("fdl"))]


def fdl(*args: str) -> str:
    return subprocess.run([*FDL, *args], check=True, capture_output=True, text=True).stdout


def sql(repo: Path, work: Path, statements: str) -> None:
    """Run ``statements`` against a fresh working catalog checked out from ``repo``."""
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    out = fdl("checkout", str(repo), str(work / "lake.sqlite"))
    attach = next(line.strip() for line in out.splitlines() if line.strip().startswith("ATTACH"))
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake; INSTALL sqlite; LOAD sqlite;")
    con.execute(attach)
    con.execute(statements)
    con.close()


def commit(work: Path, message: str) -> None:
    (work / "dataset.json").write_text('{"name": "towns", "title": "Towns"}\n')
    fdl("commit", str(work / "lake.sqlite"), "-m", message, "--author", "conformance",
        "--dataset", str(work / "dataset.json"))


def main_id(repo: Path) -> str:
    return (repo / "refs/heads/main").read_text().strip()


def parent_of(repo: Path, version_id: str) -> str:
    return json.loads((repo / "versions" / version_id / "version.json").read_text())["parents"][0]


def tag(repo: Path, name: str, version_id: str) -> None:
    fdl("tag", str(repo), name, version_id)


def edit_json(path: Path, change) -> None:
    record = json.loads(path.read_text())
    change(record)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def write_case(name: str, repo: Path, about: str, reads: dict) -> None:
    case = CASES / name
    if case.exists():
        shutil.rmtree(case)
    case.mkdir(parents=True)
    shutil.copytree(repo, case / "repo")
    expect = {"about": about, "reads": reads}
    (case / "expect.json").write_text(json.dumps(expect, ensure_ascii=False, indent=2) + "\n")


BASIC_FIRST = """
CREATE SCHEMA lake.geo;
CREATE TABLE lake.geo.town AS
  SELECT printf('%06d', i // 1000) AS lg_code, i AS n FROM range(10000) t(i) ORDER BY lg_code;
CREATE TABLE lake.main.gone AS SELECT i FROM range(10) t(i);
CREATE TABLE lake.main.renamed AS SELECT i AS a FROM range(10) t(i);
CREATE TABLE lake.main.empty (x INTEGER);
"""
BASIC_SECOND = """
DELETE FROM lake.main.gone WHERE i < 3;
ALTER TABLE lake.main.renamed RENAME COLUMN a TO b;
"""


def build_basic(scratch: Path, *, data_dir: str = "data/") -> tuple[Path, str, str]:
    """Two versions on main; the first is tagged ``first``."""
    repo, work = scratch / "repo", scratch / "work"
    fdl("init", str(repo), "--data-dir", data_dir)
    sql(repo, work, BASIC_FIRST)
    commit(work, "First")
    sql(repo, work, BASIC_SECOND)
    commit(work, "Second")
    second = main_id(repo)
    first = parent_of(repo, second)
    tag(repo, "first", first)
    shutil.rmtree(work)
    return repo, first, second


def read(version: str, seq: int, tables: dict) -> dict:
    return {"version": version, "seq": seq, "tables": tables}


def tables(first: bool) -> dict:
    # A reader that does not apply deletes or renamed columns may skip a table
    # that has them (Versions, files.json); it must not report other rows.
    return {
        "geo.town": {"rows": 10000},
        "main.empty": {"rows": 0},
        "main.gone": {"rows": 10} if first else {"rows": 7, "may_skip": True},
        "main.renamed": {"rows": 10} if first else {"rows": 10, "may_skip": True},
    }


def main() -> None:
    scratch = HERE / ".build"
    if scratch.exists():
        shutil.rmtree(scratch)
    if CASES.exists():
        shutil.rmtree(CASES)

    def fresh(name: str, **kwargs) -> tuple[Path, str, str]:
        return build_basic(scratch / name, **kwargs)

    repo, first, second = fresh("basic")
    write_case("basic", repo, "Two versions on main, the first tagged.", {
        "HEAD": read(second, 2, tables(False)),
        "refs/heads/main": read(second, 2, tables(False)),
        "refs/tags/first": read(first, 1, tables(True)),
    })

    repo, first, second = fresh("data-dir", data_dir="files/")
    write_case("data-dir", repo, "Data files under data_dir files/, not data/.", {
        "HEAD": read(second, 2, tables(False)),
    })

    repo, first, second = fresh("ignore-unknown")
    (repo / "NOTES.txt").write_text("Files with other names may sit at the root.\n")
    edit_json(repo / "versions" / second / "version.json",
              lambda r: r.update({"from_a_later_minor": {"x": 1}}))
    edit_json(repo / "fdl.json", lambda r: r.update({"from_a_later_minor": True}))
    write_case("ignore-unknown", repo, "Unknown fields and root files are ignored.", {
        "HEAD": read(second, 2, tables(False)),
    })

    repo, first, second = fresh("parents")
    edit_json(repo / "versions" / second / "version.json",
              lambda r: r.update({"parents": [first, "01a00000-0000-7000-8000-000000000000"]}))
    write_case("parents", repo, "parents is a list of any length.", {
        "HEAD": read(second, 2, tables(False)),
    })

    repo, first, second = fresh("unknown-major")
    edit_json(repo / "fdl.json", lambda r: r.update({"fdl": "99.0"}))
    write_case("unknown-major", repo, "fdl.json is of a major version no reader knows.", {
        "HEAD": {"refuse": True},
    })

    repo, first, second = fresh("version-major")
    edit_json(repo / "versions" / second / "version.json", lambda r: r.update({"fdl": "99.0"}))
    write_case("version-major", repo, "version.json is of a major version no reader knows.", {
        "HEAD": {"refuse": True},
        "refs/tags/first": read(first, 1, tables(True)),
    })

    repo, first, second = fresh("id-mismatch")
    edit_json(repo / "versions" / second / "version.json",
              lambda r: r.update({"id": "01a00000-0000-7000-8000-000000000001"}))
    write_case("id-mismatch", repo, "version.json records another id than its folder.", {
        "HEAD": {"refuse": True},
    })

    repo, first, second = fresh("expired")
    # A ref must not point at an expired version; a reader still has to stop.
    (repo / "versions" / first / "expired.json").write_text(
        '{"expired_at": "2026-11-03T00:00:00Z", "reason": "retention"}\n'
    )
    # The catalog is left in place, as a mirror that missed the deletion would
    # serve it: the reader stops on expired.json, not on a missing catalog.
    write_case("expired", repo, "The tagged version is past retention.", {
        "HEAD": read(second, 2, tables(False)),
        "refs/tags/first": {"refuse": True},
    })

    shutil.rmtree(scratch)
    # IDs are the only thing that differs between runs.
    assert all(re.fullmatch(r"[a-z0-9-]+", p.name) for p in CASES.iterdir())
    print(f"wrote {len(list(CASES.iterdir()))} cases to {CASES.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
