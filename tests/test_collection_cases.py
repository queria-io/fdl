"""Run the collection cases every implementation of the spec agrees on (cases/collection.json)."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest

from fdl import repo
from fdl.stores import LocalStore

CASES = json.loads((Path(__file__).parent.parent / "cases" / "collection.json").read_text())


def _time(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda case: case["name"])
def test_collection_case(tmp_path: Path, case: dict) -> None:
    root = tmp_path / "repo"
    for key, value in case["files"].items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    for key, placed in case["objects"].items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        when = _time(placed).timestamp()
        os.utime(path, (when, when))
    store = LocalStore(root)
    try:
        plan = repo.plan_collection(
            store, grace=dt.timedelta(seconds=CASES["grace_seconds"]), now=_time(CASES["now"])
        )
        deleted = repo.collect(store, plan=plan)
    except repo.RepoError:
        # Stopping without deleting is how this implementation deletes nothing.
        deleted = []
    assert sorted(deleted) == sorted(case["deleted"])
    for key in case["objects"]:
        assert (root / key).exists() == (key not in case["deleted"])
