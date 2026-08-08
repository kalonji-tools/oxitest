"""One record per test: which process, which thread, and when it ran."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path


def window(name: str) -> None:
    """Append ``<pid> <thread ident> <start> <end> <name>`` for a 50 ms window.

    The sleep is what makes overlap detectable. Two tests running back to back
    produce disjoint intervals however fast they are; two running at once
    overlap for essentially the whole window. Without it the intervals are
    sub-millisecond and the overlap assertion would pass under a concurrent
    mutant by luck.
    """
    start = time.perf_counter()
    time.sleep(0.05)
    end = time.perf_counter()
    line = f"{os.getpid()} {threading.get_ident()} {start:.6f} {end:.6f} {name}"
    # One shard per process, not one shared file. Two workers appending to the
    # same path is only safe because POSIX makes an O_APPEND write under
    # PIPE_BUF atomic; Windows promises nothing of the sort, and the race was
    # measured there as both a lost record ("got 4 records") and a torn one
    # ("not enough values to unpack (expected 5, got 0)"). The assertions group
    # by pid regardless, so the shared file bought nothing (#1989).
    shard = Path(f"{os.environ['WS_LOG']}.{os.getpid()}")
    with shard.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")
