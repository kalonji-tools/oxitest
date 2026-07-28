"""Tests for oxitest.fixture public re-export."""

from __future__ import annotations

import oxitest as oxi
from oxitest._bridge._fixture_decorator import fixture as _decorator_impl


def test_oxi_fixture_is_the_real_decorator() -> None:
    """oxitest.fixture must be the real decorator (not the pre-ADR-0009 sentinel)."""
    assert oxi.fixture is _decorator_impl, (
        "oxitest.fixture must be the real decorator (not the sentinel that "
        "raises AttributeError from the pre-ADR-0009 API)"
    )


def test_oxi_fixture_works_via_public_namespace() -> None:
    """Public decorator produces the same marker as the internal impl."""

    @oxi.fixture(lifetime="function")
    def sample() -> int:
        return 42

    assert hasattr(sample, "__oxitest_fixture__"), (
        "public decorator must produce the same marker as the internal impl"
    )
    assert sample() == 42, "wrapped function must still be callable"
