"""Package-lifetime disposal happens at the package boundary (#1839).

Two sibling packages, each declaring its own ``lifetime="package"`` fixture.
That shape is the whole point: with a single package, "disposed when the
package ends" and "disposed when the run ends" are the same instant, so a
one-package suite passes either way — which is why ``slice3_package_lifetime``
stayed green while the boundary drain was a no-op.

The unit-level twin in ``test_package_lifetime_scope.py`` hands ``end_package``
the anchor directly, so it pins the mechanism while leaving the wiring that
chooses the argument unexercised. This suite runs the real binary end to end
and therefore covers exactly that wiring.
"""

from __future__ import annotations

from pathlib import Path

from oxitest import TempDir
from tests import helpers

_PROJECT = Path(__file__).parent / "data" / "package_boundary_order"
_LOG_VAR = "P2LOG"

#: The two orderings the scheduler may legitimately choose. Which package runs
#: first is a scheduling detail; that neither one's value outlives its own
#: package is not.
_ACCEPTABLE = (
    ("SETUP a", "USE a", "TEARDOWN a", "SETUP b", "USE b", "TEARDOWN b"),
    ("SETUP b", "USE b", "TEARDOWN b", "SETUP a", "USE a", "TEARDOWN a"),
)


def test_a_package_is_disposed_before_its_sibling_starts(tmp: TempDir) -> None:
    """Each package's teardown fires at its own boundary, not at run end."""
    # Act — --serial pins the coordinator path, the one where the delay is
    # unbounded: a worker's session covers a single task group, so the end-task
    # backstop there lands close enough to hide the defect.
    run = helpers.run_with_event_log(_PROJECT, tmp, _LOG_VAR, "--serial")

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
