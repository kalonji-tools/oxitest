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
    "_in_teardown",
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

# ── Teardown window ──────────────────────────────────────────────────────────

#: True while *any* teardown loop is running, at any lifetime tier.
#:
#: Deliberately separate from ``_current_teardown_node_id`` above, which answers
#: a different question — *which node's* teardown is running — and exists for
#: error attribution (#618). The two coincide at the function, module and
#: package tiers and diverge at the process, shared and session tiers, where
#: nothing sets a node id because no single node owns the boundary. Keying "am
#: I inside a teardown?" off the node id therefore reads False for three whole
#: tiers, which is measurably wrong (#1952) rather than merely imprecise.
_in_teardown: ContextVar[bool] = ContextVar("_in_teardown", default=False)

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


def _callback_name(fn: Callable[[], None]) -> str:
    """Best-effort display name for a raw-appended teardown callback.

    ``__qualname__`` carries the enclosing scope for a closure
    (``A.<locals>._x``), which is noise in a diagnostic — the trailing segment
    is the part the author recognises. A bound method has no ``<locals>`` and
    survives whole, so ``Patcher.close`` reads as itself.
    """
    qualname = getattr(fn, "__qualname__", "")
    return qualname.rsplit(">.", 1)[-1]


def _warn_callback_teardown(name: str, exc: Exception) -> None:
    """Report a raw-appended teardown callback that raised.

    Separate from ``_warn_teardown`` rather than a mode flag on it: these
    callbacks are not fixtures — ``ctx.addfinalizer`` registers plain
    callables and the built-ins append bound methods — so borrowing the
    fixture wording would be false. ``safe_teardown`` takes a ``warn``
    callback for exactly this.
    """
    node_id = _current_teardown_node_id.get()
    who = f"teardown callback '{name}'" if name else "a teardown callback"
    # Empty at the process tier: nothing sets the node id there, because no
    # single node owns that boundary (#1952).
    where = f" during {node_id}" if node_id else ""
    emit_diagnostic(
        DiagnosticSeverity.WARNING, "fixture teardown", f"{who} failed{where}: {exc}"
    )
