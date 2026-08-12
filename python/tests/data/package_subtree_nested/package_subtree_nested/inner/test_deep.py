"""The inner package's tests — one marked, one not."""

from __future__ import annotations

import os
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture


def _record_use(label: str, inner: str, outer: str) -> None:
    with Path(f"{os.environ['SUBTREE_NESTED_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"USE {label} {os.getpid()} {inner}/{outer}\n")


@oxi.mark.inprocess
def test_deep_marked(inner: Fixture[str], outer: Fixture[str]) -> None:
    _record_use("deep_marked", inner, outer)
    assert inner and outer, "both package fixtures must be injected"


def test_deep_plain(inner: Fixture[str], outer: Fixture[str]) -> None:
    _record_use("deep_plain", inner, outer)
    assert inner and outer, "both package fixtures must be injected"
