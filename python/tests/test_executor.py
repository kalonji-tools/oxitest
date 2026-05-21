from __future__ import annotations

from dataclasses import dataclass

from oxitest import FixtureTeardownWarning, TempDir, WarnCapture, parametrize
from oxitest._bridge.executor import run_test
from oxitest._bridge.fixtures import (
    FixtureDef,
    FixtureRegistry,
    FixtureSession,
)


def test_warn_teardown_emits_fixture_teardown_warning(warn: WarnCapture) -> None:
    from oxitest._bridge.fixtures import FixtureTeardownWarning, _warn_teardown

    _warn_teardown("my_fix", RuntimeError("boom"))

    assert len(warn.list) == 1, f"expected 1 warning, got {warn.list!r}"
    assert issubclass(warn.list[0].category, FixtureTeardownWarning), (
        f"expected FixtureTeardownWarning, got {warn.list[0].category!r}"
    )
    assert "my_fix" in str(warn.list[0].message), (
        f"warning message should contain fixture name 'my_fix', got "
        f"{str(warn.list[0].message)!r}"
    )


def test_passing_function(tmp: TempDir):
    f = tmp / "test_pass.py"
    f.write_text("def test_ok(): assert 1 == 1\n")
    result = run_test(str(f), "test_ok")
    assert result.status == "passed", (
        f"passing test should have status='passed', got {result.status!r}"
    )
    assert result.message == "", (
        f"passing test should have empty message, got {result.message!r}"
    )
    assert result.file == "", (
        f"passing test should have empty file, got {result.file!r}"
    )
    assert result.lineno == 0, f"passing test should have lineno=0, got {result.lineno}"
    assert result.source_line == "", (
        f"passing test should have empty source_line, got {result.source_line!r}"
    )
    assert result.no_message_lines == [1], (
        f"bare assert on line 1 should appear in no_message_lines, got "
        f"{result.no_message_lines}"
    )


def test_passing_with_bare_assert_returns_no_message_lines(tmp: TempDir):
    f = tmp / "test_bare.py"
    f.write_text("def test_bare():\n    assert 1 == 1\n")
    result = run_test(str(f), "test_bare")
    assert result.status == "passed", (
        f"passing test should have status='passed', got {result.status!r}"
    )
    assert len(result.no_message_lines) == 1, (
        f"expected 1 bare-assert line reported, got {result.no_message_lines}"
    )
    assert result.no_message_lines[0] == 2, (
        f"bare assert is on line 2, got no_message_lines={result.no_message_lines}"
    )


def test_passing_with_message_assert_returns_empty_no_message_lines(tmp: TempDir):
    f = tmp / "test_msg.py"
    f.write_text('def test_msg():\n    assert 1 == 1, "one equals one"\n')
    result = run_test(str(f), "test_msg")
    assert result.status == "passed", (
        f"passing test should have status='passed', got {result.status!r}"
    )
    assert result.no_message_lines == [], (
        f"assert with message should not appear in no_message_lines, got "
        f"{result.no_message_lines}"
    )


def test_failing_assertion_with_message(tmp: TempDir):
    f = tmp / "test_fail.py"
    f.write_text('def test_bad():\n    assert 1 == 2, "one is not two"\n')
    result = run_test(str(f), "test_bad")
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
    assert result.no_message_lines == [], (
        f"assert with message should not appear in no_message_lines, got "
        f"{result.no_message_lines}"
    )


def test_failing_bare_assertion(tmp: TempDir):
    f = tmp / "test_bare_fail.py"
    f.write_text("def test_bad():\n    assert 1 == 2\n")
    result = run_test(str(f), "test_bad")
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
    assert result.no_message_lines == [], (
        f"failed assert line should not be in no_message_lines (only passing asserts "
        "tracked), "
        f"got {result.no_message_lines}"
    )


def test_error_exception(tmp: TempDir):
    f = tmp / "test_err.py"
    f.write_text("def test_error():\n    raise ValueError('boom')\n")
    result = run_test(str(f), "test_error")
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
    assert result.no_message_lines == [], (
        f"error result should have empty no_message_lines, got "
        f"{result.no_message_lines}"
    )


