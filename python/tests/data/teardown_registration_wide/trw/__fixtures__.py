"""A process-lifetime fixture that registers a finalizer from its own teardown.

The process tier is the one the guard nearly missed. Nothing sets
``_current_teardown_node_id`` when ``end_task``/``end_process`` drain the wide
scopes, so a guard keyed on that var reads False here and the drop stays
silent — measured, and the reason the guard keys on ``_in_teardown`` instead
(#1952).

Deliberately serial-only and separate from ``testcontext_current``: that
project is also run with ``-n 2``, and a process-lifetime teardown fires once
per worker, so folding this position into it would make its diagnostic count
depend on the scheduler.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi
from oxitest import TestContext


def _record(event: str) -> None:
    """Append one event line to the log named by ``TRW_LOG``."""
    with Path(os.environ["TRW_LOG"]).open("a", encoding="utf-8") as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="process")
def wide(ctx: TestContext) -> Iterator[str]:
    """Registers from inside its own teardown, at the widest lifetime."""
    _record("WIDE SETUP")
    yield "wide-value"
    _record("WIDE TEARDOWN START")
    # Dropped: since #1958 this ctx binds the *process* scope's teardown list —
    # the very list being drained around this call — so the append lands behind
    # the drain loop's reversed() cursor and is then cleared. Before #1958 it
    # bound the constructing test's fn_teardowns, which had drained long ago;
    # different mechanism, same outcome. The point here is that it must not be
    # silent.
    ctx.on_teardown(lambda: _record("WIDE LATE FINALIZER RAN"))
    _record("WIDE TEARDOWN END")
