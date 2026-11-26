"""Run the expire cases every implementation of the spec agrees on (cases/expire.json)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from fdl import layout, repo
from fdl.stores import LocalStore

CASES = json.loads((Path(__file__).parent.parent / "cases" / "expire.json").read_text())


def _time(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda case: case["name"])
def test_retention_case(tmp_path: Path, case: dict) -> None:
    root = tmp_path / "repo"
    files = {"fdl.json": {"fdl": "0.1", "data_dir": "data/"}}
    for version in case["versions"]:
        files[layout.version_record(version["id"])] = {**CASES["record"], **version}
    for version_id in case["expired"]:
        files[layout.version_expired(version_id)] = {"expired_at": CASES["now"], "reason": "retention"}
    for key, value in files.items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    for name, target in case["refs"].items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(target + "\n")
    # HEAD names the default branch: main unless the case says otherwise.
    (root / layout.HEAD).write_text(f"{layout.SYMBOLIC}{case.get('head', layout.DEFAULT_BRANCH)}\n")

    taken = repo.plan_retention(
        LocalStore(root),
        keep=case["keep"],
        since=case["since"],
        grace=dt.timedelta(seconds=CASES["grace_seconds"]),
        now=_time(CASES["now"]),
    )
    assert sorted(taken) == sorted(case["take_out"])