def test_skipped_via_unittest(tmp: TempDir):
    f = tmp / "test_skip2.py"
    f.write_text(
        "import unittest\ndef test_skip(): raise unittest.SkipTest('reason')\n"
    )
    result = run_test(str(f), "test_skip")
    assert result.status == "skipped", (
        f"unittest.SkipTest should produce status='skipped', got {result.status!r}"
    )
    assert result.message == "reason", (
        f"skip message should be 'reason', got {result.message!r}"
    )
    assert result.no_message_lines == [], (
        f"skipped result should have empty no_message_lines, got "
        f"{result.no_message_lines}"
    )


def test_function_not_found_is_error(tmp: TempDir):
    f = tmp / "test_foo.py"
    f.write_text("def test_real(): pass\n")
    result = run_test(str(f), "test_nonexistent")
    assert result.status == "error", (
        f"missing test function should produce status='error', got {result.status!r}"
    )


def test_warning_captured_as_warned_status(tmp: TempDir):
    f = tmp / "test_warn.py"
    f.write_text(
        "import warnings\n"
        "def test_warn():\n"
        "    warnings.warn('old api', DeprecationWarning)\n"
        "    assert 1 == 1\n"
    )
    result = run_test(str(f), "test_warn")
    assert result.status == "warned", (
        f"test emitting warnings should produce status='warned', got {result.status!r}"
    )
    assert "DeprecationWarning" in result.message, (
        f"warned message should mention 'DeprecationWarning', got {result.message!r}"
    )


@dataclass(frozen=True)
class OperandCase:
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
def test_assertion_operands(
    tmp: TempDir,
    source: str,
    fn_name: str,
    expected_status: str,
    expected_left: str,
    expected_right: str,
    expected_op: str,
    expected_message: str,
) -> None:
    f = tmp / "test_op.py"
    f.write_text(source)
    result = run_test(str(f), fn_name)
    assert result.status == expected_status, (
        f"expected status={expected_status!r}, got {result.status!r} "
        f"(message={result.message!r})"
    )
    assert result.left == expected_left, (
        f"expected left={expected_left!r}, got {result.left!r}"
    )
    assert result.right == expected_right, (
        f"expected right={expected_right!r}, got {result.right!r}"
    )
    assert result.op == expected_op, f"expected op={expected_op!r}, got {result.op!r}"
    if expected_message:
        assert result.message == expected_message, (
            f"expected message={expected_message!r}, got {result.message!r}"
        )


# ── Fixture integration ───────────────────────────────────────────────────────


def _make_session_with(name: str, factory) -> FixtureSession:
    reg = FixtureRegistry()
    reg.register(FixtureDef(name, factory, False, None, "/c.py"))
    return FixtureSession(reg)


def test_run_test_without_session_backward_compat(tmp: TempDir):
    f = tmp / "test_simple.py"
    f.write_text("def test_ok(): assert 1 == 1\n")
    result = run_test(str(f), "test_ok")
    assert result.status == "passed", (
        f"run_test without session should work and return status='passed', got "
        f"{result.status!r}"
    )


def test_run_test_with_fixture_injected(tmp: TempDir):
    f = tmp / "test_fix.py"
    f.write_text(
        "from oxitest import Fixture\n"
        "def test_uses_val(val: Fixture[int]) -> None: assert val == 99\n"
    )
    session = _make_session_with("val", lambda: 99)
    session.begin_module(str(f))
    result = run_test(str(f), "test_uses_val", session)
    assert result.status == "passed", (
        f"test with injected fixture should pass, got status={result.status!r}, "
        f"msg={result.message!r}"
    )


def test_run_test_fixture_setup_error_returns_error_result(tmp: TempDir):
    f = tmp / "test_fix.py"
    f.write_text(
        "from oxitest import Fixture\n"
        "def test_uses_bad(bad: Fixture[None]) -> None: pass\n"
    )

    def bad_factory():
        raise RuntimeError("db is down")

    session = _make_session_with("bad", bad_factory)
    session.begin_module(str(f))
    result = run_test(str(f), "test_uses_bad", session)
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


def test_run_test_missing_fixture_returns_error_result(tmp: TempDir):
    f = tmp / "test_fix.py"
    f.write_text(
        "from oxitest import Fixture\n"
        "def test_uses_missing(nonexistent: Fixture[int]) -> None: pass\n"
    )
    reg = FixtureRegistry()  # empty registry
    session = FixtureSession(reg)
    session.begin_module(str(f))
    result = run_test(str(f), "test_uses_missing", session)
    assert result.status == "error", (
        f"missing fixture should produce status='error', got {result.status!r}"
    )
    assert "nonexistent" in result.message, (
        f"error message should mention the missing fixture 'nonexistent', got "
        f"{result.message!r}"
    )


