"""A test that *also* requests an autouse fixture explicitly.

Autouse is additive, not duplicative: the autouse pass and the ``fx.`` proxy
are two routes to one definition, and ``_cache_key`` is keyed on the definition
with no route discriminator (#1775). So this module must add exactly one
``FIRE per_test`` line, not two.

The log helper is duplicated per module rather than imported: oxitest is
invoked with this project as a positional path, so the package is not
importable by name from the caller's sys.path.
"""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def _record(event: str) -> None:
    with Path(os.environ["SLICE9_LOG"]).open("a", encoding="utf-8") as fh:
        fh.write(f"{event}\n")


def test_gamma_also_requests_the_autouse_fixture(fx: Fixtures) -> None:
    value = fx.slice9_autouse_tiers.per_test
    assert value == "per_test", (
        "the explicitly requested value must be the instance the autouse pass "
        f"built, not a second one; got {value!r}"
    )
    _record(f"TEST gamma_one {os.getpid()}")
