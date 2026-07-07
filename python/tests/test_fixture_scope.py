from __future__ import annotations

from collections.abc import Callable

from oxitest._bridge._fixture_context import (
    _fixture_context,
    _fixture_scope,
)


def test_fixture_scope_sets_and_resets_context() -> None:
    """Context should be set inside the block and reset after."""
    assert _fixture_context.get(None) is None, "precondition: no active context"
    teardowns: list[Callable[[], None]] = []
    session = object()
    with _fixture_scope(session, "/test.py", teardowns):
        ctx = _fixture_context.get(None)
        assert ctx is not None, "context should be set inside _fixture_scope"
        assert ctx.session is session, "session should match"
        assert ctx.module_path == "/test.py", "module_path should match"
        assert ctx.fn_teardowns is teardowns, "fn_teardowns should match"
    assert _fixture_context.get(None) is None, "context should be reset after block"


def test_fixture_scope_resets_on_exception() -> None:
    """Context must be reset even if the block raises."""
    teardowns: list[Callable[[], None]] = []
    try:
        with _fixture_scope(object(), "/test.py", teardowns):
            msg = "boom"
            raise RuntimeError(msg)
    except RuntimeError:
        pass
    assert _fixture_context.get(None) is None, "context should be reset after exception"


def test_fixture_scope_uses_parent_teardowns_when_nested() -> None:
    """Nested _fixture_scope should use the parent's fn_teardowns."""
    outer_teardowns: list[Callable[[], None]] = []
    inner_teardowns: list[Callable[[], None]] = []
    session = object()
    with _fixture_scope(session, "/outer.py", outer_teardowns):
        with _fixture_scope(session, "/inner.py", inner_teardowns):
            ctx = _fixture_context.get(None)
            assert ctx is not None, "inner context should be set"
            assert ctx.fn_teardowns is outer_teardowns, (
                "nested scope should use parent's fn_teardowns, not its own"
            )
            assert ctx.module_path == "/inner.py", (
                "module_path should be the inner scope's"
            )


def test_fixture_scope_uses_own_teardowns_when_no_parent() -> None:
    """When there is no parent context, use the provided fn_teardowns directly."""
    teardowns: list[Callable[[], None]] = []
    with _fixture_scope(object(), "/test.py", teardowns):
        ctx = _fixture_context.get(None)
        assert ctx is not None, "context should be set"
        assert ctx.fn_teardowns is teardowns, (
            "without parent, should use provided fn_teardowns"
        )
