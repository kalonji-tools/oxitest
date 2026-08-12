"""The outer declaring package.

`group_by_package` gives the outermost declaration the whole subtree, so a mark
in the *inner* package must move the outer one's modules too. Honouring the
inner anchor alone would split the outer subtree and rebuild its value, which
is the duplicate the tier exists to prevent (#2058).
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi

_COUNTER = itertools.count(1)


def _record(event: str) -> None:
    with Path(f"{os.environ['SUBTREE_NESTED_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="package")
def outer() -> Iterator[str]:
    instance_id = f"{os.getpid()}-{next(_COUNTER)}"
    _record(f"SETUP outer-{instance_id}")
    yield instance_id
    _record(f"TEARDOWN outer-{instance_id}")
