"""Executor integration for @oxi.arrange.

Verifies that CollectedItem.arranged entries are resolved (setup + teardown
registered) BEFORE parameter fixture injection, so arrange side-effects
(e.g. CWD change, env mutation) are visible to the test body.
"""

from __future__ import annotations

from collections.abc import Generator

from oxitest import TempDir, helpers
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._loader import _load_module
from oxitest._bridge.plugin_loader import PluginRegistry


def test_arrange_type_builtin_setup_runs(tmp: TempDir) -> None:
    """@arrange(TempDir) triggers setup and registers teardown before the test runs.

    The executor's arrange loop dispatches isinstance(entry, type) →
    get_fixture_by_type.  If that dispatch is missing, the TempDir factory
    never runs and the teardown list stays empty.
    """
    result = helpers.common.exec_inline(
        tmp,
        "import oxitest\n"
        "from oxitest import TempDir\n"
        "\n"
        "@oxitest.arrange(TempDir)\n"
        "def test_with_arrange() -> None:\n"
        "    assert True, 'arrange-only test must reach the body'\n",
        "test_with_arrange",
    )
    assert result.status == "passed", (
        "arrange phase must not prevent test body from running — "
        f"got status={result.status!r}, message={result.message!r}"
    )


def test_arrange_name_string_setup_runs(tmp: TempDir) -> None:
    """@arrange('my_fixture') triggers setup for a conftest fixture by name.

    The executor's arrange loop dispatches isinstance(entry, str) →
    get_fixture_by_name.  If that dispatch is missing, the conftest factory
    never runs and the teardown list stays empty.
    """
    setup_log: list[str] = []
    teardown_log: list[str] = []

    def my_fixture_factory() -> Generator[None, None, None]:
        setup_log.append("setup")
        yield
        teardown_log.append("teardown")

    session = helpers.common.make_session_with("my_fixture", my_fixture_factory)
    result = helpers.common.exec_inline(
        tmp,
        "import oxitest\n"
        "\n"
        "@oxitest.arrange('my_fixture')\n"
        "def test_with_name_arrange() -> None:\n"
        "    assert True, 'arrange-only test must reach the body'\n",
        "test_with_name_arrange",
        session=session,
    )
    assert result.status == "passed", (
        "arrange phase must not prevent test body from running — "
        f"got status={result.status!r}, message={result.message!r}"
    )
    assert setup_log == ["setup"], (
        "arrange by name must call the fixture factory exactly once — "
        f"setup_log={setup_log!r}"
    )
    assert teardown_log == ["teardown"], (
        "arrange by name must register teardown and run it after the test — "
        f"teardown_log={teardown_log!r}"
    )


def test_arrange_teardown_runs_after_test_body(tmp: TempDir) -> None:
    """Teardown registered by the arrange phase runs after the test body.

    This confirms that get_fixture_by_type/get_fixture_by_name populates
    fn_teardowns so _run_teardowns drains them on test completion.
    """
    teardown_log: list[str] = []

    def side_effect_fixture() -> Generator[None, None, None]:
        yield
        teardown_log.append("torn_down")

    session = helpers.common.make_session_with("side_effect", side_effect_fixture)
    result = helpers.common.exec_inline(
        tmp,
        "import oxitest\n"
        "\n"
        "@oxitest.arrange('side_effect')\n"
        "def test_teardown_registered() -> None:\n"
        "    assert True, 'test body must run before teardown'\n",
        "test_teardown_registered",
        session=session,
    )
    assert result.status == "passed", (
        "test must pass so teardown-ran check is meaningful — "
        f"got status={result.status!r}, message={result.message!r}"
    )
    assert teardown_log == ["torn_down"], (
        "arrange teardown must run after the test body — "
        "if empty, fn_teardowns was not populated by the arrange phase: "
        f"teardown_log={teardown_log!r}"
    )


