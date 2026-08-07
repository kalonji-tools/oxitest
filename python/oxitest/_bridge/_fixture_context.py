"""ContextVar protocol for fixture resolution — stdlib-only leaf module.

Defines the per-test transient state (``TestRunContext``), the fixture
resolution context (``FixtureContext``), and the teardown diagnostic
helper.  All symbols are re-exported by ``_fixture_session`` for
backward compatibility.
"""

from __future__ import annotations

__all__ = [
    "FixtureContext",
    "TestRunContext",
    "_current_teardown_node_id",
    "_fixture_context",
    "_fixture_scope",
    "_test_run_context",
    "_warn_teardown",
]
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oxitest._bridge._test_meta import TestMeta

from oxitest._bridge._diagnostic_collector import emit_diagnostic
from oxitest._bridge.result import DiagnosticSeverity

# ── Per-test teardown node ID ────────────────────────────────────────────────

_current_teardown_node_id: ContextVar[str] = ContextVar(
    "_current_teardown_node_id", default=""
)

# ── Per-test transient state ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TestRunContext:
    """Per-test transient state, set by executor around run_test.

    ``meta`` and ``fn_teardowns`` exist so ``TestContext.current()`` can build
    a context without injection (#1949). ``meta is None`` is precisely what
    "outside a test" means — the default instance carries no identity, so
    import-time and post-reset positions are distinguishable for free.
    """

    keep_tmp: str = "cleanup"
    result_cell: list[Any] = field(default_factory=list)
    meta: TestMeta | None = None
    fn_teardowns: list[Callable[[], None]] = field(default_factory=list)


_DEFAULT_TEST_RUN_CONTEXT: TestRunContext = TestRunContext()

_test_run_context: ContextVar[TestRunContext] = ContextVar(
    "_test_run_context", default=_DEFAULT_TEST_RUN_CONTEXT
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


# ── Teardown diagnostic ──────────────────────────────────────────────────────


def _warn_teardown(name: str, exc: Exception, *, node_id: str = "") -> None:
    effective_id = node_id or _current_teardown_node_id.get()
    if name and effective_id:
        msg = f"fixture '{name}' teardown failed during {effective_id}: {exc}"
    elif name:
        msg = f"error in teardown of fixture '{name}': {exc}"
    else:
        msg = f"error during teardown: {exc}"
    emit_diagnostic(DiagnosticSeverity.WARNING, "fixture teardown", msg)
