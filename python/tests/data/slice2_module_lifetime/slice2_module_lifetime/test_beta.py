"""A second module — proves each module gets its own instance.

The log helper is duplicated here for the same reason as in ``test_alpha``:
this project is run by path, not imported by name.
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
    _record("USE beta")


def test_beta_one(fx: Fixtures) -> None:
    _use(fx)


def test_beta_two(fx: Fixtures) -> None:
    _use(fx)
