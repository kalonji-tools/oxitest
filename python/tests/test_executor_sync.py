"""Tests for sync test execution: pass/fail, fixture injection, teardown safety."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

from oxitest import (
    Fixture,
    FixtureTeardownWarning,
    TempDir,
    WarnCapture,
    helpers,
    parametrize,
)
from oxitest._bridge._fixture_context import _current_teardown_node_id, _warn_teardown
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixture_session import FixtureSession


def test_warn_teardown_emits_fixture_teardown_warning(warn: WarnCapture) -> None:
    """_warn_teardown() emits a FixtureTeardownWarning containing the fixture name."""
    _warn_teardown("my_fix", RuntimeError("boom"))

    assert len(warn.list) == 1, f"expected 1 warning, got {warn.list!r}"
    assert issubclass(warn.list[0].category, FixtureTeardownWarning), (
        f"expected FixtureTeardownWarning, got {warn.list[0].category!r}"
    )
    assert "my_fix" in str(warn.list[0].message), (
        f"warning message should contain fixture name 'my_fix', got "
        f"{str(warn.list[0].message)!r}"
    )


def test_warn_teardown_includes_node_id(warn: WarnCapture) -> None:
    """_warn_teardown() includes the node_id in the warning message when provided."""
    _warn_teardown("my_fix", RuntimeError("boom"), node_id="tests/test_a.py::test_foo")

    assert len(warn.list) == 1, f"expected 1 warning, got {warn.list!r}"
    msg = str(warn.list[0].message)
    assert "my_fix" in msg, f"expected fixture name in message, got {msg!r}"
    assert "test_foo" in msg, f"expected node_id in message, got {msg!r}"


def test_warn_teardown_without_node_id(warn: WarnCapture) -> None:
    """_warn_teardown() still emits a warning when no node_id is given."""
    _warn_teardown("my_fix", RuntimeError("boom"))

    assert len(warn.list) == 1, f"expected 1 warning, got {warn.list!r}"
    msg = str(warn.list[0].message)
    assert "my_fix" in msg, f"expected fixture name in message, got {msg!r}"


def test_warn_teardown_picks_up_contextvar(warn: WarnCapture) -> None:
    """_warn_teardown() reads node_id from _current_teardown_node_id ContextVar."""
    token = _current_teardown_node_id.set("tests/test_b.py::test_bar")
    try:
        _warn_teardown("db", RuntimeError("oops"))
    finally:
        _current_teardown_node_id.reset(token)

    assert len(warn.list) == 1, f"expected 1 warning, got {warn.list!r}"
    msg = str(warn.list[0].message)
    assert "db" in msg, f"expected fixture name in message, got {msg!r}"
    assert "test_bar" in msg, f"expected node_id in message, got {msg!r}"


def test_passing_function(tmp: TempDir) -> None:
    """A passing test produces status='passed' and reports its bare assert line."""
    result = helpers.common.exec_inline(
        tmp, "def test_ok(): assert 1 == 1\n", "test_ok"
    )
    assert result.status == "passed", (
        f"passing test should have status='passed', got {result.status!r}"
    )
    assert result.no_message_lines == (1,), (
        f"bare assert on line 1 should appear in no_message_lines, got "
        f"{result.no_message_lines}"
    )


def test_passing_with_bare_assert_returns_no_message_lines(tmp: TempDir) -> None:
    """Bare assert lines are tracked in no_message_lines for strict-mode checks."""
    result = helpers.common.exec_inline(
        tmp, "def test_bare():\n    assert 1 == 1\n", "test_bare"
    )
    assert result.status == "passed", (
        f"passing test should have status='passed', got {result.status!r}"
    )
    assert len(result.no_message_lines) == 1, (
        f"expected 1 bare-assert line reported, got {result.no_message_lines}"
    )
    assert result.no_message_lines[0] == 2, (
        f"bare assert is on line 2, got no_message_lines={result.no_message_lines}"
    )


def test_passing_with_message_assert_returns_empty_no_message_lines(
    tmp: TempDir,
) -> None:
    """Asserts with a message are not flagged in no_message_lines."""
    result = helpers.common.exec_inline(
        tmp, 'def test_msg():\n    assert 1 == 1, "one equals one"\n', "test_msg"
    )
    assert result.status == "passed", (
        f"passing test should have status='passed', got {result.status!r}"
    )
    assert result.no_message_lines == (), (
        f"assert with message should not appear in no_message_lines, got "
        f"{result.no_message_lines}"
    )


def test_failing_assertion_with_message(tmp: TempDir) -> None:
    """A failing assert with a message produces status='failed' with correct lineno."""
    result = helpers.common.exec_inline(
        tmp, 'def test_bad():\n    assert 1 == 2, "one is not two"\n', "test_bad"
    )
    assert result.status == "failed", (
        f"assertion failure should produce status='failed', got {result.status!r}"
    )
    assert result.message == "one is not two", (
        f"failure message should be 'one is not two', got {result.message!r}"
    )
    assert result.lineno == 2, f"failure lineno should be 2, got {result.lineno}"
    assert "assert" in result.source_line, (
        f"source_line should contain 'assert', got {result.source_line!r}"
    )
    assert result.no_message_lines == (), (
        f"assert with message should not appear in no_message_lines, got "
        f"{result.no_message_lines}"
    )


def test_failing_bare_assertion(tmp: TempDir) -> None:
    """A failing bare assert produces status='failed' with an empty message string."""
    result = helpers.common.exec_inline(
        tmp, "def test_bad():\n    assert 1 == 2\n", "test_bad"
    )
    assert result.status == "failed", (
        f"assertion failure should produce status='failed', got {result.status!r}"
    )
    assert result.message == "", (
        f"bare assert failure should have empty message, got {result.message!r}"
    )
    assert result.lineno == 2, f"failure lineno should be 2, got {result.lineno}"
    assert "assert" in result.source_line, (
        f"source_line should contain 'assert', got {result.source_line!r}"
    )
    assert result.no_message_lines == (), (
        f"failed assert line should not be in no_message_lines (only passing asserts "
        "tracked), "
        f"got {result.no_message_lines}"
    )


def test_error_exception(tmp: TempDir) -> None:
    """An uncaught exception produces status='error' with exception type and message."""
    result = helpers.common.exec_inline(
        tmp, "def test_error():\n    raise ValueError('boom')\n", "test_error"
    )
    assert result.status == "error", (
        f"uncaught exception should produce status='error', got {result.status!r}"
    )
    assert "ValueError" in result.message, (
        f"error message should contain 'ValueError', got {result.message!r}"
    )
    assert "boom" in result.message, (
        f"error message should contain the exception text 'boom', got "
        f"{result.message!r}"
    )
    assert result.lineno == 2, f"error lineno should be 2, got {result.lineno}"


def test_skipped_via_unittest(tmp: TempDir) -> None:
    """Raising unittest.SkipTest produces status='skipped' with the skip reason."""
    result = helpers.common.exec_inline(
        tmp,
        "import unittest\ndef test_skip(): raise unittest.SkipTest('reason')\n",
        "test_skip",
    )
    assert result.status == "skipped", (
        f"unittest.SkipTest should produce status='skipped', got {result.status!r}"
    )
    assert result.message == "reason", (
        f"skip message should be 'reason', got {result.message!r}"
    )


def test_function_not_found_is_error(tmp: TempDir) -> None:
    """Running a non-existent test function name produces status='error'."""
    result = helpers.common.exec_inline(
        tmp, "def test_real(): pass\n", "test_nonexistent", name="test_foo.py"
    )
    assert result.status == "error", (
        f"missing test function should produce status='error', got {result.status!r}"
    )


def test_warning_captured_as_warned_status(tmp: TempDir) -> None:
    """A test that emits a Python warning produces status='warned' with warning type."""
    result = helpers.common.exec_inline(
        tmp,
        "import warnings\n"
        "def test_warn():\n"
        "    warnings.warn('old api', DeprecationWarning)\n"
        "    assert 1 == 1\n",
        "test_warn",
    )
    assert result.status == "warned", (
        f"test emitting warnings should produce status='warned', got {result.status!r}"
    )
    assert "DeprecationWarning" in result.message, (
        f"warned message should mention 'DeprecationWarning', got {result.message!r}"
    )


@dataclass(frozen=True)
class OperandCase:
    """Parametrize case capturing source code and expected assertion operand fields."""

    source: str
    fn_name: str
    expected_status: str
    expected_left: str
    expected_right: str
    expected_op: str
    expected_message: str = ""


@parametrize(
    compare_equal=OperandCase(
        source="def test_bad():\n    assert 41 == 42\n",
        fn_name="test_bad",
        expected_status="failed",
        expected_left="41",
        expected_right="42",
        expected_op="==",
    ),
    bool_assert=OperandCase(
        source="def test_bad():\n    flag = False\n    assert flag\n",
        fn_name="test_bad",
        expected_status="failed",
        expected_left="False",
        expected_right="",
        expected_op="",
    ),
    message_assert=OperandCase(
        source='def test_bad():\n    assert 1 == 2, "one is not two"\n',
        fn_name="test_bad",
        expected_status="failed",
        expected_left="1",
        expected_right="2",
        expected_op="==",
        expected_message="one is not two",
    ),
    error_no_operands=OperandCase(
        source="def test_error():\n    raise ValueError('boom')\n",
        fn_name="test_error",
        expected_status="error",
        expected_left="",
        expected_right="",
        expected_op="",
    ),
    is_none_check=OperandCase(
        source="def test_bad():\n    result = 42\n    assert result is None\n",
        fn_name="test_bad",
        expected_status="failed",
        expected_left="42",
        expected_right="None",
        expected_op="is",
    ),
)
def test_assertion_operands(  # noqa: PLR0913
    tmp: TempDir,
    source: str,
    fn_name: str,
    expected_status: str,
    expected_left: str,
    expected_right: str,
    expected_op: str,
    expected_message: str,
) -> None:
    """Executor extracts left, right, and op operands from assertion failures."""
    result = helpers.common.exec_inline(tmp, source, fn_name, name="test_op.py")
    assert result.status == expected_status, (
        f"expected status={expected_status!r}, got {result.status!r} "
        f"(message={result.message!r})"
    )
    actual_left = getattr(result, "left", "")
    actual_right = getattr(result, "right", "")
    actual_op = getattr(result, "op", "")
    assert actual_left == expected_left, (
        f"expected left={expected_left!r}, got {actual_left!r}"
    )
    assert actual_right == expected_right, (
        f"expected right={expected_right!r}, got {actual_right!r}"
    )
    assert actual_op == expected_op, f"expected op={expected_op!r}, got {actual_op!r}"
    if expected_message:
        assert result.message == expected_message, (
            f"expected message={expected_message!r}, got {result.message!r}"
        )


# ── Fixture integration ───────────────────────────────────────────────────────


def test_run_test_without_session_backward_compat(tmp: TempDir) -> None:
    """run_test() works without a FixtureSession for tests with no fixture params."""
    result = helpers.common.exec_inline(
        tmp, "def test_ok(): assert 1 == 1\n", "test_ok"
    )
    assert result.status == "passed", (
        f"run_test without session should work and return status='passed', got "
        f"{result.status!r}"
    )


def test_run_test_with_fixture_injected(tmp: TempDir) -> None:
    """Executor injects a registered fixture value into Fixture[T]-annotated params."""
    session = helpers.common.make_session_with("val", lambda: 99)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "def test_uses_val(val: Fixture[int]) -> None: assert val == 99\n",
        "test_uses_val",
        session=session,
    )
    assert result.status == "passed", (
        f"test with injected fixture should pass, got status={result.status!r}, "
        f"msg={result.message!r}"
    )


def test_run_test_fixture_setup_error_returns_error_result(tmp: TempDir) -> None:
    """A fixture factory that raises propagates as status='error' with exc text."""

    def bad_factory() -> None:
        msg = "db is down"
        raise RuntimeError(msg)

    session = helpers.common.make_session_with("bad", bad_factory)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "def test_uses_bad(bad: Fixture[None]) -> None: pass\n",
        "test_uses_bad",
        session=session,
    )
    assert result.status == "error", (
        f"fixture setup error should produce status='error', got {result.status!r}"
    )
    assert "bad" in result.message, (
        f"error message should mention fixture name 'bad', got {result.message!r}"
    )
    assert "db is down" in result.message, (
        f"error message should contain the original exception text, got "
        f"{result.message!r}"
    )


def test_run_test_missing_fixture_returns_error_result(
    tmp: TempDir, fixture_session: Fixture[FixtureSession]
) -> None:
    """Requesting an unregistered fixture produces status='error' naming the fixture."""
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "def test_uses_missing(nonexistent: Fixture[int]) -> None: pass\n",
        "test_uses_missing",
        session=fixture_session,
    )
    assert result.status == "error", (
        f"missing fixture should produce status='error', got {result.status!r}"
    )
    assert "nonexistent" in result.message, (
        f"error message should mention the missing fixture 'nonexistent', got "
        f"{result.message!r}"
    )


def test_run_test_fixture_teardown_runs_after_failure(tmp: TempDir) -> None:
    """Yield fixture teardown executes even when the test body fails an assertion."""
    torn_down = []

    def factory() -> Generator[int, None, None]:
        yield 99
        torn_down.append(True)

    session = helpers.common.make_session_with("val", factory)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "def test_fail(val: Fixture[int]) -> None: assert val == 0, 'not zero'\n",
        "test_fail",
        session=session,
    )
    assert result.status == "failed", (
        f"test with failing assertion should produce status='failed', got "
        f"{result.status!r}"
    )
    assert torn_down == [True], (  # teardown ran despite test failure
        f"fixture teardown should run even when test fails, got torn_down={torn_down!r}"
    )


def test_yield_fixture_teardown_exception_does_not_affect_test_result(
    tmp: TempDir, warn: WarnCapture
) -> None:
    """A RuntimeError raised inside fixture teardown must not change test status."""
    torn_down: list[str] = []

    def factory() -> Generator[int, None, None]:
        yield 42
        torn_down.append("ran")
        msg = "teardown exploded"
        raise RuntimeError(msg)

    session = helpers.common.make_session_with("val", factory)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "def test_ok(val: Fixture[int]) -> None:\n"
        "    assert val == 42\n",
        "test_ok",
        session=session,
    )
    assert result.status == "passed", (
        f"teardown exception must not affect test result, got "
        f"status={result.status!r}, msg={result.message!r}"
    )
    assert torn_down == ["ran"], (
        f"fixture teardown must have executed before raising, got {torn_down!r}"
    )
    assert any(issubclass(w.category, FixtureTeardownWarning) for w in warn.list), (
        f"expected a FixtureTeardownWarning in warn.list, got {warn.list!r}"
    )


def test_yield_fixture_teardown_exception_does_not_block_next_teardown(
    tmp: TempDir, warn: WarnCapture
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
    reg.register(helpers.common.make_fixture_def("a", factory_a, conftest_path="/c.py"))
    reg.register(helpers.common.make_fixture_def("b", factory_b, conftest_path="/c.py"))
    session = FixtureSession(reg)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "def test_ok(a: Fixture[int], b: Fixture[int]) -> None:\n"
        "    assert a == 1\n"
        "    assert b == 2\n",
        "test_ok",
        session=session,
    )
    assert result.status == "passed", (
        f"teardown exception must not propagate to test result, got "
        f"status={result.status!r}, msg={result.message!r}"
    )
    assert "a_teardown" in log, f"first fixture teardown must have run, got log={log!r}"
    assert "b_teardown" in log, (
        f"second fixture teardown must still run despite first fixture raising, "
        f"got log={log!r}"
    )
    assert any(issubclass(w.category, FixtureTeardownWarning) for w in warn.list), (
        f"expected a FixtureTeardownWarning in warn.list, got {warn.list!r}"
    )


def test_multiple_teardown_failures_all_reported(
    tmp: TempDir, warn: WarnCapture
) -> None:
    """When ALL fixture teardowns fail, each emits a warning and test still passes."""
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
    reg.register(helpers.common.make_fixture_def("a", factory_a, conftest_path="/c.py"))
    reg.register(helpers.common.make_fixture_def("b", factory_b, conftest_path="/c.py"))
    session = FixtureSession(reg)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "def test_ok(a: Fixture[int], b: Fixture[int]) -> None:\n"
        "    assert a == 1\n"
        "    assert b == 2\n",
        "test_ok",
        session=session,
    )
    assert result.status == "passed", (
        f"test result should be passed despite all teardowns failing, got "
        f"status={result.status!r}, msg={result.message!r}"
    )
    assert "a_teardown" in log, f"fixture a teardown must have run, got log={log!r}"
    assert "b_teardown" in log, f"fixture b teardown must have run, got log={log!r}"
    teardown_warnings = [
        w for w in warn.list if issubclass(w.category, FixtureTeardownWarning)
    ]
    assert len(teardown_warnings) == 2, (
        f"expected 2 FixtureTeardownWarning (one per failing teardown), "
        f"got {len(teardown_warnings)}: {warn.list!r}"
    )


# ── Compact parametrize ───────────────────────────────────────────────────────


def test_compact_parametrize_passes_whole_dataclass(tmp: TempDir) -> None:
    """params: Params receives the whole dataclass, not spread fields."""
    result = helpers.common.exec_inline(
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
    assert result.status == "passed", (
        f"compact parametrize (params: Params) should pass, got "
        f"status={result.status!r}, msg={result.message!r}"
    )


def test_expanded_parametrize_still_works(tmp: TempDir) -> None:
    """x: int, y: int receives spread fields (existing behaviour preserved)."""
    result = helpers.common.exec_inline(
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
    assert result.status == "passed", (
        f"expanded parametrize (x: int, y: int) should pass, got "
        f"status={result.status!r}, msg={result.message!r}"
    )


def test_compact_parametrize_mixed_with_fixture(tmp: TempDir) -> None:
    """params: Params + db: Fixture[int] — both resolved correctly."""
    session = helpers.common.make_session_with("db", lambda: 99)
    result = helpers.common.exec_inline(
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
    assert result.status == "passed", (
        f"compact parametrize with fixture should pass, got status={result.status!r}, "
        f"msg={result.message!r}"
    )


def test_expanded_parametrize_with_unrelated_annotation(tmp: TempDir) -> None:
    """Non-fixture param annotated with a different type → expanded mode."""
    result = helpers.common.exec_inline(
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
    assert result.status == "passed", (
        f"expanded parametrize with unrelated annotation should pass, "
        f"got status={result.status!r}, msg={result.message!r}"
    )
