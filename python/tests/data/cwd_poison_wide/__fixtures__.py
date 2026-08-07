"""A module-lifetime fixture whose teardown deletes the directory in use.

Wider-than-function fixtures drain through ``_Scope.drain``, which runs outside
``run_test``'s ``finally``. Without a check at that boundary the deletion is
attributed to whichever test happens to run next — or, when the drain is the
last thing in the run, to nothing at all.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi


@oxi.fixture(lifetime="module")
def doomed_dir() -> Iterator[Path]:
    d = Path(tempfile.mkdtemp(prefix="wide_")).resolve()
    yield d
    os.chdir(d)
    shutil.rmtree(d, ignore_errors=True)