def test_arrange_missing_fixture_returns_error_result(tmp: TempDir) -> None:
    """@arrange('nonexistent') on a test that requests a missing fixture → error.

    The arrange phase calls get_fixture_by_name which raises
    FixtureNotFoundError; run_test must catch this and return status='error'.
    """
    session = FixtureSession(FixtureRegistry(), PluginRegistry())
    result = helpers.common.exec_inline(
        tmp,
        "import oxitest\n"
        "\n"
        "@oxitest.arrange('nonexistent_fixture')\n"
        "def test_missing_arrange() -> None:\n"
        "    assert True, 'should not reach here'\n",
        "test_missing_arrange",
        session=session,
    )
    assert result.status == "error", (
        "a missing arranged fixture is an infrastructure error — "
        "the test cannot run, so status must be 'error' not 'passed': "
        f"got status={result.status!r}"
    )


def test_arrange_multiple_entries_all_setup(tmp: TempDir) -> None:
    """@arrange(TempDir, 'extra') resolves both entries in order.

    Both get_fixture_by_type (for the type) and get_fixture_by_name (for the
    string) must be called; both teardowns must run.
    """
    log: list[str] = []

    def extra_factory() -> Generator[None, None, None]:
        log.append("extra_setup")
        yield
        log.append("extra_teardown")

    session = helpers.common.make_session_with("extra", extra_factory)
    result = helpers.common.exec_inline(
        tmp,
        "import oxitest\n"
        "from oxitest import TempDir\n"
        "\n"
        "@oxitest.arrange(TempDir, 'extra')\n"
        "def test_multi_arrange() -> None:\n"
        "    assert True, 'test body must run after both arrange entries'\n",
        "test_multi_arrange",
        session=session,
    )
    assert result.status == "passed", (
        "test must pass when all arranged fixtures resolve successfully — "
        f"got status={result.status!r}, message={result.message!r}"
    )
    assert "extra_setup" in log, (
        f"string-based arranged fixture 'extra' must run setup — log={log!r}"
    )
    assert "extra_teardown" in log, (
        f"string-based arranged fixture 'extra' must run teardown — log={log!r}"
    )


def test_arrange_ambiguous_type_reports_error(tmp: TempDir) -> None:
    """@oxi.arrange(SomeType) where two fixtures provide SomeType → error result.

    Guards against the arrange phase's except clause missing AmbiguousFixtureError.
    When two conftest fixtures resolve to the same binding type and the decorator
    uses type-based arrange, run_test must return status='error' with an
    'ambiguous' message rather than propagating an unhandled exception.
    """
    # Write a module that defines an @injectable sentinel type and uses it in
    # @arrange.  The class is written to disk so the executor can load it, and
    # we pre-load the same module here to extract the _AmbigSentinel class so
    # that the FixtureDefs reference the SAME type object the executor will see.
    code = (
        "import oxitest\n"
        "\n"
        "class _AmbigSentinel:\n"
        "    __oxitest_injectable__ = True\n"
        "\n"
        "@oxitest.arrange(_AmbigSentinel)\n"
        "def test_ambiguous() -> None:\n"
        "    pass\n"
    )
    path = helpers.common.write_test_module(tmp, code, name="test_ambig.py")

    # Pre-load the module via the same loader the executor uses, then seed it
    # into the session cache so the executor reuses the exact same module
    # object (and thus the same _AmbigSentinel class identity).
    unique_name = "_oxitest_preloaded_ambig"
    preloaded = _load_module(path, unique_name)
    ambig_type: type = preloaded._AmbigSentinel  # noqa: SLF001

    # Build a session with TWO fixtures for the same type but different names.
    # registry.resolve(ambig_type) sees two candidates; neither name matches
    # the qualifier "_AmbigSentinel" ("alpha"/"beta"), so AmbiguousFixtureError
    # is raised.
    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "alpha", fixture_type=ambig_type, conftest_path="/conftest.py"
        ),
        helpers.common.make_fixture_def(
            "beta", fixture_type=ambig_type, conftest_path="/conftest.py"
        ),
    )
    # Seed the cache so the executor loads the same module we pre-loaded.
    session.module_cache.set(path, preloaded)

    result = helpers.common.run_test(path, "test_ambiguous", session=session)

    assert result.status == "error", (
        "an ambiguous arranged fixture is an infrastructure error — "
        "run_test must catch AmbiguousFixtureError and return status='error', "
        f"not propagate it as an unhandled exception: got status={result.status!r}"
    )
    assert "ambiguous" in result.message.lower(), (
        "the error message must mention 'ambiguous' so the user understands why "
        f"the test could not run: got message={result.message!r}"
    )
