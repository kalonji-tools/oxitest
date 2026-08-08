"""Tests sharing the package-lifetime instance declared in ``__init__.py``.

The log helper is duplicated per module rather than imported: oxitest is
invoked with this project as a positional path, so the package is not
importable by name from the caller's sys.path.
"""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def _record(event: str) -> None:
    with Path(f"{os.environ['SLICE3_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"{event}\n")


def _use(fx: Fixtures) -> None:
    engine = fx.slice3_package_lifetime.engine
    assert engine is not None, "the package-lifetime fixture must be injected"
    _record(f"USE alpha {engine}")


def test_alpha_one(fx: Fixtures) -> None:
    _use(fx)


def test_alpha_two(fx: Fixtures) -> None:
    _use(fx)
