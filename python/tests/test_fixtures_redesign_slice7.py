"""Slice-7 acceptance: shortcut access ``fx.<name>`` (#1714).

Runs oxitest as a subprocess. Shortcut resolution happens inside a running
test's fixture resolution, so the assertions have to be about a run rather than
about registry state.

**No strict dial is asserted anywhere in this file, and that is the point.**
ADR-0009 Rule 5 originally gated the shortcut behind ``strict = "off" | "warn"
| "abort"``; Amendment 3 retracted it, because ``"warn"`` was never a
``StrictMode`` value and the dial's value never crosses into Python. The
shortcut is unconditionally legal, so the data projects run at their own
``strict = "abort"`` — which would have been the *forbidding* position under
the original spec — and pass.

Every project is re-run under ``-n 2``, following slice 6. Shortcut resolution
reads the same B1-filtered catalog that route depends on, and that catalog is
only correct in a worker because ``worker.py`` re-registers every declaration
home. A regression there degrades nearest-ancestor-wins into
last-registered-wins **in parallel only**, invisible to every serial assertion
here.

``-n N`` forces the parallel path regardless of test count, so these projects
deliberately leave ``min_parallel_tests`` at its default. Lowering it would
make the "serial" half of each parametrize case run through workers too, and
this file would then have no serial coverage at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import oxitest as oxi
from oxitest import helpers
from oxitest._bridge._builtins._base import BuiltinFixture

_DATA_ROOT = Path(__file__).parent / "data"
_LEGAL = _DATA_ROOT / "slice7_shortcut"
_CROSS = _DATA_ROOT / "slice7_shortcut_cross"
_ASYNC_SYNC = _DATA_ROOT / "slice7_shortcut_async_sync"
_FOREIGN_INLINE = _DATA_ROOT / "slice7_shortcut_foreign_inline"

#: 5 in api/test_api.py + 2 in api/v1/test_v1.py.
_LEGAL_TESTS = 7

#: ``ExitCode::Failure`` (``src/types/exit.rs``). Both violation projects fail
#: inside a running test, so the run completes and exits 1. Pinning the exact
#: code matters because 3 is ``CollectError`` and 4 is ``UsageError`` — a
#: regression that moved either violation to collection time would still be
#: non-zero while silently changing what CI is told.
_EXIT_FAILURE = 1


@dataclass(frozen=True)
class RunMode:
    """Serial versus parallel, as one parametrize case."""

    label: str
    args: tuple[str, ...]


_SERIAL = RunMode(label="serial", args=())
_PARALLEL = RunMode(label="-n 2", args=("-n", "2"))


@oxi.parametrize(serial=_SERIAL, parallel=_PARALLEL)
def test_the_legal_tree_passes_whole(case: RunMode) -> None:
    """Shortcut, nearest-wins, segment shadowing, and the async await."""
    # Act
    stdout, stderr, rc = helpers.common.run_oxitest(_LEGAL, *case.args, cwd=str(_LEGAL))

    # Assert
    assert rc == 0, (
        f"the legal tree must pass under {case.label}; a proxy that refused "
        f"every shortcut would satisfy the two violation tests below just as "
        f"well as a correct one, so this is what separates them\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert f"{_LEGAL_TESTS} passed" in stdout, (
        f"all {_LEGAL_TESTS} tests must run under {case.label}; a collection "
        f"regression that silently dropped the v1 subtree would still exit 0 "
        f"with fewer tests\nstdout:\n{stdout}"
    )


@oxi.parametrize(serial=_SERIAL, parallel=_PARALLEL)
def test_the_shortcut_does_not_cross_a_b1_boundary(case: RunMode) -> None:
    """A bare name reports not-found, never the sibling's fixture."""
    # Act
    stdout, stderr, rc = helpers.common.run_oxitest(_CROSS, *case.args, cwd=str(_CROSS))

    # Assert
    assert rc == _EXIT_FAILURE, (
        f"a cross-boundary shortcut must fail the run under {case.label}; if "
        f"it passed, the shortcut would be a B1 bypass rather than a spelling "
        f"convenience\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "1 passed" in stdout, (
        f"the anchor package's own shortcut must still succeed under "
        f"{case.label}; without that, the sibling's failure could just as "
        f"easily mean the fixture never registered\nstdout:\n{stdout}"
    )
    assert "fixture-boundary" not in stdout, (
        "a bare-name lookup has no segment to attribute the boundary to, so "
        "it must report as not-found rather than as BoundaryError — #1713 "
        "made that split deliberately and the shortcut inherits it\n"
        f"stdout:\n{stdout}"
    )


