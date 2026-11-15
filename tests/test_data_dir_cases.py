"""Run the data_dir cases every implementation of the spec agrees on (cases/data-dir.json)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fdl import layout, repo
from fdl.stores import LocalStore

CASES = json.loads((Path(__file__).parent.parent / "cases" / "data-dir.json").read_text())


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda case: case["name"])
def test_data_dir_case(tmp_path: Path, case: dict) -> None:
    store = LocalStore(tmp_path)
    store.put(layout.CONFIG, json.dumps(case["fdl_json"]).encode())
    if case["data_dir"] is None:
        with pytest.raises(repo.RepoError, match="data_dir"):
            repo.read_config(store)
    else:
        assert repo.read_config(store).data_dir == case["data_dir"]
