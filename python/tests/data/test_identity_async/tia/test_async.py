"""Two async tests, so a shared identity fails rather than passing by luck."""

from __future__ import annotations

from oxitest import Fixture


async def test_async_route(async_named: Fixture[str]) -> None:
    """The async route names this test."""
    assert async_named == "test_async_route", (
        "the async route resolves through _resolve_async_deps, a different "
        "caller of _resolve_deps than the sync path"
    )


async def test_async_second(async_named: Fixture[str]) -> None:
    """A second async test gets its own identity, not the first test's."""
    assert async_named == "test_async_second", (
        "one value shared by both async tests is the #1874 defect reached "
        "through the async caller"
    )