@oxi.parametrize(serial=_SERIAL, parallel=_PARALLEL)
def test_a_sync_test_cannot_shortcut_to_an_async_fixture(case: RunMode) -> None:
    """The illegal cell, on the route that middleware cannot see."""
    # Act
    stdout, stderr, rc = helpers.common.run_oxitest(
        _ASYNC_SYNC, *case.args, cwd=str(_ASYNC_SYNC)
    )

    # Assert
    assert rc == _EXIT_FAILURE, (
        f"a sync test reaching an async fixture by shortcut must fail under "
        f"{case.label}; AsyncDepGuardMiddleware inspects resolved kwargs and "
        f"cannot see a proxy access in the test body, so this route needs its "
        f"own guard\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "1 passed" in stdout, (
        f"the async test alongside it must still pass under {case.label}, or "
        f"the sync failure proves only that the fixture is broken\n"
        f"stdout:\n{stdout}"
    )
    assert "cannot be used by a sync test" in stdout, (
        "the failure must be the async-access diagnostic, not an arbitrary "
        "AttributeError or an un-awaited-coroutine warning; the message is "
        "what tells the user to make the test async\n"
        f"stdout:\n{stdout}"
    )


def test_the_not_found_diagnostic_does_not_vary_with_scheduling() -> None:
    """One wording for every miss, serially and under ``-n``.

    Regression test. The first cut of the shortcut branch consulted the
    *unfiltered* catalog to decide whether the name was a fixture at all, so it
    could choose a more specific message. That catalog contains inline
    declarations, and those register only in the worker that imported their
    module — so the same source produced "fixture 'x' not found" serially and
    "no fixture or fixture namespace 'x'" under ``-n 2``, the second of which
    was also false: the fixture existed, just not here.

    ADR-0009 Rule 5 rules out exactly this — a diagnostic that depends on
    worker assignment. Pinning the two runs against each other is the only way
    to catch it, because either message read alone looks entirely reasonable.
    """
    # Act
    serial_out, _, serial_rc = helpers.common.run_oxitest(
        _FOREIGN_INLINE, cwd=str(_FOREIGN_INLINE)
    )
    parallel_out, _, parallel_rc = helpers.common.run_oxitest(
        _FOREIGN_INLINE, "-n", "2", cwd=str(_FOREIGN_INLINE)
    )

    # Assert
    assert serial_rc == _EXIT_FAILURE and parallel_rc == _EXIT_FAILURE, (
        f"a foreign inline fixture must be unreachable in both modes; got "
        f"serial={serial_rc}, parallel={parallel_rc}"
    )
    marker = "cannot resolve fixture 'inline_only'"
    assert marker in serial_out and marker in parallel_out, (
        f"both runs must produce the same shortcut not-found wording; a "
        f"catalog-dependent branch reintroduces a message that changes with "
        f"worker assignment\nserial:\n{serial_out}\nparallel:\n{parallel_out}"
    )


def test_no_builtin_is_reachable_by_shortcut() -> None:
    """Pin the coupling that keeps ``fx.oxi.<name>`` the only builtin spelling.

    Builtins land in the same bare-name index the shortcut reads, and they are
    B1-exempt, so ``get_visible`` reports them visible everywhere. They stay
    unreachable only because ``FixtureSession`` registers them under
    ``impl_cls.__name__`` — the *private* implementation class
    (``_TempDirFixture``, not ``TempDir``) — and ``FixturesProxy.__getattr__``
    rejects underscore-prefixed names on its first line.

    That is a naming convention doing a predicate's job, the same shape as
    #1768. Renaming those classes would silently open a second, undocumented
    spelling for every builtin and collide with any user fixture of the same
    name — which is exactly what the reserved ``oxi`` namespace exists to
    prevent. This test fails if the convention goes.
    """
    # Arrange
    BuiltinFixture.ensure_registered()

    # Act
    registered_names = [
        impl_cls.__name__ for impl_cls in BuiltinFixture.registered_types().values()
    ]

    # Assert
    assert registered_names, (
        "no builtins registered at all — the assertion below would hold "
        "vacuously and stop guarding anything"
    )
    leaked = [name for name in registered_names if not name.startswith("_")]
    assert not leaked, (
        f"builtin implementation classes must stay underscore-prefixed so the "
        f"proxy's leading-underscore guard keeps them out of shortcut form; "
        f"{leaked} would each become a working, undocumented alias for a "
        f"framework fixture. If this rename is intentional, add an explicit "
        f"BuiltinSource filter to the shortcut branch instead (see #1768)"
    )
