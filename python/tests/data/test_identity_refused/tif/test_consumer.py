"""Two tests share the module-lifetime consumer.

Neither test asserts anything about the value. The refusal happens while the
consumer is being built, so these bodies never run — what the runner reports is
the whole measurement.
"""

from __future__ import annotations

from oxitest import Fixture


def test_first(outer: Fixture[str]) -> None:
    """Consumes the wider fixture so it gets built."""
    assert outer, "unreachable — inner must refuse identity before outer builds"


def test_second(outer: Fixture[str]) -> None:
    """Consumes the cached value the first test would have pinned."""
    assert outer, "unreachable — inner must refuse identity before outer builds"
