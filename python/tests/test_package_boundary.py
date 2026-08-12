"""Package-lifetime disposal happens at the package boundary (#1839).

Two sibling packages, each declaring its own ``lifetime="package"`` fixture.
That shape is the whole point: with a single package, "disposed when the
package ends" and "disposed when the run ends" are the same instant, so a
one-package suite passes either way — which is why ``slice3_package_lifetime``
stayed green while the boundary drain was a no-op.

The unit-level twin in ``test_package_lifetime_scope.py`` hands ``end_package``
the anchor directly, so only an end-to-end run covers the wiring that *chooses*
that argument.

Each shape is covered twice, sync and async, because #1839 had two independent
root causes: the Rust side passed the wrong key, and the async side never filed
a teardown under an anchor for that key to find. Either fix alone leaves the
other half firing at the end of the run.
"""

from __future__ import annotations

from itertools import groupby
from pathlib import Path

from oxitest import TempDir
from tests import helpers

_DATA = Path(__file__).parent / "data"
_PROJECT = _DATA / "package_boundary_order"
_NESTED = _DATA / "package_boundary_nested"
_PROJECT_ASYNC = _DATA / "package_boundary_order_async"
_NESTED_ASYNC = _DATA / "package_boundary_nested_async"

#: Which package runs first is a scheduling detail; that neither one's value
#: outlives its own package is not.
_ACCEPTABLE = (
    ("SETUP a", "USE a", "TEARDOWN a", "SETUP b", "USE b", "TEARDOWN b"),
    ("SETUP b", "USE b", "TEARDOWN b", "SETUP a", "USE a", "TEARDOWN a"),
)


def test_a_package_is_disposed_before_its_sibling_starts(tmp: TempDir) -> None:
    """Each package's teardown fires at its own boundary, not at run end."""
    # Act — --serial pins the coordinator path, the one where the delay is
    # unbounded: a worker's session covers a single task group, so the end-task
    # backstop there lands close enough to hide the defect.
    run = helpers.run_with_event_log(_PROJECT, tmp, "P2LOG", "--serial")

    # Assert
    assert run.rc == 0, (
        f"the data-project must pass; rc={run.rc}\nstdout:\n{run.stdout}\n"
        f"stderr:\n{run.stderr}"
    )
    assert run.events in _ACCEPTABLE, (
        f"each package-lifetime value must be disposed at its own package "
        f"boundary; got {run.events}. Both TEARDOWNs trailing at the end is the "
        f"signature of #1839 — the boundary drain missing and the end-of-task "
        f"backstop catching everything"
    )


def _blocks(events: tuple[str, ...]) -> list[str]:
    """Collapse a nested-project log to runs of the package each event belongs to.

    Which package the scheduler runs first is its own business — a persisted
    timing cache reorders them between runs — so the assertion has to be that
    neither package's events straddle the other's, not that a particular one
    came first. Two blocks means neither straddles.
    """
    return [
        label
        for label, _ in groupby(
            "zzz" if event.split()[1] == "other" else "api" for event in events
        )
    ]


def test_a_nested_declaring_package_is_disposed_inside_its_ancestors_boundary(
    tmp: TempDir,
) -> None:
    """A package nested in a declaring package dies at that boundary, first."""
    # Act
    run = helpers.run_with_event_log(_NESTED, tmp, "NESTLOG", "--serial")

    # Assert
    assert run.rc == 0, (
        f"the data-project must pass; rc={run.rc}\nstdout:\n{run.stdout}\n"
        f"stderr:\n{run.stderr}"
    )
    # `.index` raises an unmessaged ValueError when an event is missing, so
    # pin presence first — a reformatted or absent event would otherwise fail
    # pointing at nothing. "sees outer" also depends on FrozenProxy.__format__
    # forwarding (#1735); if that regressed, the event text changes silently.
    for required in ("TEARDOWN inner sees outer", "TEARDOWN outer", "SETUP other"):
        assert required in run.events, (
            f"{required!r} must appear at all — a missing event makes the "
            f"ordering assertions below vacuous; got {run.events}"
        )
    # The scheduler merges api and api/v1 into one group under api's anchor
    # alone, so only api's boundary is announced. Draining just that key leaves
    # api/v1's scope for the end-of-task backstop, which is #1839 one level
    # down: SETUP other would appear before TEARDOWN inner.
    assert _blocks(run.events) in (["api", "zzz"], ["zzz", "api"]), (
        f"the api subtree and the unrelated zzz package must each live and die "
        f"in one contiguous block; got {_blocks(run.events)} from {run.events}. "
        f"An api event after zzz's block means api/v1's scope was left to the "
        f"end-of-task backstop, which is #1839 one level down"
    )
    # Innermost first. The inner fixture holds the outer one's value, so the
    # reverse order would run this teardown against a disposed value — and it
    # would do so silently, since nothing about a torn-down str looks wrong.
    assert run.events.index("TEARDOWN inner sees outer") < run.events.index(
        "TEARDOWN outer"
    ), (
        f"a nested package's value must be disposed before the ancestor value "
        f"it was built on; got {run.events}"
    )


