"""A module in a subdirectory with no ``__init__.py`` of its own.

Two rules meet here. B1 makes a package fixture usable from descendant
directories, so this module must see the ancestor's instance rather than build
its own. And the boundary is the directory itself — this one carries no
``__init__.py``, which ADR-0009's amended wording (#1746) says is irrelevant.
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


def test_gamma_sees_the_ancestor_instance(fx: Fixtures) -> None:
    engine = fx.slice3_package_lifetime.engine
    assert engine is not None, (
        "a descendant directory must resolve the ancestor package's fixture"
    )
    _record(f"USE gamma {engine}")
