"""A declaring package whose subtree is split by arrangement, not by a mark.

`@oxi.arrange` on one module of a declaring package used to leave its siblings
in the parallel remainder — two phases, so two builds. No mark is involved,
which is why the inprocess rule alone does not cover this project (#2058).
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi

_COUNTER = itertools.count(1)


def _record(event: str) -> None:
    with Path(f"{os.environ['SUBTREE_ARRANGED_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="package")
def engine() -> Iterator[str]:
    instance_id = f"{os.getpid()}-{next(_COUNTER)}"
    _record(f"SETUP {instance_id}")
    yield instance_id
    _record(f"TEARDOWN {instance_id}")


@oxi.fixture(lifetime="module")
def pinned() -> str:
    """Named by @oxi.arrange in one module, which is what forms the component."""
    return "pinned"