def test_run_test_fixture_teardown_runs_after_failure(tmp: TempDir):
    f = tmp / "test_fix.py"
    f.write_text(
        "from oxitest import Fixture\n"
        "def test_fail(val: Fixture[int]) -> None: assert val == 0, 'not zero'\n"
    )

    torn_down = []

    def factory():
        yield 99
        torn_down.append(True)

    session = _make_session_with("val", factory)
    session.begin_module(str(f))
    result = run_test(str(f), "test_fail", session)
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
    f = tmp / "test_td_exc.py"
    f.write_text(
        "from oxitest import Fixture\n"
        "def test_ok(val: Fixture[int]) -> None:\n"
        "    assert val == 42\n"
    )
    torn_down: list[str] = []

    def factory():
        yield 42
        torn_down.append("ran")
        raise RuntimeError("teardown exploded")

    session = _make_session_with("val", factory)
    session.begin_module(str(f))
    result = run_test(str(f), "test_ok", session)
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
    f = tmp / "test_td_chain.py"
    f.write_text(
        "from oxitest import Fixture\n"
        "def test_ok(a: Fixture[int], b: Fixture[int]) -> None:\n"
        "    assert a == 1\n"
        "    assert b == 2\n"
    )
    log: list[str] = []

    def factory_a():
        yield 1
        log.append("a_teardown")
        raise RuntimeError("a teardown exploded")

    def factory_b():
        yield 2
        log.append("b_teardown")

    reg = FixtureRegistry()
    reg.register(FixtureDef("a", factory_a, False, None, "/c.py"))
    reg.register(FixtureDef("b", factory_b, False, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module(str(f))
    result = run_test(str(f), "test_ok", session)
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
    f = tmp / "test_multi_td.py"
    f.write_text(
        "from oxitest import Fixture\n"
        "def test_ok(a: Fixture[int], b: Fixture[int]) -> None:\n"
        "    assert a == 1\n"
        "    assert b == 2\n"
    )
    log: list[str] = []

    def factory_a():
        yield 1
        log.append("a_teardown")
        raise RuntimeError("a exploded")

    def factory_b():
        yield 2
        log.append("b_teardown")
        raise ValueError("b exploded")

    reg = FixtureRegistry()
    reg.register(FixtureDef("a", factory_a, False, None, "/c.py"))
    reg.register(FixtureDef("b", factory_b, False, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module(str(f))
    result = run_test(str(f), "test_ok", session)
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


def test_compact_parametrize_passes_whole_dataclass(tmp: TempDir):
    """params: Params receives the whole dataclass, not spread fields."""
    f = tmp / "test_compact.py"
    f.write_text(
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
        "    assert params.y == 2\n"
    )
    result = run_test(str(f), "test_compact", param_id="case1")
    assert result.status == "passed", (
        f"compact parametrize (params: Params) should pass, got "
        f"status={result.status!r}, msg={result.message!r}"
    )


def test_expanded_parametrize_still_works(tmp: TempDir):
    """x: int, y: int receives spread fields (existing behaviour preserved)."""
    f = tmp / "test_expanded.py"
    f.write_text(
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
        "    assert y == 4\n"
    )
    result = run_test(str(f), "test_expanded", param_id="case1")
    assert result.status == "passed", (
        f"expanded parametrize (x: int, y: int) should pass, got "
        f"status={result.status!r}, msg={result.message!r}"
    )


def test_compact_parametrize_mixed_with_fixture(tmp: TempDir):
    """params: Params + db: Fixture[int] — both resolved correctly."""
    f = tmp / "test_mixed.py"
    f.write_text(
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
        "    assert db == 99\n"
    )
    session = _make_session_with("db", lambda: 99)
    session.begin_module(str(f))
    result = run_test(str(f), "test_mixed", session, param_id="case1")
    assert result.status == "passed", (
        f"compact parametrize with fixture should pass, got status={result.status!r}, "
        f"msg={result.message!r}"
    )


def test_expanded_parametrize_with_unrelated_annotation(tmp: TempDir):
    """Non-fixture param annotated with a different type → expanded mode."""
    f = tmp / "test_unrelated.py"
    f.write_text(
        "import dataclasses\n"
        "import oxitest\n"
        "\n"
        "@dataclasses.dataclass(frozen=True)\n"
        "class Params:\n"
        "    x: int\n"
        "\n"
        "@oxitest.parametrize(case1=Params(x=7))\n"
        "def test_unrelated(x: int) -> None:\n"
        "    assert x == 7\n"
    )
    result = run_test(str(f), "test_unrelated", param_id="case1")
    assert result.status == "passed", (
        f"expanded parametrize with unrelated annotation should pass, "
        f"got status={result.status!r}, msg={result.message!r}"
    )


def test_run_test_timeout_mark_fires(tmp: TempDir):
    f = tmp / "test_to.py"
    f.write_text(
        "import time, oxitest\n"
        "@oxitest.mark.timeout(seconds=1)\n"
        "def test_slow():\n"
        "    time.sleep(5)\n"
    )
    result = run_test(str(f), "test_slow")
    assert result.status == "timeout", (
        f"@mark.timeout on a slow test should produce status='timeout', got "
        f"{result.status!r}"
    )
    assert "1s" in result.message, (
        f"timeout message should mention the limit '1s', got {result.message!r}"
    )


def test_run_test_timeout_passes_fast_test(tmp: TempDir):
    f = tmp / "test_fast.py"
    f.write_text(
        "import oxitest\n@oxitest.mark.timeout(seconds=5)\ndef test_fast():\n    pass\n"
    )
    result = run_test(str(f), "test_fast")
    assert result.status == "passed", (
        f"fast test under timeout limit should produce status='passed', got "
        f"{result.status!r}"
    )


def test_run_test_default_timeout_fires(tmp: TempDir):
    f = tmp / "test_dt.py"
    f.write_text("import time\ndef test_slow():\n    time.sleep(5)\n")
    result = run_test(str(f), "test_slow", default_timeout=1)
    assert result.status == "timeout", (
        f"default_timeout=1 should fire on a slow test, got status={result.status!r}"
    )


def test_run_test_no_timeout_by_default(tmp: TempDir):
    f = tmp / "test_nd.py"
    f.write_text("def test_ok():\n    pass\n")
    result = run_test(str(f), "test_ok")
    assert result.status == "passed", (
        f"test without timeout mark should pass normally, got {result.status!r}"
    )


# ── Async test execution ─────────────────────────────────────────────────────


def test_async_test_passes(tmp: TempDir):
    f = tmp / "test_async_pass.py"
    f.write_text("async def test_ok():\n    assert 1 == 1\n")
    result = run_test(str(f), "test_ok")
    assert result.status == "passed", (
        f"passing async test should have status='passed', got {result.status!r}, "
        f"msg={result.message!r}"
    )


def test_async_test_fails(tmp: TempDir):
    f = tmp / "test_async_fail.py"
    f.write_text('async def test_bad():\n    assert 1 == 2, "nope"\n')
    result = run_test(str(f), "test_bad")
    assert result.status == "failed", (
        f"failing async test should have status='failed', got {result.status!r}"
    )
    assert "nope" in result.message, (
        f"failure message should contain 'nope', got {result.message!r}"
    )


def test_async_test_error(tmp: TempDir):
    f = tmp / "test_async_err.py"
    f.write_text("async def test_err():\n    raise ValueError('boom')\n")
    result = run_test(str(f), "test_err")
    assert result.status == "error", (
        f"async error should produce status='error', got {result.status!r}"
    )
    assert "ValueError" in result.message, (
        f"error message should contain 'ValueError', got {result.message!r}"
    )
    assert "boom" in result.message, (
        f"error message should contain 'boom', got {result.message!r}"
    )


def test_async_test_warning(tmp: TempDir):
    f = tmp / "test_async_warn.py"
    f.write_text(
        "import warnings\n"
        "async def test_warn():\n"
        "    warnings.warn('old api', DeprecationWarning)\n"
        "    assert 1 == 1\n"
    )
    result = run_test(str(f), "test_warn")
    assert result.status == "warned", (
        f"async test with warning should produce status='warned', got {result.status!r}"
    )
    assert "DeprecationWarning" in result.message, (
        f"warned message should mention 'DeprecationWarning', got {result.message!r}"
    )


def test_async_test_skip(tmp: TempDir):
    f = tmp / "test_async_skip.py"
    f.write_text(
        "import oxitest\n"
        "@oxitest.mark.skip(reason='not ready')\n"
        "async def test_skip():\n"
        "    pass\n"
    )
    result = run_test(str(f), "test_skip")
    assert result.status == "skipped", (
        f"skipped async test should have status='skipped', got {result.status!r}"
    )
    assert "not ready" in result.message, (
        f"skip message should contain 'not ready', got {result.message!r}"
    )


def test_async_test_xfail(tmp: TempDir):
    f = tmp / "test_async_xfail.py"
    f.write_text(
        "import oxitest\n"
        "@oxitest.mark.xfail(reason='known bug')\n"
        "async def test_xfail():\n"
        "    assert 1 == 2\n"
    )
    result = run_test(str(f), "test_xfail")
    assert result.status == "xfailed", (
        f"xfail async test should have status='xfailed', got {result.status!r}"
    )


def test_async_test_xpass(tmp: TempDir):
    f = tmp / "test_async_xpass.py"
    f.write_text(
        "import oxitest\n"
        "@oxitest.mark.xfail(reason='expected to fail')\n"
        "async def test_xpass():\n"
        "    assert 1 == 1\n"
    )
    result = run_test(str(f), "test_xpass")
    assert result.status == "xpassed", (
        f"xpass async test should have status='xpassed', got {result.status!r}"
    )


def test_async_test_with_async_fixture(tmp: TempDir):
    f = tmp / "test_async_fx.py"
    f.write_text(
        "from oxitest import Fixture\n"
        "async def test_uses_val(val: Fixture[int]) -> None:\n"
        "    assert val == 99\n"
    )

    async def async_factory():
        return 99

    session = _make_session_with("val", async_factory)
    session.begin_module(str(f))
    result = run_test(str(f), "test_uses_val", session)
    assert result.status == "passed", (
        f"async test with async fixture should pass, got status={result.status!r}, "
        f"msg={result.message!r}"
    )


def test_async_test_with_sync_fixture(tmp: TempDir):
    f = tmp / "test_async_sync_fx.py"
    f.write_text(
        "from oxitest import Fixture\n"
        "async def test_uses_val(val: Fixture[int]) -> None:\n"
        "    assert val == 42\n"
    )
    session = _make_session_with("val", lambda: 42)
    session.begin_module(str(f))
    result = run_test(str(f), "test_uses_val", session)
    assert result.status == "passed", (
        f"async test with sync fixture should pass, got status={result.status!r}, "
        f"msg={result.message!r}"
    )


def test_async_fixture_setup_error(tmp: TempDir):
    f = tmp / "test_async_fx_err.py"
    f.write_text(
        "from oxitest import Fixture\n"
        "async def test_uses_bad(bad: Fixture[None]) -> None:\n"
        "    pass\n"
    )

    async def bad_factory():
        raise RuntimeError("db is down")

    session = _make_session_with("bad", bad_factory)
    session.begin_module(str(f))
    result = run_test(str(f), "test_uses_bad", session)
    assert result.status == "error", (
        f"async fixture setup error should produce status='error', "
        f"got {result.status!r}"
    )
    assert "db is down" in result.message, (
        f"error message should contain 'db is down', got {result.message!r}"
    )


def test_sync_test_with_async_fixture_produces_error(tmp: TempDir):
    f = tmp / "test_sync_async_fx.py"
    f.write_text(
        "from oxitest import Fixture\n"
        "def test_uses_val(val: Fixture[int]) -> None:\n"
        "    assert val == 99\n"
    )

    async def async_factory():
        return 99

    session = _make_session_with("val", async_factory)
    session.begin_module(str(f))
    result = run_test(str(f), "test_uses_val", session)
    assert result.status == "error", (
        f"sync test with async fixture should produce error, got {result.status!r}, "
        f"msg={result.message!r}"
    )
    assert "async fixture" in result.message.lower(), (
        f"error message should mention 'async fixture', got {result.message!r}"
    )
    assert "val" in result.message, (
        f"error message should mention fixture name 'val', got {result.message!r}"
    )
