"""A test that poisons the working directory and otherwise passes.

Separate from ``cwd_poison`` deliberately: there the poisoner fails on its own
assert, so ``rc != 0`` holds with or without the guard and any assertion on the
exit code is vacuous. Here the test passes on its own merits, so a non-zero
exit code can only come from the guard.
"""

from __future__ import annotations

import os

from oxitest import TempDir


def test_poisons_but_passes(tmp: TempDir) -> None:
    os.chdir(tmp.path)
    assert tmp.path.exists(), (
        "the directory exists while the test runs; it is the tmp fixture "
        "teardown that deletes it, with the worker still sitting inside"
    )
