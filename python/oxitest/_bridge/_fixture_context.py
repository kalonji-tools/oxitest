"""ContextVar protocol for fixture resolution — stdlib-only leaf module.

Defines the per-test transient state (``TestRunContext``), the fixture
resolution context (``FixtureContext``), and the ``FixtureTeardownWarning``
warning class.  All symbols are re-exported by ``_fixture_session`` for
backward compatibility.

Zero oxitest imports — this module depends only on the standard library.
"""

from __future__ import annotations

__all__ = [
    "FixtureContext",
    "FixtureTeardownWarning",
    "TestRunContext",
    "_current_teardown_node_id",
    "_fixture_context",
    "_fixture_scope",
    "_test_run_context",
    "_warn_teardown",
]

import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

# ── Per-test teardown node ID ────────────────────────────────────────────────

_current_teardown_node_id: ContextVar[str] = ContextVar(
    "_current_teardown_node_id", default=""
)

# ── Per-test transient state ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TestRunContext:
    """Per-test transient state, set by executor around run_test."""

    keep_tmp: str | None = None
    result_cell: list[Any] | None = None


_test_run_context: ContextVar[TestRunContext | None] = ContextVar(
    "_test_run_context", default=None
)

# ── Fixture resolution context ───────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FixtureContext:
    """Context for fixture resolution during test execution.

    Bundles the session, module path, and per-test teardown list into a single
    ContextVar value, replacing the previous dual-state mechanism
    (_instantiation_context ContextVar + _teardown_local threading.local).
    """

    session: Any  # FixtureSession (avoiding circular import)
    module_path: str
    fn_teardowns: list[Callable[[], None]]


_fixture_context: ContextVar[FixtureContext | None] = ContextVar(
    "_fixture_context", default=None
)


@contextmanager
def _fixture_scope(
    session: Any,
    module_path: str,
    fn_teardowns: list[Callable[[], None]],
) -> Iterator[None]:
    """Scoped fixture context — handles parent lookup and guaranteed reset."""
    parent = _fixture_context.get(None)
    effective = parent.fn_teardowns if parent is not None else fn_teardowns
    token = _fixture_context.set(FixtureContext(session, module_path, effective))
    try:
        yield
    finally:
        _fixture_context.reset(token)


# ── Teardown warning ─────────────────────────────────────────────────────────


class FixtureTeardownWarning(UserWarning):
    """Emitted when an exception occurs inside a yield-fixture teardown.

    Captured by `WarnCapture` when a test annotates `warn: WarnCapture`.
    """


def _warn_teardown(name: str, exc: Exception, *, node_id: str = "") -> None:
    effective_id = node_id or _current_teardown_node_id.get()
    if name and effective_id:
        msg = f"fixture '{name}' teardown failed during {effective_id}: {exc}"
    elif name:
        msg = f"error in teardown of fixture '{name}': {exc}"
    else:
        msg = f"error during teardown: {exc}"
    warnings.warn(FixtureTeardownWarning(msg), stacklevel=2)
