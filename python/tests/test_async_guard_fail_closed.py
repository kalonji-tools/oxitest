"""The ADR-0006 async guard parameter must have no default anywhere (#1876).

``test_is_async`` is a *guard* parameter: it decides whether
``AsyncFixtureAccessError`` is raised. It used to default to ``True`` — the
permissive value — at six declarations, so any resolution entry point that
omitted it got no enforcement, silently. The ``FixtureRef`` route in
``executor.py`` is the one that actually inherited the gap.

The signature cases below are the gate for that. Nothing else checks it: ruff
and ty are both happy with a defaulted keyword, and a behavioural test only
covers the routes that exist today, not the next one someone adds.

``python/tests/data/async_guard_matrix/`` pins the *behaviour*, and ADR-0006
Amendment 2 is what it now pins:

- On the **parameter** routes — ``Fixture[T]`` and ``FixtureRef`` — an async
  fixture is legal for either test kind at any lifetime wider than
  ``function``. The value was awaited on the shared session loop before the
  test started, so there is nothing left to await.
- On the **proxy** route — ``await fx.<name>`` — a sync test is refused at
  *every* lifetime. The proxy returns a handle only ``await`` can unwrap.
- ``function`` lifetime stays illegal for a sync test on every route.

The lifetimes above are ``Lifetime`` values, which is what a user writes. This
docstring named ``shared`` and ``session`` until Amendment 2. Those are
``FixtureScope`` spellings, ``shared`` has not existed since #1720, and neither
is accepted by ``@oxi.fixture``.
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

#: ``ExitCode::UsageError`` (``src/types/exit.rs``). Every cell in the illegal
#: project fails inside a *running* test, so the run completes rather than
#: refusing at collection — but ``AsyncFixtureAccessError`` is one of the
#: errors that votes ``UsageError`` (#1761), so the run's code is 4 rather
#: than the 1 a plain assertion failure would give.
#:
#: This was 1 until ADR-0006 Amendment 2. The project then held only the
#: ``FixtureRef`` cell, which is refused by ``AsyncDepGuardMiddleware`` and
#: does not vote. Adding the proxy cells changed the run's verdict, measured,
#: not the meaning of the test.
_EXIT_USAGE_ERROR = 4


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


def test_the_cells_that_stay_illegal_still_refuse() -> None:
    """The two refusals ADR-0006 Amendment 2 keeps, and their split (#1876).

    Amendment 2 makes the parameter routes legal above ``function`` lifetime
    and leaves two things illegal. They fail for different reasons, and the
    project asserts both:

    - a ``FixtureRef`` to a ``function``-lifetime async fixture, refused by
      ``AsyncDepGuardMiddleware`` because the value is an un-advanced coroutine;
    - a **proxy** access at ``module`` and at ``package``, refused by
      ``AsyncFixtureAccessError`` because the proxy returns a handle only
      ``await`` can unwrap.

    The proxy pair is the load-bearing half. It is the only assertion that the
    proxy stays *stricter* than ADR-0006's own cell, and without it a change
    that relaxed the proxy would leave every gate green.
    """
    # Act
    stdout, stderr, rc = helpers.run_oxitest(
        _MATRIX / "illegal", "--warnings", cwd=str(_MATRIX / "illegal")
    )

    # Assert
    assert rc == _EXIT_USAGE_ERROR, (
        f"four cells must refuse a sync test, and AsyncFixtureAccessError votes "
        f"UsageError; passing would mean a test body ran against a coroutine or "
        f"nothing\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "cannot be used by a sync test" in stdout, (
        "the proxy failure must be the async-access diagnostic, not an "
        "AttributeError on the handle; the message is what tells the user to "
        f"make the test async rather than to hunt a broken fixture\n"
        f"stdout:\n{stdout}"
    )
    assert "6 errors · 1 passed" in stdout, (
        "two FixtureRef cases and four proxy cells must each refuse, and the "
        "async control must pass. A lower error count means a cell became "
        "legal silently, and the run's exit code alone cannot tell which one. "
        "The summary line is read rather than the 'ERROR' headers, because a "
        f"parametrized failure is reported under one header\nstdout:\n{stdout}"
    )
    assert "Lifetime:    function" in stdout, (
        "the diagnostic must name the Lifetime the user wrote. It printed "
        "'each', which is the FixtureScope spelling and is not a value "
        f"@oxi.fixture accepts (ADR-0006 Amendment 2)\nstdout:\n{stdout}"
    )
    assert "Two ways forward" in stdout, (
        "at function lifetime the diagnostic must offer two exits, not three. "
        "Raising the lifetime is what makes the parameter route work and never "
        "helps on the proxy route — measured refusing at function, module and "
        f"package\nstdout:\n{stdout}"
    )
    assert "Take it as a parameter instead" in stdout, (
        "above function lifetime the third exit must be the parameter route, "
        "which is the cell Amendment 2 declares legal. Without this the "
        f"diagnostic contradicts the decision it sits under\nstdout:\n{stdout}"
    )
    assert "Raise the fixture's lifetime" not in stdout, (
        "the removed hint must not come back on this route at any tier; it "
        f"sent the reader to a change that cannot help them\nstdout:\n{stdout}"
    )
    assert "Accessed as: fx.agi.wide_module" in stdout, (
        "the *qualified* proxy spelling has its own guard site — "
        "get_fixture_in_namespace, not get_fixture_shortcut — and this is the "
        "only assertion that reaches it. The count and the shared substring "
        "above cannot: relaxing this site alone lets the cell fall through to "
        "an AsyncFixtureHandle, whose AttributeError on `.label` still errors "
        "and still leaves 'cannot be used by a sync test' in the output from "
        f"the shortcut cells. A mutation proved it survives them\n"
        f"stdout:\n{stdout}"
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
    assert "9 passed" in stdout, (
        "all nine cells must run — seven parameter cells plus the two "
        "FixtureRef cases Amendment 2 made legal; a collection regression that "
        f"silently dropped half the matrix would still exit 0\nstdout:\n{stdout}"
    )
