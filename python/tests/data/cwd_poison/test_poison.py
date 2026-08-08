"""A test that leaves the worker's working directory deleted, plus a victim.

Mirrors #1957's shape: chdir into the per-test temp dir, fail before any
restore, and let the tmp fixture teardown delete the directory underneath.
The victim then spawns a subprocess, which is what dies when the worker is
sitting in a directory that no longer exists.

Named ``aaa``/``bbb`` so the poisoner runs first.
"""

from __future__ import annotations

import os
import subprocess
import sys

from oxitest import TempDir


def test_aaa_poisoner(tmp: TempDir) -> None:
    os.chdir(tmp.path)
    assert False, (  # noqa: B011
        "deliberate failure, leaving the working directory inside the temp dir "
        "that the tmp fixture teardown is about to delete"
    )


def test_bbb_victim() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import coverage"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, (
        "a child inheriting a deleted working directory dies at import, which is "
        f"the cascade the guard exists to stop: {result.stderr[-200:]}"
    )
