"""The teardown-registration guard at the widest lifetime tier (#1952).

``testcontext_current`` covers the function tier, both registration routes, and
the worker path. This file covers the tier that one cannot: ``process``.

The distinction is not cosmetic. ``_current_teardown_node_id`` — the var the
guard originally keyed on — is set at exactly three sites (the function,
module and package drains) and at none of the wide ones, because no single node
owns a process boundary. A guard on that var reads False here, and the drop
stays as silent as it was before the fix. Only a test at this tier can tell the
two signals apart.
"""

from __future__ import annotations

from pathlib import Path

from oxitest import TempDir
from tests import helpers

_PROJECT = Path(__file__).parent / "data" / "teardown_registration_wide"


def test_a_process_lifetime_teardown_registration_is_reported(tmp: TempDir) -> None:
    """Registering from inside a process-lifetime teardown must not be silent."""
    # Arrange / Act
    # --warnings is load-bearing: without it the diagnostic collapses to a
    # count and the text assertion below passes against any run at all.
    run = helpers.run_with_event_log(_PROJECT, tmp, "TRW_LOG", "--serial", "--warnings")

    # Assert
    assert run.rc == 0, (
        f"the probe project must pass; rc={run.rc}\n{run.stdout}\n{run.stderr}"
    )
    assert "WIDE TEARDOWN END" in run.events, (
        "the process-lifetime teardown must run to completion — if it died "
        "partway the assertions below would be about a position that never "
        f"reached the registration; got {run.events}"
    )
    assert "WIDE LATE FINALIZER RAN" not in run.events, (
        "the callback is genuinely dropped at this tier, which is what makes "
        "the diagnostic the only signal. If it ever starts running, the "
        f"diagnostic below becomes wrong and must change with it; got {run.events}"
    )
    assert "teardown registration" in run.stdout, (
        "the drop must be reported at the process tier too. Nothing sets "
        "_current_teardown_node_id when the wide scopes drain, so a guard "
        "keyed on that var is silent here — this is the assertion that "
        f"distinguishes it from a guard on _in_teardown; stdout:\n{run.stdout}"
    )
