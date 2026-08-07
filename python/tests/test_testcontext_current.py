"""Position rule for TestContext.current() (#1949).

Six positions, one test each. The discriminators all pre-date this feature:
_test_run_context carries the identity, _fixture_context marks fixture
resolution, and _current_teardown_node_id marks the function-teardown window.
"""

from __future__ import annotations

from pathlib import Path

import oxitest as oxi
from oxitest import TempDir
from oxitest._bridge._errors import TestContextUnavailableError
from oxitest._bridge._fixture_context import (
    _DEFAULT_TEST_RUN_CONTEXT,
    TestRunContext,
    _test_run_context,
)
from oxitest._bridge._test_meta import TestMeta
from tests import helpers

_PROJECT = Path(__file__).parent / "data" / "testcontext_current"
_LEAK_PROJECT = Path(__file__).parent / "data" / "testcontext_leak"


def test_run_context_carries_the_test_identity() -> None:
    """TestRunContext holds the meta and teardown list current() reads."""
    # Arrange
    meta = TestMeta(
        module_path="/t.py",
        fn_name="test_x",
        node_id="/t.py::test_x",
    )

    # Act
    run_ctx = TestRunContext(meta=meta)

    # Assert
    assert run_ctx.meta is meta, (
        "TestRunContext must carry the running test's meta — it is the only "
        "state current() can read to answer 'which test is this'"
    )
    assert run_ctx.fn_teardowns == [], (
        "TestRunContext must carry a teardown list so an ambient on_teardown "
        "has somewhere to append without the caller holding the list"
    )


def test_the_error_names_the_position_it_refused() -> None:
    """The refusal message says which position rejected the call."""
    # Arrange / Act
    err = TestContextUnavailableError("inside a fixture body")

    # Assert
    assert "inside a fixture body" in str(err), (
        "the message must name the position — #1874's precedent is that a "
        "refusal which does not say where it fired sends the user hunting"
    )


def test_current_returns_the_running_test_from_a_test_body() -> None:
    """current() names the test whose body called it."""
    # Arrange / Act
    ctx = oxi.TestContext.current()

    # Assert
    assert ctx.name == "test_current_returns_the_running_test_from_a_test_body", (
        "current() must name the test it was called from — an ambient reader "
        "that names the wrong test is the #1874 failure mode with a new spelling"
    )


def test_current_refuses_outside_a_test() -> None:
    """With no identity in the context var, current() refuses rather than guesses."""
    # Arrange
    token = _test_run_context.set(_DEFAULT_TEST_RUN_CONTEXT)

    # Act / Assert
    try:
        with oxi.raises(TestContextUnavailableError):
            oxi.TestContext.current()
    finally:
        _test_run_context.reset(token)


def test_every_position_behaves_under_wide_async_promotion(tmp: TempDir) -> None:
    """All six positions, with the body promoted onto the shared session loop."""
    # Arrange / Act
    # --warnings is load-bearing: without it the diagnostic is collapsed to a
    # count and the text assertion below would pass vacuously.
    run = helpers.run_with_event_log(_PROJECT, tmp, "TCC_LOG", "--serial", "--warnings")

    # Assert
    assert run.rc == 0, (
        f"the probe project must pass; rc={run.rc}\n{run.stdout}\n{run.stderr}"
    )
    assert "FIXTURE_SETUP raised" in run.events, (
        "a fixture body has no running test to describe, so current() must "
        f"refuse there rather than name the fixture; got {run.events}"
    )
    assert "WIDE_TEARDOWN raised" in run.events, (
        "a process-lifetime teardown fires after run_test reset the context, "
        f"so there is no current test to return; got {run.events}"
    )
    assert "BODY test_alpha" in run.events, (
        "the promoted async body runs as a Task on the shared session loop — "
        f"the ContextVar must survive that hop; got {run.events}"
    )
    assert "HELPER test_alpha" in run.events, (
        "a plain imported function must reach the same identity as the body; "
        f"that capability gap is the whole reason for current(); got {run.events}"
    )
    assert "TEARDOWN reached" in run.events, (
        f"on_teardown registered ambiently must actually run; got {run.events}"
    )
    assert "ALIAS TEARDOWN reached" in run.events, (
        "current_test() builds a fresh TestContext per call, so it must hand "
        "back the run's shared teardown list — a private one would make "
        f"finalizers registered through the alias vanish; got {run.events}"
    )
    assert "silently dropped" in run.stdout, (
        "calling current() from a teardown must emit a diagnostic — a raise "
        "there is swallowed by the teardown loop, so the diagnostic channel "
        f"is the only signal that survives; stdout:\n{run.stdout}"
    )


def test_every_position_behaves_in_worker_subprocesses(tmp: TempDir) -> None:
    """A worker is a separate process; the identity must be set there too."""
    # Arrange / Act
    run = helpers.run_with_event_log(
        _PROJECT, tmp, "TCC_LOG", "-n", "2", log_name="parallel.log"
    )

    # Assert
    assert run.rc == 0, (
        f"the parallel run must pass; rc={run.rc}\n{run.stdout}\n{run.stderr}"
    )
    assert "BODY test_alpha" in run.events, (
        "a worker is a separate process that re-registers fixtures — the "
        f"identity must be set there too, not only in the parent; got {run.events}"
    )


def test_a_failed_resolution_does_not_leak_identity_into_a_wide_teardown(
    tmp: TempDir,
) -> None:
    """run_test's early-return path must reset the identity ContextVar.

    Found in review: `return resolved` is the one return that escapes
    run_test's try/finally, so a test that dies during fixture resolution
    used to leave its meta behind, and the wide-teardown position handed
    back a context for a test that never ran.
    """
    # Arrange / Act
    run = helpers.run_with_event_log(_LEAK_PROJECT, tmp, "TCL_LOG", "--serial")

    # Assert
    assert "WIDE_TEARDOWN raised" in run.events, (
        "after a failed fixture resolution there is no running test, so a "
        "wider-lifetime teardown must still refuse — a leaked identity here "
        f"names a test that never executed; got {run.events}"
    )


def test_the_module_level_alias_reaches_the_same_context() -> None:
    """oxi.current_test() is the running test's context, same as the classmethod."""
    # Arrange / Act
    via_alias = oxi.current_test()
    via_classmethod = oxi.TestContext.current()

    # Assert
    assert via_alias.node_id == via_classmethod.node_id, (
        "the alias must resolve the same running test as the classmethod — a "
        "second spelling that can disagree is the duplication this API set "
        "out to remove"
    )
    assert via_alias.name == "test_the_module_level_alias_reaches_the_same_context", (
        "the alias must name the calling test, not the module it is defined in"
    )


def test_the_alias_refuses_wherever_the_classmethod_refuses() -> None:
    """The alias delegates, so the position rule cannot drift between them."""
    # Arrange
    token = _test_run_context.set(_DEFAULT_TEST_RUN_CONTEXT)

    # Act / Assert
    try:
        with oxi.raises(TestContextUnavailableError):
            oxi.current_test()
    finally:
        _test_run_context.reset(token)
