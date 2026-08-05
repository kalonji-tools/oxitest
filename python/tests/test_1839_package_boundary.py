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
    # The scheduler merges api and api/v1 into one group under api's anchor
    # alone, so only api's boundary is announced. Draining just that key leaves
    # api/v1's scope for the end-of-task backstop, which is #1839 one level
    # down: SETUP other would appear before TEARDOWN inner.
    assert run.events.index("TEARDOWN inner sees outer") < run.events.index(
        "SETUP other"
    ), (
        f"the nested package must be disposed at its ancestor's boundary, "
        f"before an unrelated package starts; got {run.events}"
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


def _package_window(events: tuple[str, ...], label: str) -> tuple[int, int]:
    """First SETUP and last TEARDOWN index for package *label*.

    The span during which that package held a value. Two packages' spans
    overlapping is the defect: it means one package's value was still alive
    while another package's tests ran.
    """
    setups = [i for i, e in enumerate(events) if e.startswith(f"SETUP {label}-")]
    teardowns = [i for i, e in enumerate(events) if e.startswith(f"TEARDOWN {label}-")]
    assert setups and teardowns, (
        f"package {label!r} must both build and dispose its fixtures — a "
        f"missing end makes the overlap check below vacuous; got {events}"
    )
    return min(setups), max(teardowns)


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
    a_start, a_end = _package_window(run.events, "a")
    b_start, b_end = _package_window(run.events, "b")
    assert a_end < b_start or b_end < a_start, (
        f"the two packages' async values must not be alive at the same time — "
        f"pkg_a held [{a_start}, {a_end}], pkg_b held [{b_start}, {b_end}] in "
        f"{run.events}. Overlap means a teardown slid past its own package "
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
    assert run.events.index("TEARDOWN inner sees outer") < run.events.index(
        "SETUP other"
    ), (
        f"the nested async package must be disposed at its ancestor's "
        f"boundary, before an unrelated package starts; got {run.events}"
    )
    # An async generator's post-yield half is resumed later than a sync one's,
    # so the wrong order here resumes it against a disposed value rather than
    # merely reading one — the use-after-teardown shape #1777 hit.
    assert run.events.index("TEARDOWN inner sees outer") < run.events.index(
        "TEARDOWN outer"
    ), (
        f"a nested async package's value must be disposed before the ancestor "
        f"value it closes over; got {run.events}"
    )
