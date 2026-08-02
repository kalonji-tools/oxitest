"""The ADR-0006 async guard parameter must have no default anywhere (#1876).

``test_is_async`` is a *guard* parameter: it decides whether
``AsyncFixtureAccessError`` is raised. It used to default to ``True`` — the
permissive value — at six declarations, so any resolution entry point that
omitted it got no enforcement, silently. The ``FixtureRef`` route in
``executor.py`` is the one that actually inherited the gap.

The signature cases below are the gate for that. Nothing else checks it: ruff
and ty are both happy with a defaulted keyword, and a behavioural test only
covers the routes that exist today, not the next one someone adds.

Sibling file for the *behaviour* this deliberately does not change:
``python/tests/data/async_guard_matrix/`` pins ADR-0006's dispatch table, where
``async`` with ``shared``/``session`` is legal for **either** test kind and the one
illegal cell is a sync test reaching a ``function``-lifetime async fixture.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import oxitest as oxi
from oxitest._bridge._fixture_session import FixtureSession, _SessionProtocol
from oxitest._bridge.proxy_ns import FixturesProxy, NamespaceProxy
from tests import helpers

_DATA_ROOT = Path(__file__).parent / "data"
_MATRIX = _DATA_ROOT / "async_guard_matrix"

#: ``ExitCode::Failure`` (``src/types/exit.rs``). The illegal cell fails inside
#: a running test, so the run completes and exits 1 rather than 3
#: (``CollectError``) or 4 (``UsageError``).
_EXIT_FAILURE = 1


@dataclass(frozen=True)
class GuardSite:
    """One declaration of the ``test_is_async`` guard parameter."""

    label: str
    target: Callable[..., Any]


@oxi.parametrize(
    protocol_in_namespace=GuardSite(
        "_SessionProtocol.get_fixture_in_namespace",
        _SessionProtocol.get_fixture_in_namespace,
    ),
    protocol_shortcut=GuardSite(
        "_SessionProtocol.get_fixture_shortcut",
        _SessionProtocol.get_fixture_shortcut,
    ),
    session_in_namespace=GuardSite(
        "FixtureSession.get_fixture_in_namespace",
        FixtureSession.get_fixture_in_namespace,
    ),
    session_shortcut=GuardSite(
        "FixtureSession.get_fixture_shortcut",
        FixtureSession.get_fixture_shortcut,
    ),
    namespace_proxy=GuardSite("NamespaceProxy.__init__", NamespaceProxy.__init__),
    fixtures_proxy=GuardSite("FixturesProxy.__init__", FixturesProxy.__init__),
)
def test_the_async_guard_parameter_has_no_default(case: GuardSite) -> None:
    """Every declaration forces its caller to state the test's kind."""
    # Act
    param = inspect.signature(case.target).parameters["test_is_async"]

    # Assert
    assert param.default is inspect.Parameter.empty, (
        f"{case.label} must force every caller to state the test's kind. A "
        f"default of True silently disables ADR-0006's refusal for any "
        f"resolution entry point added later — which is exactly how the "
        f"FixtureRef route in executor.py lost it, with nothing to notice"
    )


def test_a_sync_test_cannot_reach_an_async_fixture_by_fixture_ref() -> None:
    """The route that inherited the permissive default (#1876).

    ``FixtureRef`` resolution happens after ``resolve_for_test`` and lands in
    ``param_kwargs``, so ``AsyncDepGuardMiddleware`` never sees it as a
    coroutine — a namespaced async fixture arrived as an ``AsyncFixtureHandle``
    the sync test could do nothing with.
    """
    # Act
    stdout, stderr, rc = helpers.run_oxitest(
        _MATRIX / "illegal", "--warnings", cwd=str(_MATRIX / "illegal")
    )

    # Assert
    assert rc == _EXIT_FAILURE, (
        f"a sync test whose FixtureRef resolves to an async fixture must fail; "
        f"passing would mean the test body ran against an AsyncFixtureHandle "
        f"and its assertions proved nothing\nstdout:\n{stdout}\n"
        f"stderr:\n{stderr}"
    )
    assert "cannot be used by a sync test" in stdout, (
        "the failure must be the async-access diagnostic, not an AttributeError "
        "on the handle; the message is what tells the user to make the test "
        f"async rather than to go looking for a broken fixture\nstdout:\n{stdout}"
    )


def test_every_legal_async_cell_still_passes() -> None:
    """ADR-0006's dispatch table, both test kinds, all four lifetime tiers.

    The regression risk in #1876: a guard broad enough to catch the parameter
    route at *every* lifetime would also refuse the cells ADR-0006 routes
    through ``SharedAsyncManager.resolve()``, where the value is awaited on the
    shared session loop before the test starts.
    """
    # Act — under -n 2, because a worker re-registers every declaration home
    # and resolves the shared session loop in its own process; a tier that
    # only works in the coordinator would pass every serial assertion here.
    legal = _MATRIX / "legal"
    stdout, stderr, rc = helpers.run_oxitest(
        legal, "--warnings", "-n", "2", cwd=str(legal)
    )

    # Assert
    assert rc == 0, (
        f"async tests must reach an async fixture at every lifetime tier, and "
        f"sync tests must reach the three wider-than-function tiers; without "
        f"this, tightening the guard would look free\nstdout:\n{stdout}\n"
        f"stderr:\n{stderr}"
    )
    assert "7 passed" in stdout, (
        "all seven cells must run; a collection regression that silently "
        f"dropped half the matrix would still exit 0\nstdout:\n{stdout}"
    )
