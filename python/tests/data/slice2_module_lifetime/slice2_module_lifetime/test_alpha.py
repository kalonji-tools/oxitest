"""Three tests sharing one module-lifetime instance.

The log helper is duplicated in each test module rather than imported from
``__fixtures__``: oxitest is invoked with this project as a positional path,
so the package is not importable by name from the caller's sys.path.
"""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def _record(event: str) -> None:
    with Path(os.environ["SLICE2_LOG"]).open("a") as fh:
        fh.write(f"{event}\n")


def _use(fx: Fixtures) -> None:
    resource = fx.slice2_module_lifetime.resource
    assert resource is not None, "the module-lifetime fixture must be injected"
    _record("USE alpha")


def test_alpha_one(fx: Fixtures) -> None:
    _use(fx)


def test_alpha_two(fx: Fixtures) -> None:
    _use(fx)


def test_alpha_three(fx: Fixtures) -> None:
    _use(fx)
