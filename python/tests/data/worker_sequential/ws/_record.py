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
    start = time.monotonic()
    time.sleep(0.05)
    end = time.monotonic()
    line = f"{os.getpid()} {threading.get_ident()} {start:.6f} {end:.6f} {name}"
    with Path(os.environ["WS_LOG"]).open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")
