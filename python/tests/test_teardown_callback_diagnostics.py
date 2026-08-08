"""Raw-appended teardown callbacks report when they raise (#1966).

A yield fixture's teardown failure has always been reported. A callback
appended raw — by ``ctx.addfinalizer`` or by any of the nine built-in
``ctx.teardown_stack.append`` sites — was drained under a blanket
``contextlib.suppress`` and vanished. The two are documented as equivalent
ways to register cleanup, so the difference was invisible until a cleanup
silently stopped running.
"""

from __future__ import annotations

from pathlib import Path

from tests import helpers

_CALLBACK = Path(__file__).parent / "data" / "teardown_callback_raises"
_STRAND = Path(__file__).parent / "data" / "teardown_undo_strand"


def test_a_raising_addfinalizer_callback_is_reported() -> None:
    """The silent-discard defect: no diagnostic, no status change, exit 0."""
    # Arrange / Act
    # --warnings is load-bearing: without it the diagnostic collapses to a
    # count and the text assertion below passes against any run at all.
    out, _err, _rc = helpers.run_oxitest(_CALLBACK, "--serial", "--warnings")

    # Assert
    assert "teardown callback '_boom' failed during" in out, (
        "a cleanup that silently stops running is the failure mode; naming "
        f"the callable is what lets the author find it\nstdout:\n{out}"
    )
    assert "test_addfinalizer_raises" in out, (
        "the diagnostic must name the test, at parity with the yield-fixture "
        f"case — without it the reader has no way to reproduce\nstdout:\n{out}"
    )


def test_the_run_is_not_aborted_by_a_raising_callback() -> None:
    """The fix stops the silence, it does not start propagating."""
    # Arrange / Act
    out, _err, rc = helpers.run_oxitest(_CALLBACK, "--serial", "--warnings")

    # Assert
    assert rc == 0, (
        "a failing teardown must not fail the run; turning a diagnostic into "
        f"an exit code would break every suite that has one\nstdout:\n{out}"
    )
    assert "2 passed" in out, (
        f"both tests must still pass on their own merits\nstdout:\n{out}"
    )


def test_a_yield_fixture_failure_is_not_also_reported_as_a_callback() -> None:
    """Double-wrapping must not turn one failure into two diagnostics.

    A yield fixture's teardown is already ``safe_teardown``-wrapped inside
    ``_unpack_sync``, so it returns normally and the outer wrap never sees
    the exception. This asserts that, rather than resting on the source read.
    """
    # Arrange / Act
    out, _err, _rc = helpers.run_oxitest(_CALLBACK, "--serial", "--warnings")

    # Assert
    assert "fixture 'loud' teardown failed during" in out, (
        "the yield arm must still report in the fixture wording — if it "
        f"stopped, this test would pass vacuously\nstdout:\n{out}"
    )
    assert "teardown callback 'loud'" not in out, (
        "the fixture's failure is handled inside the fixture wrapper; a "
        "second callback-worded line for the same failure means the outer "
        f"wrap is re-reporting a handled exception\nstdout:\n{out}"
    )


def test_a_raising_builtin_cleanup_is_reported() -> None:
    """Built-in cleanups are appended raw at nine sites; all nine were silent."""
    # Arrange / Act
    out, _err, _rc = helpers.run_oxitest(_STRAND, "--serial", "--warnings")

    # Assert
    assert "teardown callback 'Patcher.close' failed during" in out, (
        "a bound method keeps its qualified name, so the built-in that failed "
        f"is identified without touching any of the nine sites\nstdout:\n{out}"
    )


def test_a_failing_undo_does_not_strand_the_undos_behind_it() -> None:
    """``close`` aborted its loop on the first raise, leaking the rest.

    Measured before the fix: the env override registered before a failing
    ``chdir`` undo survived into the next test in the same worker, with the
    run reporting ``1 failed`` for a reason the author never wrote.
    """
    # Arrange / Act
    out, _err, rc = helpers.run_oxitest(_STRAND, "--serial", "--warnings")

    # Assert
    assert "2 passed" in out, (
        "both tests must pass: the second one fails if and only if the first "
        f"test's env override leaked past its own teardown\nstdout:\n{out}"
    )
    assert rc == 0, f"a reported teardown failure must not change the exit code\n{out}"
