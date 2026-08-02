"""Shared test infrastructure for the oxitest test suite.

Holds only the shared ``fx`` fixtures. Test utility functions live in the
``tests.helpers`` package (#1787) — reached with ``from tests import
helpers``, called as ``helpers.<function>()``.
"""

from __future__ import annotations

import sys

import oxitest
from oxitest import Yields
from oxitest._bridge._diagnostic_collector import _diagnostic_collector_var
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge.plugin_loader import PluginRegistry
from oxitest._bridge.result import Diagnostic

fx = oxitest.Fixtures()


@fx.fixture
def fixture_session() -> Yields[FixtureSession]:
    """Provide a fresh ``FixtureSession`` with an empty registry."""
    session = FixtureSession([], PluginRegistry())
    yield session
    # Both rungs: the fixture owns the whole session, so it must leave nothing
    # behind — the task tiers and the process-lifetime ones (#1777).
    session.end_task()
    session.end_process()


@fx.fixture
def diag_collector() -> Yields[list[Diagnostic]]:
    """Provide a fresh diagnostic collector, wired to the ContextVar.

    Use in tests that call bridge code expecting ``emit_diagnostic()``
    to land somewhere.  Yields the list so tests can assert on it.
    """
    diags: list[Diagnostic] = []
    token = _diagnostic_collector_var.set(diags)
    yield diags
    _diagnostic_collector_var.reset(token)


@fx.fixture
def _clean_sys_modules() -> Yields[None]:
    """Snapshot-restore sys.modules — cleans up transitive imports a test triggers.

    Prefer ``helpers.install_module(ctx, name, module)`` for cleanup of a
    specific fake module a test installs by name. This fixture complements it
    by also cleaning up modules loaded transitively (e.g. through
    ``spec.loader.exec_module``).
    """
    saved = sys.modules.copy()
    yield
    for key in list(sys.modules):
        if key not in saved:
            del sys.modules[key]
    sys.modules.update({k: v for k, v in saved.items() if k not in sys.modules})
