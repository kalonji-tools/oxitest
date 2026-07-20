"""Tests for resolve_strategy — the Optional-collapse adapter for SessionStrategy."""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any, TypeVar

from oxitest._bridge._async_backend import AsyncioBackend
from oxitest._bridge._middleware import (
    Arrange,
    Fresh,
    Shared,
    resolve_strategy,
)

_T = TypeVar("_T")


class _StubSession:
    """Minimal stand-in for AsyncSession — resolve_strategy only stores references.

    run() is never called by resolve_strategy but must exist to satisfy the
    AsyncSession Protocol for ty.
    """

    def run(self, _coro: Coroutine[Any, Any, _T], /) -> _T:
        """Stub — never called by resolve_strategy."""
        msg = "_StubSession.run should not be called in resolve_strategy tests"
        raise AssertionError(msg)


def test_shared_wins_when_used_shared_and_present() -> None:
    """Shared session wins over arrange when used_shared=True and shared is not None."""
    shared = _StubSession()
    arrange = _StubSession()
    backend = AsyncioBackend()
    result = resolve_strategy(
        used_shared=True, shared=shared, arrange=arrange, default_backend=backend
    )
    assert isinstance(result, Shared), (
        "when a shared session is present and used, it must win over arrange"
    )
    assert result.session is shared, "Shared.session must reference the shared instance"


def test_arrange_wins_when_used_shared_false() -> None:
    """Arrange wins when used_shared is False, even if a shared session is provided."""
    arrange = _StubSession()
    backend = AsyncioBackend()
    result = resolve_strategy(
        used_shared=False,
        shared=_StubSession(),
        arrange=arrange,
        default_backend=backend,
    )
    assert isinstance(result, Arrange), (
        "when used_shared is False, an unused shared reference must be ignored"
    )
    assert result.session is arrange, (
        "Arrange.session must reference the arrange instance"
    )


def test_arrange_only() -> None:
    """Arrange-only input (no shared, used_shared=False) yields Arrange."""
    arrange = _StubSession()
    backend = AsyncioBackend()
    result = resolve_strategy(
        used_shared=False, shared=None, arrange=arrange, default_backend=backend
    )
    assert isinstance(result, Arrange), "arrange-only input must yield Arrange"
    assert result.session is arrange, (
        "Arrange.session must reference the arrange instance"
    )


def test_fresh_when_nothing() -> None:
    """No shared or arrange session falls through to Fresh(default_backend)."""
    backend = AsyncioBackend()
    result = resolve_strategy(
        used_shared=False, shared=None, arrange=None, default_backend=backend
    )
    assert isinstance(result, Fresh), (
        "no shared or arrange session must fall through to Fresh(default_backend)"
    )
    assert result.backend is backend, "Fresh.backend must reference the default_backend"


def test_used_shared_true_but_shared_none_falls_back_to_arrange() -> None:
    """used_shared=True with shared=None falls back to Arrange rather than raising."""
    arrange = _StubSession()
    backend = AsyncioBackend()
    result = resolve_strategy(
        used_shared=True, shared=None, arrange=arrange, default_backend=backend
    )
    assert isinstance(result, Arrange), (
        "used_shared=True with shared=None cannot yield Shared — must fall through"
    )
    assert result.session is arrange, (
        "Arrange.session must reference the arrange instance"
    )


def test_used_shared_true_all_none_falls_all_the_way_to_fresh() -> None:
    """used_shared=True with shared=None and arrange=None falls through to Fresh.

    Guards against a bug where the used_shared flag would short-circuit before
    reaching the final Fresh(default_backend) return.
    """
    backend = AsyncioBackend()
    result = resolve_strategy(
        used_shared=True, shared=None, arrange=None, default_backend=backend
    )
    assert isinstance(result, Fresh), (
        "used_shared=True with everything else None must fall through to Fresh"
    )
    assert result.backend is backend, "Fresh.backend must reference the default_backend"
