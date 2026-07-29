"""A module-level mark object beside a real inline fixture (#1757).

`_Mark` defines __getattr__, so it answers every attribute name — including the
fixture marker attribute. Inline registration probes module attributes with
getattr, so a truthiness guard treats the mark as a fixture and then crashes on
`marker.lifetime`. That took main red once; this file is the shape that did it.

The mark must be ignored and the real fixture must still register.
"""

from __future__ import annotations

import oxitest as oxi
from oxitest import Fixtures

#: A module-level mark object — the exact shape that broke registration.
MODULE_LEVEL_MARK = oxi.mark.skip(reason="never applied; here to be ignored")


@oxi.fixture(lifetime="module")
def real_fixture() -> str:
    return "registered"


def test_the_fixture_registers_despite_the_mark(fx: Fixtures) -> None:
    value = fx.test_mark_and_fixture.real_fixture
    assert value == "registered", (
        f"the inline fixture must register even though a module-level mark object "
        f"sits beside it; got {value!r}"
    )