def test_an_async_package_is_disposed_before_its_sibling_starts(
    tmp: TempDir,
) -> None:
    """The async twin of the two-sibling ordering — a second root cause.

    Keying the Rust side correctly is necessary but not sufficient: an async
    teardown is filed under a boundary chosen when it is *registered*, and
    ``package`` was not one of the cases, so ``drain_boundary`` had nothing to
    find no matter what ``end_package`` was called with.

    Both packages declare both routes, so whichever runs first constrains both
    registration sites. One route per package would leave the last package's
    route unproven: its boundary and the end of the run are the same instant,
    which is exactly the blind spot that hid this bug in the first place.
    """
    # Act
    run = helpers.run_with_event_log(_PROJECT_ASYNC, tmp, "ASYNCLOG", "--serial")

    # Assert
    assert run.rc == 0, (
        f"the data-project must pass; rc={run.rc}\nstdout:\n{run.stdout}\n"
        f"stderr:\n{run.stderr}"
    )
    # Collapse the log to the package each event belongs to, then to runs of
    # that. Two blocks means each package's whole life — setup, use, teardown —
    # is contiguous. Anything that slid to the end-of-task backstop splits its
    # package into two blocks and lands the sibling's in between.
    blocks = [
        label
        for label, _ in groupby(event.split()[1].split("-")[0] for event in run.events)
    ]
    assert blocks in (["a", "b"], ["b", "a"]), (
        f"each package's async fixtures must live and die inside one "
        f"contiguous block; got blocks {blocks} from {run.events}. An "
        f"interleaved block means a teardown slid past its own package "
        f"boundary to the end-of-task backstop, which is #1839 for async"
    )


def test_a_nested_async_package_is_disposed_inside_its_ancestors_boundary(
    tmp: TempDir,
) -> None:
    """Nested-anchor disposal holds on the async route too."""
    # Act
    run = helpers.run_with_event_log(_NESTED_ASYNC, tmp, "NESTASYNCLOG", "--serial")

    # Assert
    assert run.rc == 0, (
        f"the data-project must pass; rc={run.rc}\nstdout:\n{run.stdout}\n"
        f"stderr:\n{run.stderr}"
    )
    for required in ("TEARDOWN inner", "TEARDOWN outer", "SETUP other"):
        assert required in run.events, (
            f"{required!r} must appear at all — a missing event makes the "
            f"ordering assertions below vacuous; got {run.events}"
        )
    assert _blocks(run.events) in (["api", "zzz"], ["zzz", "api"]), (
        f"the api subtree and the unrelated zzz package must each live and die "
        f"in one contiguous block; got {_blocks(run.events)} from {run.events}"
    )
    # Innermost first, structurally: api/v1 is nested inside api, so its value
    # must go first whether or not it holds a reference to api's. The sync twin
    # takes the dependency and proves the reference case; this one deliberately
    # does not — see its __fixtures__.py for why that would make the project
    # order-dependent rather than boundary-dependent.
    assert run.events.index("TEARDOWN inner") < run.events.index("TEARDOWN outer"), (
        f"a nested async package must be disposed before the ancestor package "
        f"it sits inside; got {run.events}"
    )


def test_a_nested_async_package_is_disposed_innermost_first_in_a_worker(
    tmp: TempDir,
) -> None:
    """Innermost-first holds on the parallel path too, where nothing calls end_package.

    A worker's session covers exactly one task group, so the coordinator never
    fires ``end_package`` for it and ``drain_task`` is the drain. That path
    ordered async teardowns by registration rather than by nesting, which put
    the ancestor's disposal first — the one order that can hand an inner
    generator a value that is already gone.

    Only the nesting assertion is made here. ``zzz`` runs in a different
    process, so its position in a shared log says nothing about ordering.
    """
    # Act
    run = helpers.run_with_event_log(
        _NESTED_ASYNC, tmp, "NESTASYNCLOG", "-n", "2", log_name="parallel.log"
    )

    # Assert
    assert run.rc == 0, (
        f"the data-project must pass; rc={run.rc}\nstdout:\n{run.stdout}\n"
        f"stderr:\n{run.stderr}"
    )
    for required in ("TEARDOWN inner", "TEARDOWN outer"):
        assert required in run.events, (
            f"{required!r} must appear at all — a missing event makes the "
            f"ordering assertion below vacuous; got {run.events}"
        )
    assert run.events.index("TEARDOWN inner") < run.events.index("TEARDOWN outer"), (
        f"under -n the worker drains its own package scopes, and it must still "
        f"dispose the nested package before the ancestor package it sits inside; "
        f"got {run.events}"
    )
