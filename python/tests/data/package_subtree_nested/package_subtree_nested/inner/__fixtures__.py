"""The inner declaring package, nested under the outer one."""

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
def inner() -> Iterator[str]:
    instance_id = f"{os.getpid()}-{next(_COUNTER)}"
    _record(f"SETUP inner-{instance_id}")
    yield instance_id
    _record(f"TEARDOWN inner-{instance_id}")
