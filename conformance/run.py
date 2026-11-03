"""Run the reader cases against an implementation.

    uv run python conformance/run.py -- <command that reads>

The command is run as ``<command> <root> <ref>`` for each case and ref, where
<root> is the path of a sample repository (with a trailing slash). It prints
one JSON object on stdout:

    {"version": "<id>", "seq": 2, "tables": {"geo.town": {"rows": 10000}, ...}}

or exits with a non-zero status when it refuses to read. See README.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CASES = Path(__file__).resolve().parent / "cases"


def check(expected: dict, ran: subprocess.CompletedProcess) -> list[str]:
    if expected.get("refuse"):
        return [] if ran.returncode != 0 else ["read a repository it must refuse"]
    if ran.returncode != 0:
        return [f"refused to read: {ran.stderr.strip()}"]
    try:
        got = json.loads(ran.stdout)
    except json.JSONDecodeError:
        return [f"printed no JSON: {ran.stdout[:200]!r}"]
    problems = [
        f"{key} is {got.get(key)!r}, expected {expected[key]!r}"
        for key in ("version", "seq")
        if got.get(key) != expected[key]
    ]
    tables = got.get("tables", {})
    for name, table in expected["tables"].items():
        if name not in tables:
            if not table.get("may_skip"):
                problems.append(f"{name} is missing")
            continue
        rows = tables[name].get("rows")
        if rows != table["rows"]:
            problems.append(f"{name} has {rows} rows, expected {table['rows']}")
    problems += [f"{name} is not in the version" for name in tables if name not in expected["tables"]]
    return problems


def run(command: list[str]) -> int:
    failed = 0
    for case in sorted(p for p in CASES.iterdir() if p.is_dir()):
        expect = json.loads((case / "expect.json").read_text())
        for ref, expected in expect["reads"].items():
            ran = subprocess.run(
                [*command, str(case / "repo") + "/", ref], capture_output=True, text=True
            )
            problems = check(expected, ran)
            failed += bool(problems)
            print(f"{'FAIL' if problems else 'ok  '} {case.name} {ref}")
            for problem in problems:
                print(f"     {problem}")
    print(f"{failed} failed" if failed else "all passed")
    return 1 if failed else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--"]:
        args = args[1:]
    if not args:
        sys.exit(__doc__)
    sys.exit(run(args))
