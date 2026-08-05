"""Package-lifetime disposal happens at the package boundary (#1839).

Two sibling packages, each declaring its own ``lifetime="package"`` fixture.
That shape is the whole point: with a single package, "disposed when the
package ends" and "disposed when the run ends" are the same instant, so a
one-package suite passes either way — which is why ``slice3_package_lifetime``
stayed green while the boundary drain was a no-op.

The unit-level twin in ``test_package_lifetime_scope.py`` hands ``end_package``
the anchor directly, so only an end-to-end run covers the wiring that *chooses*
that argument.
"""

from __future__ import annotations

from pathlib import Path

from oxitest import TempDir
from tests import helpers

_DATA = Path(__file__).parent / "data"
_PROJECT = _DATA / "package_boundary_order"
_NESTED = _DATA / "package_boundary_nested"

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
