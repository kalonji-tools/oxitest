"""``TestContext`` identity is refused outside a test, never guessed (#1874).

Three ``TestMeta`` bundles reach ``TestContext``. Only one of them describes a
test:

===================================  ===========================  ============
Built at                             Reached by                   Identity
===================================  ===========================  ============
``executor``/``worker``              a test's ``ctx`` parameter   real
``_fixture_instantiator``            a fixture's ``ctx`` param    **none**
``_fixture_session.get_by_type``     ``Fixture[T]`` by type       **none**
===================================  ===========================  ============

The second used to report the *fixture's* name as ``ctx.name`` and the third
reported ``""``, both silently. ``f"test_{ctx.name}"`` in a fixture therefore
produced one well-formed, identical string for every test in a run — a
collision that surfaces somewhere else entirely.

``fx.oxi.ctx`` was a fourth case and is not in the table because it is no
longer synthetic: the proxy now forwards the running test's ``TestMeta`` whole
rather than rebuilding one from ``module_path`` + ``fn_name``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture, TempDir, TestContext
from oxitest._bridge._errors import TestIdentityUnavailableError
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._test_meta import TestMeta
from tests import helpers

_DATA_ROOT = Path(__file__).parent / "data"
_PROJECT = _DATA_ROOT / "testcontext_in_fixture"

#: ``ExitCode::Failure`` (``src/types/exit.rs``). The refusal happens while a
#: fixture is being set up for a running test, so the run completes and exits 1
#: rather than 3 (``CollectError``).
_EXIT_FAILURE = 1

#: The fixture's own name, which ``ctx.name`` used to return in its body.
_FIXTURE_NAME = "db_schema"


def _fixture_meta() -> TestMeta:
    """The bundle ``_resolve_deps`` builds for fixture-to-fixture resolution."""
    return TestMeta(
        module_path="/t.py",
        fn_name=_FIXTURE_NAME,
        node_id="",
        describes_a_test=False,
    )


@dataclass(frozen=True)
class Accessor:
    """One ``TestContext`` identity property, by name."""

    attr: str


@oxi.parametrize(
    name=Accessor("name"),
    node_id=Accessor("node_id"),
    marks=Accessor("marks"),
    param_id=Accessor("param_id"),
)
def test_identity_reads_raise_outside_a_test(case: Accessor) -> None:
    """All four identity accessors refuse; none of them guesses."""
    # Arrange
    ctx = TestContext(_fixture_meta(), [])

    # Act / Assert
    with oxi.raises(TestIdentityUnavailableError) as exc:
        getattr(ctx, case.attr)

    assert _FIXTURE_NAME not in str(exc.value), (
        f"the message for {case.attr} must not echo the fixture's own name; "
        f"handing that name back as if it answered the question is the whole "
        f"defect, and a message repeating it re-teaches the wrong model"
    )


def test_identity_reads_still_work_on_a_test() -> None:
    """The guard must be invisible to the case it does not apply to."""
    # Arrange
    meta = TestMeta(module_path="/t.py", fn_name="test_x", node_id="/t.py::test_x")
    ctx = TestContext(meta, [])

    # Act
    identity = (ctx.name, ctx.node_id, ctx.marks, ctx.param_id)

    # Assert
    assert identity == ("test_x", "/t.py::test_x", frozenset(), None), (
        "a TestContext built for a real test must be untouched by #1874; a "
        "guard that fired here would break every test that reads ctx.name, "
        "which is the supported use"
    )


def test_addfinalizer_still_works_outside_a_test() -> None:
    """Teardown registration is the reason to inject ``ctx`` into a fixture."""
    # Arrange
    stack: list[Callable[[], None]] = []
    ctx = TestContext(_fixture_meta(), stack)

    # Act
    ctx.addfinalizer(lambda: None)
    ctx.on_teardown(lambda: None)

    # Assert
    assert len(stack) == 2, (
        "if the identity guard caught addfinalizer too, the fix would remove "
        "the only legitimate use of ctx inside a fixture body and turn a "
        "silent wrong answer into a loud regression"
    )


def test_module_path_still_works_outside_a_test() -> None:
    """``module_path`` is where resolution is, not who the test is."""
    # Arrange
    ctx = TestContext(_fixture_meta(), [])

    # Act
    result = ctx.module_path

    # Assert
    assert result == "/t.py", (
        "module_path is the same value that selects the module-lifetime scope "
        "bucket, so it is correct inside a fixture body; guarding it would "
        "refuse an answer that is true"
    )


def test_the_by_type_route_also_refuses_identity(
    fixture_session: Fixture[FixtureSession],
) -> None:
    """Site 2, driven through the real resolver rather than a hand-built meta.

    ``get_fixture_by_type`` runs outside a test entirely — it is the route
    ``@oxi.arrange(TestContext)`` and broad ``Fixture[Any]`` hints take. Its
    docstring used to *state* that identity came back empty; nothing enforced
    it, and nothing told the caller.
    """
    # Arrange
    teardowns: list[Callable[[], None]] = []

    # Act
    ctx = fixture_session.get_fixture_by_type(TestContext, "test_mod.py", teardowns)

    # Assert
    with oxi.raises(TestIdentityUnavailableError):
        _ = ctx.node_id
    assert ctx.module_path == "test_mod.py", (
        "the by-type route still knows where resolution is; refusing that too "
        "would over-shoot the fix and break arrange's own use of the context"
    )


def test_a_fixture_reading_ctx_name_fails_the_run() -> None:
    """End to end: the refusal reaches the user, with a usable message."""
    # Act
    project = _PROJECT / "reads_identity"
    stdout, stderr, rc = helpers.run_oxitest(project, "--warnings", cwd=str(project))

    # Assert
    assert rc == _EXIT_FAILURE, (
        f"a fixture deriving a value from ctx.name must fail the run; passing "
        f"is what let every test in a suite share one 'test-specific' schema "
        f"name\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "TestContext.name is not available here" in stdout, (
        "the diagnostic must name the accessor and say it is unavailable — a "
        "bare FixtureSetupError would send the reader into the fixture body "
        f"looking for a bug that is not there\nstdout:\n{stdout}"
    )
    assert "addfinalizer" in stdout, (
        "the message must point at the supported use of ctx in a fixture; "
        "without it the reader's only inference is that ctx does not belong in "
        f"a fixture at all, which is wrong\nstdout:\n{stdout}"
    )


def test_the_supported_uses_of_ctx_all_still_pass(tmp: TempDir) -> None:
    """The positive half: teardown from a fixture, identity from a test.

    Includes ``fx.oxi.ctx.node_id``, which returned ``""`` from a real test
    before the proxy forwarded the running test's ``TestMeta`` whole.
    """
    # Arrange
    project = _PROJECT / "supported"
    log = Path(tmp) / "events.log"
    env = {**os.environ, "TESTCONTEXT_LOG": str(log)}

    # Act — also under -n 2: a worker builds its own TestMeta from the wire,
    # so a regression that lost `describes_a_test` or the forwarded node id
    # only in the parallel path would be invisible to a serial assertion.
    stdout, stderr, rc = helpers.run_oxitest(
        project, "--warnings", "-n", "2", cwd=str(project), env=env
    )

    # Assert
    assert rc == 0, (
        f"ctx.addfinalizer from a fixture, ctx identity on a test, and "
        f"fx.oxi.ctx identity must all keep working; without this the refusal "
        f"next door could be a blanket break\nstdout:\n{stdout}\n"
        f"stderr:\n{stderr}"
    )
    assert "4 passed" in stdout, (
        "all four supported uses must run; a collection regression that "
        f"dropped one would still exit 0\nstdout:\n{stdout}"
    )
    events = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    assert events == ["FINALIZED test_schema"], (
        f"the finalizer a fixture registered through ctx must actually run — "
        f"the in-project test only proves it was accepted, and a guard that "
        f"broke teardown while leaving registration intact would pass there "
        f"and fail nothing\nevents: {events}\nstdout:\n{stdout}"
    )
