"""Tests for sync test execution: pass/fail, fixture injection, teardown safety."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

from oxitest import (
    Fixture,
    TempDir,
    parametrize,
)
from oxitest._bridge._diagnostic_collector import (
    _diagnostic_collector_var,
)
from oxitest._bridge._fixture_context import _current_teardown_node_id, _warn_teardown
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge.result import (
    Diagnostic,
    ErrorResult,
    FailedResult,
    PassedResult,
    SkippedResult,
    WarnedResult,
)
from tests import helpers


def test_warn_teardown_emits_diagnostic(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """_warn_teardown() emits a diagnostic containing the fixture name."""
    _warn_teardown("my_fix", RuntimeError("boom"))

    assert len(diag_collector) == 1, (
        f"teardown diagnostics are one-per-fixture -- multiple or zero means the emit"
        f" logic is broken: "
        f"{diag_collector!r}"
    )
    assert diag_collector[0].context == "fixture teardown", (
        f"teardown failures must use 'fixture teardown' context so tooling can filter"
        f" them: {diag_collector[0].context!r}"
    )
    assert "my_fix" in diag_collector[0].message, (
        f"the fixture name identifies which cleanup failed -- without it, users cannot"
        f" diagnose "
        f"resource leaks: {diag_collector[0].message!r}"
    )


def test_warn_teardown_includes_node_id(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """_warn_teardown() includes the node_id in the diagnostic message when provided."""
    _warn_teardown("my_fix", RuntimeError("boom"), node_id="tests/test_a.py::test_foo")

    assert len(diag_collector) == 1, (
        f"teardown diagnostics are one-per-fixture -- multiple or zero means the emit"
        f" logic is broken: "
        f"{diag_collector!r}"
    )
    msg = diag_collector[0].message
    assert "my_fix" in msg, (
        f"the fixture name identifies which cleanup failed -- without it, users cannot"
        f" diagnose "
        f"resource leaks: {msg!r}"
    )
    assert "test_foo" in msg, (
        f"the node_id tells users which test triggered the teardown failure -- without"
        f" it, the "
        f"diagnostic is not actionable: {msg!r}"
    )


def test_warn_teardown_picks_up_contextvar(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """_warn_teardown() reads node_id from _current_teardown_node_id ContextVar."""
    node_token = _current_teardown_node_id.set("tests/test_b.py::test_bar")
    try:
        _warn_teardown("db", RuntimeError("oops"))
    finally:
        _current_teardown_node_id.reset(node_token)

    assert len(diag_collector) == 1, (
        f"teardown diagnostics are one-per-fixture -- multiple or zero means the emit"
        f" logic is broken: "
        f"{diag_collector!r}"
    )
    msg = diag_collector[0].message
    assert "db" in msg, (
        f"the fixture name identifies which cleanup failed -- without it, users cannot"
        f" diagnose "
        f"resource leaks: {msg!r}"
    )
    assert "test_bar" in msg, (
        f"ContextVar-based node_id must propagate into the diagnostic so users know"
        f" which test "
        f"triggered the teardown failure: {msg!r}"
    )


def test_passing_function(tmp: TempDir) -> None:
    """A passing test produces status='passed' and reports its bare assert line."""
    result = helpers.exec_inline(tmp, "def test_ok(): assert 1 == 1\n", "test_ok")
    result = helpers.assert_result(
        result,
        PassedResult,
        why="simple passing test is the baseline contract -- executor must not mangle"
        " results",
    )
    assert result.no_message_lines == (1,), (
        f"strict mode relies on no_message_lines to catch bare asserts -- missing this"
        f" line means "
        f"the strict check cannot enforce message requirements:"
        f" {result.no_message_lines}"
    )


def test_passing_with_bare_assert_returns_no_message_lines(tmp: TempDir) -> None:
    """Bare assert lines are tracked in no_message_lines for strict-mode checks."""
    result = helpers.exec_inline(
        tmp, "def test_bare():\n    assert 1 == 1\n", "test_bare"
    )
    result = helpers.assert_result(
        result,
        PassedResult,
        why="passing test is the baseline contract -- executor must not mangle results",
    )
    assert len(result.no_message_lines) == 1, (
        f"exactly one bare assert exists in the source -- over- or under-counting"
        f" breaks strict "
        f"mode reporting: {result.no_message_lines}"
    )
    assert result.no_message_lines[0] == 2, (
        f"the line number must match the source location so strict mode can point users"
        f" to the "
        f"exact bare assert: no_message_lines={result.no_message_lines}"
    )


def test_passing_with_message_assert_returns_empty_no_message_lines(
    tmp: TempDir,
) -> None:
    """Asserts with a message are not flagged in no_message_lines."""
    result = helpers.exec_inline(
        tmp, 'def test_msg():\n    assert 1 == 1, "one equals one"\n', "test_msg"
    )
    result = helpers.assert_result(
        result,
        PassedResult,
        why="passing test is the baseline contract -- executor must not mangle results",
    )
    assert result.no_message_lines == (), (
        f"asserts with messages satisfy strict mode -- flagging them would produce"
        f" false violations "
        f"and erode trust in strict enforcement: {result.no_message_lines}"
    )


def test_failing_assertion_with_message(tmp: TempDir) -> None:
    """A failing assert with a message produces status='failed' with correct lineno."""
    result = helpers.exec_inline(
        tmp, 'def test_bad():\n    assert 1 == 2, "one is not two"\n', "test_bad"
    )
    result = helpers.assert_result(
        result,
        FailedResult,
        why="AssertionError maps to 'failed' status, not 'error' -- these are distinct"
        " failure modes that reporters and CI gates handle differently",
    )
    assert result.message == "one is not two", (
        f"the user-provided assertion message is the developer's diagnosis -- losing it"
        f" forces "
        f"them to re-derive context from the traceback: {result.message!r}"
    )
    assert result.lineno == 2, (
        f"the lineno anchors the failure to source -- wrong line numbers send"
        f" developers on wild "
        f"goose chases: {result.lineno}"
    )
    assert "assert" in result.source_line, (
        f"source_line gives context without opening the file -- omitting the assert"
        f" keyword makes "
        f"the snippet useless for quick triage: {result.source_line!r}"
    )
    assert result.no_message_lines == (), (
        f"asserts with messages satisfy strict mode -- flagging them would produce"
        f" false violations "
        f"and erode trust in strict enforcement: {result.no_message_lines}"
    )


def test_failing_bare_assertion(tmp: TempDir) -> None:
    """A failing bare assert produces status='failed' with an empty message string."""
    result = helpers.exec_inline(
        tmp, "def test_bad():\n    assert 1 == 2\n", "test_bad"
    )
    result = helpers.assert_result(
        result,
        FailedResult,
        why="AssertionError maps to 'failed' status, not 'error' -- these are distinct"
        " failure modes that reporters and CI gates handle differently",
    )
    assert result.message == "", (
        f"bare asserts have no user message -- fabricating one would mislead developers"
        f" into "
        f"thinking the test author provided diagnostic text: {result.message!r}"
    )
    assert result.lineno == 2, (
        f"the lineno anchors the failure to source -- wrong line numbers send"
        f" developers on wild "
        f"goose chases: {result.lineno}"
    )
    assert "assert" in result.source_line, (
        f"source_line gives context without opening the file -- omitting the assert"
        f" keyword makes "
        f"the snippet useless for quick triage: {result.source_line!r}"
    )
    assert result.no_message_lines == (), (
        f"no_message_lines only tracks passing bare asserts for strict mode -- failed"
        f" asserts are "
        f"already surfaced as failures, double-reporting would be noise:"
        f" {result.no_message_lines}"
    )


def test_error_exception(tmp: TempDir) -> None:
    """An uncaught exception produces status='error' with exception type and message."""
    result = helpers.exec_inline(
        tmp, "def test_error():\n    raise ValueError('boom')\n", "test_error"
    )
    result = helpers.assert_result(
        result,
        ErrorResult,
        why="uncaught exceptions are infrastructure failures, not assertion failures --"
        " conflating them loses the signal that something unexpected broke",
    )
    assert "ValueError" in result.message, (
        f"the exception type tells developers what category of bug to look for --"
        f" without it, "
        f"they cannot triage the failure from the summary alone: {result.message!r}"
    )
    assert "boom" in result.message, (
        f"the exception text carries the author's diagnostic context -- stripping it"
        f" forces "
        f"developers to reproduce the failure to understand it: {result.message!r}"
    )
    assert result.lineno == 2, (
        f"the lineno anchors the error to source -- wrong line numbers send developers"
        f" on wild "
        f"goose chases: {result.lineno}"
    )


def test_skipped_via_unittest(tmp: TempDir) -> None:
    """Raising unittest.SkipTest produces status='skipped' with the skip reason."""
    result = helpers.exec_inline(
        tmp,
        "import unittest\ndef test_skip(): raise unittest.SkipTest('reason')\n",
        "test_skip",
    )
    result = helpers.assert_result(
        result,
        SkippedResult,
        why="unittest.SkipTest is the stdlib skip protocol -- misclassifying it breaks"
        " compatibility with existing test suites that rely on unittest skip semantics",
    )
    assert result.message == "reason", (
        f"the skip reason explains why a test was excluded -- losing it makes skip"
        f" reports useless "
        f"for auditing test coverage gaps: {result.message!r}"
    )


def test_function_not_found_is_error(tmp: TempDir) -> None:
    """Running a non-existent test function name produces status='error'."""
    result = helpers.exec_inline(
        tmp, "def test_real(): pass\n", "test_nonexistent", name="test_foo.py"
    )
    helpers.assert_result(
        result,
        ErrorResult,
        why="a missing function is a collection-level defect, not a test failure --"
        " reporting it as 'error' ensures the runner surfaces broken test references"
        " instead of silently skipping them",
    )


def test_warning_captured_as_warned_status(tmp: TempDir) -> None:
    """A test that emits a Python warning produces status='warned' with warning type."""
    result = helpers.exec_inline(
        tmp,
        "import warnings\n"
        "def test_warn():\n"
        "    warnings.warn('old api', DeprecationWarning)\n"
        "    assert 1 == 1\n",
        "test_warn",
    )
    result = helpers.assert_result(
        result,
        WarnedResult,
        why="warnings are a distinct outcome that strict mode can gate on -- collapsing"
        " them into 'passed' hides deprecation debt from CI",
    )
    assert "DeprecationWarning" in result.message, (
        f"the warning category tells developers whether the warning is actionable now"
        f" or later -- "
        f"without it, all warnings look equally urgent: {result.message!r}"
    )


@dataclass(frozen=True)
class OperandCase:
    """Parametrize case capturing source code and expected assertion operand fields."""

    source: str
    fn_name: str
    expected_type: type[FailedResult | ErrorResult]
    expected_left: str
    expected_right: str
    expected_op: str
    expected_message: str = ""


@parametrize(
    compare_equal=OperandCase(
        source="def test_bad():\n    assert 41 == 42\n",
        fn_name="test_bad",
        expected_type=FailedResult,
        expected_left="41",
        expected_right="42",
        expected_op="==",
    ),
    bool_assert=OperandCase(
        source="def test_bad():\n    flag = False\n    assert flag\n",
        fn_name="test_bad",
        expected_type=FailedResult,
        expected_left="False",
        expected_right="",
        expected_op="",
    ),
    message_assert=OperandCase(
        source='def test_bad():\n    assert 1 == 2, "one is not two"\n',
        fn_name="test_bad",
        expected_type=FailedResult,
        expected_left="1",
        expected_right="2",
        expected_op="==",
        expected_message="one is not two",
    ),
    error_no_operands=OperandCase(
        source="def test_error():\n    raise ValueError('boom')\n",
        fn_name="test_error",
        expected_type=ErrorResult,
        expected_left="",
        expected_right="",
        expected_op="",
    ),
    is_none_check=OperandCase(
        source="def test_bad():\n    result = 42\n    assert result is None\n",
        fn_name="test_bad",
        expected_type=FailedResult,
        expected_left="42",
        expected_right="None",
        expected_op="is",
    ),
)
def test_assertion_operands(  # noqa: PLR0913
    tmp: TempDir,
    source: str,
    fn_name: str,
    expected_type: type[FailedResult | ErrorResult],
    expected_left: str,
    expected_right: str,
    expected_op: str,
    expected_message: str,
) -> None:
    """Executor extracts left, right, and op operands from assertion failures."""
    result = helpers.exec_inline(tmp, source, fn_name, name="test_op.py")
    result = helpers.assert_result(
        result,
        expected_type,
        why="status classification drives reporter rendering and CI gates -- wrong"
        " status breaks downstream tooling",
    )
    actual_left = getattr(result, "left", "")
    actual_right = getattr(result, "right", "")
    actual_op = getattr(result, "op", "")
    assert actual_left == expected_left, (
        f"the left operand shows what the code actually produced -- without it,"
        f" developers cannot "
        f"see the mismatch at a glance: {actual_left!r}"
    )
    assert actual_right == expected_right, (
        f"the right operand shows what was expected -- without it, the diff is"
        f" incomplete and "
        f"developers must re-run the test to understand the failure: {actual_right!r}"
    )
    assert actual_op == expected_op, (
        f"the operator distinguishes equality from identity from ordering --"
        f" misreporting it "
        f"misleads developers about what kind of comparison failed: {actual_op!r}"
    )
    if expected_message:
        assert result.message == expected_message, (
            f"the user-provided assertion message is the developer's diagnosis --"
            f" losing it forces "
            f"them to re-derive context from the traceback: {result.message!r}"
        )


# ── Fixture integration ───────────────────────────────────────────────────────


def test_run_test_without_session_backward_compat(tmp: TempDir) -> None:
    """run_test() works without a FixtureSession for tests with no fixture params."""
    result = helpers.exec_inline(tmp, "def test_ok(): assert 1 == 1\n", "test_ok")
    helpers.assert_result(
        result,
        PassedResult,
        why="backward compatibility guarantee -- tests with no fixture params must work"
        " without a session so existing code does not break when the fixture system"
        " evolves",
    )


def test_run_test_with_fixture_injected(tmp: TempDir) -> None:
    """Executor injects a registered fixture value into Fixture[T]-annotated params."""
    session = helpers.make_session_with("val", lambda: 99)
    result = helpers.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "def test_uses_val(val: Fixture[int]) -> None: assert val == 99\n",
        "test_uses_val",
        session=session,
    )
    helpers.assert_result(
        result,
        PassedResult,
        why="fixture injection is the core DI contract -- if the executor cannot"
        " resolve and pass a registered fixture value, the entire fixture system is"
        " broken",
    )


def test_run_test_fixture_setup_error_returns_error_result(tmp: TempDir) -> None:
    """A fixture factory that raises propagates as status='error' with exc text."""

    def bad_factory() -> None:
        msg = "db is down"
        raise RuntimeError(msg)

    session = helpers.make_session_with("bad", bad_factory)
    result = helpers.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "def test_uses_bad(bad: Fixture[None]) -> None: pass\n",
        "test_uses_bad",
        session=session,
    )
    result = helpers.assert_result(
        result,
        ErrorResult,
        why="fixture setup failures are infrastructure errors, not test failures --"
        " conflating them hides the fact that the test never ran",
    )
    assert "bad" in result.message, (
        f"the fixture name tells developers which dependency failed -- without it, they"
        f" must "
        f"bisect the fixture graph to find the broken setup: {result.message!r}"
    )
    assert "db is down" in result.message, (
        f"the original exception text carries the root cause -- stripping it forces"
        f" developers "
        f"to reproduce the failure to diagnose it: {result.message!r}"
    )


def test_run_test_missing_fixture_returns_error_result(
    tmp: TempDir, fixture_session: Fixture[FixtureSession]
) -> None:
    """Requesting an unregistered fixture produces status='error' naming the fixture."""
    result = helpers.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "def test_uses_missing(nonexistent: Fixture[int]) -> None: pass\n",
        "test_uses_missing",
        session=fixture_session,
    )
    result = helpers.assert_result(
        result,
        ErrorResult,
        why="an unregistered fixture is a configuration defect -- reporting it as"
        " 'error' ensures the runner surfaces wiring mistakes instead of silently"
        " injecting None",
    )
    assert "nonexistent" in result.message, (
        f"naming the missing fixture tells developers exactly what to register -- a"
        f" generic error "
        f"forces them to inspect every parameter to find the gap: {result.message!r}"
    )


def test_run_test_fixture_teardown_runs_after_failure(tmp: TempDir) -> None:
    """Yield fixture teardown executes even when the test body fails an assertion."""
    torn_down = []

    def factory() -> Generator[int, None, None]:
        yield 99
        torn_down.append(True)

    session = helpers.make_session_with("val", factory)
    result = helpers.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "def test_fail(val: Fixture[int]) -> None: assert val == 0, 'not zero'\n",
        "test_fail",
        session=session,
    )
    helpers.assert_result(
        result,
        FailedResult,
        why="AssertionError maps to 'failed' status, not 'error' -- these are distinct"
        " failure modes that reporters and CI gates handle differently",
    )
    assert torn_down == [True], (  # teardown ran despite test failure
        f"yield fixture teardown is a cleanup guarantee -- skipping it on failure"
        f" causes resource "
        f"leaks (open connections, temp files, locked state): torn_down={torn_down!r}"
    )


def test_yield_fixture_teardown_exception_does_not_affect_test_result(
    tmp: TempDir,
) -> None:
    """A RuntimeError raised inside fixture teardown must not change test status."""
    torn_down: list[str] = []

    def factory() -> Generator[int, None, None]:
        yield 42
        torn_down.append("ran")
        msg = "teardown exploded"
        raise RuntimeError(msg)

    session = helpers.make_session_with("val", factory)
    # Force the diagnostic collector to point to this session's diagnostics
    # so teardown diagnostics land here instead of the outer test runner's session.
    diag_token = _diagnostic_collector_var.set(session.diagnostics)
    try:
        result = helpers.exec_inline(
            tmp,
            "from oxitest import Fixture\n"
            "def test_ok(val: Fixture[int]) -> None:\n"
            "    assert val == 42\n",
            "test_ok",
            session=session,
        )
    finally:
        _diagnostic_collector_var.reset(diag_token)
    helpers.assert_result(
        result,
        PassedResult,
        why="teardown errors are side-effects -- they must not retroactively change a"
        " passing verdict or developers lose trust in green results",
    )
    assert torn_down == ["ran"], (
        f"teardown must execute even when it will raise -- the cleanup side-effects"
        f" (closing "
        f"connections, deleting temps) happen before the raise: {torn_down!r}"
    )
    assert any(d.context == "fixture teardown" for d in session.diagnostics), (
        f"teardown failures must surface as diagnostics so developers know cleanup"
        f" failed without "
        f"the test being retroactively marked broken: {session.diagnostics!r}"
    )


def test_yield_fixture_teardown_exception_does_not_block_next_teardown(
    tmp: TempDir,
) -> None:
    """Teardown exception in first fixture must not block teardown of second fixture."""
    log: list[str] = []

    def factory_a() -> Generator[int, None, None]:
        yield 1
        log.append("a_teardown")
        msg = "a teardown exploded"
        raise RuntimeError(msg)

    def factory_b() -> Generator[int, None, None]:
        yield 2
        log.append("b_teardown")

    reg = FixtureRegistry()
    reg.register(helpers.make_fixture_def("a", factory_a, conftest_path="/c.py"))
    reg.register(helpers.make_fixture_def("b", factory_b, conftest_path="/c.py"))
    session = FixtureSession(reg)
    diag_token = _diagnostic_collector_var.set(session.diagnostics)
    try:
        result = helpers.exec_inline(
            tmp,
            "from oxitest import Fixture\n"
            "def test_ok(a: Fixture[int], b: Fixture[int]) -> None:\n"
            "    assert a == 1\n"
            "    assert b == 2\n",
            "test_ok",
            session=session,
        )
    finally:
        _diagnostic_collector_var.reset(diag_token)
    helpers.assert_result(
        result,
        PassedResult,
        why="teardown errors are side-effects -- they must not retroactively change a"
        " passing verdict or developers lose trust in green results",
    )
    assert "a_teardown" in log, (
        f"every fixture teardown must run regardless of other teardown failures --"
        f" skipping "
        f"cleanup cascades resource leaks across the session: log={log!r}"
    )
    assert "b_teardown" in log, (
        f"teardown isolation is critical -- one fixture's cleanup failure must not"
        f" block another "
        f"fixture's cleanup or resources accumulate across the session: log={log!r}"
    )
    assert any(d.context == "fixture teardown" for d in session.diagnostics), (
        f"teardown failures must surface as diagnostics so developers know cleanup"
        f" failed without "
        f"the test being retroactively marked broken: {session.diagnostics!r}"
    )


def test_multiple_teardown_failures_all_reported(
    tmp: TempDir,
) -> None:
    """When ALL fixture teardowns fail, each emits a diagnostic; test passes."""
    log: list[str] = []

    def factory_a() -> Generator[int, None, None]:
        yield 1
        log.append("a_teardown")
        msg = "a exploded"
        raise RuntimeError(msg)

    def factory_b() -> Generator[int, None, None]:
        yield 2
        log.append("b_teardown")
        msg = "b exploded"
        raise ValueError(msg)

    reg = FixtureRegistry()
    reg.register(helpers.make_fixture_def("a", factory_a, conftest_path="/c.py"))
    reg.register(helpers.make_fixture_def("b", factory_b, conftest_path="/c.py"))
    session = FixtureSession(reg)
    diag_token = _diagnostic_collector_var.set(session.diagnostics)
    try:
        result = helpers.exec_inline(
            tmp,
            "from oxitest import Fixture\n"
            "def test_ok(a: Fixture[int], b: Fixture[int]) -> None:\n"
            "    assert a == 1\n"
            "    assert b == 2\n",
            "test_ok",
            session=session,
        )
    finally:
        _diagnostic_collector_var.reset(diag_token)
    helpers.assert_result(
        result,
        PassedResult,
        why="teardown errors are side-effects -- even when all teardowns fail, the test"
        " body passed and the verdict must reflect that",
    )
    assert "a_teardown" in log, (
        f"every fixture teardown must attempt cleanup even when all peers also fail --"
        f" partial "
        f"cleanup is better than no cleanup: log={log!r}"
    )
    assert "b_teardown" in log, (
        f"teardown isolation is critical -- one fixture's cleanup failure must not"
        f" block another "
        f"fixture's cleanup or resources accumulate across the session: log={log!r}"
    )
    teardown_diags = [d for d in session.diagnostics if d.context == "fixture teardown"]
    assert len(teardown_diags) == 2, (
        f"each failing teardown must emit its own diagnostic -- collapsing them hides"
        f" which fixtures "
        f"leaked resources: {len(teardown_diags)} diagnostics in"
        f" {session.diagnostics!r}"
    )


# ── Compact parametrize ───────────────────────────────────────────────────────


def test_compact_parametrize_passes_whole_dataclass(tmp: TempDir) -> None:
    """params: Params receives the whole dataclass, not spread fields."""
    result = helpers.exec_inline(
        tmp,
        "import dataclasses\n"
        "import oxitest\n"
        "\n"
        "@dataclasses.dataclass(frozen=True)\n"
        "class Params:\n"
        "    x: int\n"
        "    y: int\n"
        "\n"
        "@oxitest.parametrize(case1=Params(x=1, y=2))\n"
        "def test_compact(params: Params) -> None:\n"
        "    assert params.x == 1\n"
        "    assert params.y == 2\n",
        "test_compact",
        param_id="case1",
    )
    helpers.assert_result(
        result,
        PassedResult,
        why="compact mode passes the whole dataclass as a single argument -- if"
        " resolution breaks, structured test cases lose their grouping and field access"
        " fails",
    )


def test_expanded_parametrize_still_works(tmp: TempDir) -> None:
    """x: int, y: int receives spread fields (existing behaviour preserved)."""
    result = helpers.exec_inline(
        tmp,
        "import dataclasses\n"
        "import oxitest\n"
        "\n"
        "@dataclasses.dataclass(frozen=True)\n"
        "class Params:\n"
        "    x: int\n"
        "    y: int\n"
        "\n"
        "@oxitest.parametrize(case1=Params(x=3, y=4))\n"
        "def test_expanded(x: int, y: int) -> None:\n"
        "    assert x == 3\n"
        "    assert y == 4\n",
        "test_expanded",
        param_id="case1",
    )
    helpers.assert_result(
        result,
        PassedResult,
        why="expanded mode spreads dataclass fields into individual params -- this is"
        " the original behavior and must not regress when compact mode is added",
    )


def test_compact_parametrize_mixed_with_fixture(tmp: TempDir) -> None:
    """params: Params + db: Fixture[int] — both resolved correctly."""
    session = helpers.make_session_with("db", lambda: 99)
    result = helpers.exec_inline(
        tmp,
        "import dataclasses\n"
        "import oxitest\n"
        "from oxitest import Fixture\n"
        "\n"
        "@dataclasses.dataclass(frozen=True)\n"
        "class Params:\n"
        "    x: int\n"
        "\n"
        "@oxitest.parametrize(case1=Params(x=10))\n"
        "def test_mixed(params: Params, db: Fixture[int]) -> None:\n"
        "    assert params.x == 10\n"
        "    assert db == 99\n",
        "test_mixed",
        session=session,
        param_id="case1",
    )
    helpers.assert_result(
        result,
        PassedResult,
        why="compact parametrize and fixture injection must coexist -- if the resolver"
        " confuses Params with Fixture[T] annotations, one system stomps the other",
    )


def test_expanded_parametrize_with_unrelated_annotation(tmp: TempDir) -> None:
    """Non-fixture param annotated with a different type → expanded mode."""
    result = helpers.exec_inline(
        tmp,
        "import dataclasses\n"
        "import oxitest\n"
        "\n"
        "@dataclasses.dataclass(frozen=True)\n"
        "class Params:\n"
        "    x: int\n"
        "\n"
        "@oxitest.parametrize(case1=Params(x=7))\n"
        "def test_unrelated(x: int) -> None:\n"
        "    assert x == 7\n",
        "test_unrelated",
        param_id="case1",
    )
    helpers.assert_result(
        result,
        PassedResult,
        why="non-Fixture annotations must not confuse the resolver into compact mode --"
        " if a plain type hint triggers whole-dataclass injection, field spreading"
        " silently breaks",
    )
