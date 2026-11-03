"""fdl passes its own conformance suite (conformance/)."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "conformance" / "cases"
VALID = ["basic", "data-dir", "ignore-unknown"]


def test_fdl_reads_every_case_as_expected():
    ran = subprocess.run(
        [sys.executable, "conformance/run.py", "--", sys.executable, "conformance/adapters/fdl_reader.py"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr


@pytest.mark.parametrize("case", VALID)
def test_the_valid_cases_pass_the_check(case):
    ran = subprocess.run(
        [sys.executable, "conformance/check.py", str(CASES / case / "repo")],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr


@pytest.mark.parametrize("case", ["expired", "id-mismatch", "unknown-major", "version-major"])
def test_the_broken_cases_fail_the_check(case):
    ran = subprocess.run(
        [sys.executable, "conformance/check.py", str(CASES / case / "repo")],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert ran.returncode == 1, ran.stdout + ran.stderr
