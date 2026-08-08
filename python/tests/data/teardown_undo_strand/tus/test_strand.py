"""``Patcher.close`` restores into a deleted directory and must not go quiet.

The first test's ``chdir`` undo raises: it restores to a directory the test
deleted. The final working directory is left **alive** on purpose, so the
#1957 cwd guard does not fire and cannot be mistaken for this diagnostic.

The second test observes the env override registered *before* the failing
undo. ``close`` iterates in reverse, so the chdir undo runs first; if its
failure aborts the loop, ``PROBE_VAR`` survives into this test.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from oxitest import Patcher, TempDir


def test_a_close_raises_midway(tmp: TempDir, patch: Patcher) -> None:
    alive = Path.cwd()
    doomed = tmp.path / "doomed"
    doomed.mkdir()

    patch.setenv("PROBE_VAR", "patched")
    os.chdir(doomed)
    patch.chdir(alive)
    shutil.rmtree(doomed)

    assert os.environ["PROBE_VAR"] == "patched", (
        "the override must be live inside this test, or the next test's "
        "assertion measures the set rather than the restore"
    )


def test_b_observes_env_after() -> None:
    assert "PROBE_VAR" not in os.environ, (
        "every undo must run even when an earlier one raises; a stranded "
        "env override leaks into every later test in this worker"
    )
