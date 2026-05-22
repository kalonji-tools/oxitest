from __future__ import annotations

import dataclasses

import oxitest
from oxitest import TempDir, parametrize
from oxitest._bridge._mark_api import MarkInfo, _append_mark
from oxitest._bridge._mark_registry import (
    _MARK_REGISTRY,
    MarkEvalResult,
    MarkHandler,
    _HandlerContext,
    _SkipHandler,
    _SkipIfHandler,
    _UsefixturesHandler,
    _XFailHandler,
    evaluate_marks,
)
from oxitest._bridge.executor import run_test
from oxitest._bridge.fixtures import (
    FixtureDef,
    FixtureRegistry,
    FixtureSession,
)
from oxitest._bridge.result import StatusKind, TestResult


def test_mark_info_stores_name_args_kwargs():
    m = MarkInfo("slow", (), {})
    assert m.name == "slow", f"expected name='slow', got {m.name!r}"
    assert m.args == (), f"expected args=(), got {m.args!r}"
    assert m.kwargs == {}, f"expected kwargs={{}}, got {m.kwargs!r}"


def test_append_mark_creates_list_on_first_call():
    def fn():
        pass

    _append_mark(fn, MarkInfo("slow", (), {}))
    assert hasattr(fn, "_oxitest_marks"), (
        "fn should have '_oxitest_marks' attribute after first _append_mark call"
    )
    assert len(fn._oxitest_marks) == 1, (
        f"expected 1 mark after first append, got {len(fn._oxitest_marks)}"
    )
    assert fn._oxitest_marks[0].name == "slow", (
        f"first mark name should be 'slow', got {fn._oxitest_marks[0].name!r}"
    )


def test_append_mark_stacks_multiple_marks():
    def fn():
        pass

    _append_mark(fn, MarkInfo("slow", (), {}))
    _append_mark(fn, MarkInfo("integration", (), {}))
    assert len(getattr(fn, "_oxitest_marks", [])) == 2, (
        f"expected 2 marks after two appends, got "
        f"{len(getattr(fn, '_oxitest_marks', []))}"
    )


def test_mark_bare_decorator_stamps_function():
    @oxitest.mark.slow
    def test_fn():
        pass

    assert hasattr(test_fn, "_oxitest_marks"), (
        "bare mark decorator should stamp '_oxitest_marks' on function"
    )
    assert test_fn._oxitest_marks[0].name == "slow", (
        f"mark name should be 'slow', got {test_fn._oxitest_marks[0].name!r}"
    )
    assert test_fn._oxitest_marks[0].args == (), (
        f"bare mark should have empty args, got {test_fn._oxitest_marks[0].args!r}"
    )
    assert test_fn._oxitest_marks[0].kwargs == {}, (
        f"bare mark should have empty kwargs, got {test_fn._oxitest_marks[0].kwargs!r}"
    )


def test_mark_parameterised_decorator_stores_args():
    @oxitest.mark.skip(reason="not ready")
    def test_fn():
        pass

    assert test_fn._oxitest_marks[0].name == "skip", (
        f"mark name should be 'skip', got {test_fn._oxitest_marks[0].name!r}"
    )
    assert test_fn._oxitest_marks[0].kwargs == {"reason": "not ready"}, (
        f"mark kwargs should be {{'reason': 'not ready'}}, got "
        f"{test_fn._oxitest_marks[0].kwargs!r}"
    )


def test_mark_skipif_stores_condition():
    @oxitest.mark.skipif(True, reason="always skip")
    def test_fn():
        pass

    m = test_fn._oxitest_marks[0]
    assert m.name == "skipif", f"mark name should be 'skipif', got {m.name!r}"
    assert m.args == (True,), f"skipif args should be (True,), got {m.args!r}"
    assert m.kwargs == {"reason": "always skip"}, (
        f"skipif kwargs should be {{'reason': 'always skip'}}, got {m.kwargs!r}"
    )


def test_mark_xfail_stores_strict_false():
    @oxitest.mark.xfail(strict=False, reason="flaky")
    def test_fn():
        pass

    m = test_fn._oxitest_marks[0]
    assert m.name == "xfail", f"mark name should be 'xfail', got {m.name!r}"
    assert m.kwargs == {"strict": False, "reason": "flaky"}, (
        f"xfail kwargs should be {{'strict': False, 'reason': 'flaky'}}, got "
        f"{m.kwargs!r}"
    )


def test_mark_usefixtures_stores_fixture_names():
    @oxitest.mark.usefixtures("db", "cache")
    def test_fn():
        pass

    m = test_fn._oxitest_marks[0]
    assert m.name == "usefixtures", f"mark name should be 'usefixtures', got {m.name!r}"
    assert m.args == ("db", "cache"), (
        f"usefixtures args should be ('db', 'cache'), got {m.args!r}"
    )


def test_mark_stacking_two_decorators():
    @oxitest.mark.slow
    @oxitest.mark.integration
    def test_fn():
        pass

    names = [m.name for m in test_fn._oxitest_marks]
    assert "slow" in names, f"'slow' should be in stacked marks, got {names}"
    assert "integration" in names, (
        f"'integration' should be in stacked marks, got {names}"
    )


# ── executor-level marker tests ───────────────────────────────────────────────


def _write_test(tmp: TempDir, code: str) -> str:
    f = tmp / "test_exec.py"
    f.write_text("import oxitest\n" + code)
    return str(f)


@dataclasses.dataclass(frozen=True)
class MarkerCase:
    code: str
    expected_status: str
    message_contains: str = ""


@parametrize(
    skip_with_reason=MarkerCase(
        code="@oxitest.mark.skip(reason='not ready')\ndef test_foo(): assert False\n",
        expected_status="skipped",
        message_contains="not ready",
    ),
    skip_bare=MarkerCase(
        code="@oxitest.mark.skip\ndef test_foo(): assert False\n",
        expected_status="skipped",
    ),
    skipif_true=MarkerCase(
        code=(
            "@oxitest.mark.skipif(True, reason='always')\n"
            "def test_foo(): assert False\n"
        ),
        expected_status="skipped",
    ),
    skipif_false=MarkerCase(
        code="@oxitest.mark.skipif(False, reason='never')\ndef test_foo(): pass\n",
        expected_status="passed",
    ),
    xfail_failing=MarkerCase(
        code="@oxitest.mark.xfail(reason='known bug')\ndef test_foo(): assert False\n",
        expected_status="xfailed",
    ),
    xfail_passing_default=MarkerCase(
        code="@oxitest.mark.xfail\ndef test_foo(): pass\n",
        expected_status="xpassed",
    ),
    xfail_passing_strict_false=MarkerCase(
        code="@oxitest.mark.xfail(strict=False)\ndef test_foo(): pass\n",
        expected_status="xpassed",
    ),
    xfail_skipped_inside=MarkerCase(
        code=(
            "import unittest\n"
            "@oxitest.mark.xfail\n"
            "def test_foo(): raise unittest.SkipTest('skipped inside xfail')\n"
        ),
        expected_status="skipped",
    ),
    xfail_raises_matching=MarkerCase(
        code=(
            "@oxitest.mark.xfail(raises=ValueError, reason='known')\n"
            "def test_foo(): raise ValueError('boom')\n"
        ),
        expected_status="xfailed",
    ),
    xfail_raises_not_matching=MarkerCase(
        code=(
            "@oxitest.mark.xfail(raises=TypeError, reason='known')\n"
            "def test_foo(): raise ValueError('boom')\n"
        ),
        expected_status="error",
    ),
    xfail_raises_assertion_not_matching=MarkerCase(
        code=(
            "@oxitest.mark.xfail(raises=ValueError, reason='known')\n"
            "def test_foo(): assert False\n"
        ),
        expected_status="failed",
    ),
    xfail_raises_assertion_matching=MarkerCase(
        code=(
            "@oxitest.mark.xfail(raises=AssertionError, reason='known')\n"
            "def test_foo(): assert False\n"
        ),
        expected_status="xfailed",
    ),
    xfail_raises_passes=MarkerCase(
        code=(
            "@oxitest.mark.xfail(raises=ValueError, reason='known')\n"
            "def test_foo(): pass\n"
        ),
        expected_status="xpassed",
    ),
)
def test_mark_executor_result(
    tmp: TempDir, code: str, expected_status: str, message_contains: str
) -> None:
    path = _write_test(tmp, code)
    result = run_test(path, "test_foo")
    assert result.status == expected_status, (
        f"mark executor result: expected status={expected_status!r}, "
        f"got {result.status!r} (message={result.message!r})"
    )
    if message_contains:
        assert message_contains in result.message, (
            f"expected {message_contains!r} in result.message, got {result.message!r}"
        )


def test_usefixtures_resolves_fixture(tmp: TempDir):
    """usefixtures mark causes the fixture to run (side effects happen)."""
    reg = FixtureRegistry()
    log: list[str] = []

    def side_effect_fixture():
        log.append("setup")

    reg.register(
        FixtureDef(
            name="my_fixture",
            func=side_effect_fixture,
            autouse=False,
            params=None,
            conftest_path="",
        )
    )
    session = FixtureSession(reg)
    session.begin_module(str(tmp / "test_exec.py"))

    path = _write_test(
        tmp,
        "@oxitest.mark.usefixtures('my_fixture')\ndef test_foo(): pass\n",
    )
    result = run_test(path, "test_foo", session)
    assert result.status == "passed", (  # usefixtures does not short-circuit
        f"@mark.usefixtures should not short-circuit, expected status='passed', got "
        f"{result.status!r}"
    )
    assert log == ["setup"], (
        f"usefixtures fixture should have run (side effect), got log={log!r}"
    )


def _make_ctx(fn=None):
    """Minimal _HandlerContext for mark tests."""
    from oxitest._bridge._fixture_session import _NullFixtureSession
    from oxitest._bridge._mark_registry import _HandlerContext

    if fn is None:

        def fn():
            pass

    return _HandlerContext(
        fn_raw=fn,
        fn=fn,
        all_kwargs={},
        session=_NullFixtureSession(),
        module_path="test_fake.py",
        fn_teardowns=[],
        default_timeout=None,
    )


def test_mark_eval_result_defaults():
    r = MarkEvalResult()
    assert r.short_circuit is None, (
        f"MarkEvalResult() short_circuit should default to None, got "
        f"{r.short_circuit!r}"
    )
    assert r.wrapper is None, (
        f"MarkEvalResult() wrapper should default to None, got {r.wrapper!r}"
    )


def test_handler_context_is_dataclass():
    assert dataclasses.is_dataclass(_HandlerContext), (
        "_HandlerContext should be a dataclass"
    )


def test_usefixtures_handler_always_returns_none():
    ctx = _make_ctx()
    result = _UsefixturesHandler().handle(MarkInfo("usefixtures", (), {}), ctx)
    assert result.short_circuit is None, (
        "_UsefixturesHandler should never short-circuit"
    )
    assert result.wrapper is None, "_UsefixturesHandler should return no wrapper"


def test_skip_handler_returns_short_circuit():
    ctx = _make_ctx()
    result = _SkipHandler().handle(MarkInfo("skip", (), {"reason": "not ready"}), ctx)
    assert result.short_circuit is not None, (
        "_SkipHandler should produce a short_circuit result"
    )
    assert result.short_circuit.status == "skipped", (
        f"_SkipHandler short_circuit status should be 'skipped', got "
        f"{result.short_circuit.status!r}"
    )
    assert result.short_circuit.message == "not ready", (
        f"_SkipHandler message should be 'not ready', got "
        f"{result.short_circuit.message!r}"
    )
    assert result.wrapper is None, "_SkipHandler should not produce a wrapper"


def test_skipif_handler_short_circuits_when_true():
    ctx = _make_ctx()
    result = _SkipIfHandler().handle(MarkInfo("skipif", (True,), {"reason": "no"}), ctx)
    assert result.short_circuit is not None, (
        "_SkipIfHandler with condition=True should short-circuit"
    )
    assert result.short_circuit.status == "skipped", (
        f"_SkipIfHandler(True) short_circuit status should be 'skipped', got "
        f"{result.short_circuit.status!r}"
    )
    assert result.short_circuit.message == "no", (
        f"_SkipIfHandler(True) message should be 'no', got "
        f"{result.short_circuit.message!r}"
    )


def test_skipif_handler_passes_when_false():
    ctx = _make_ctx()
    result = _SkipIfHandler().handle(
        MarkInfo("skipif", (False,), {"reason": "no"}), ctx
    )
    assert result.short_circuit is None, (
        "_SkipIfHandler with condition=False should NOT short-circuit"
    )
    assert result.wrapper is None, (
        "_SkipIfHandler with condition=False should produce no wrapper"
    )


def test_xfail_handler_returns_wrapper():
    ctx = _make_ctx()
    result = _XFailHandler().handle(MarkInfo("xfail", (), {"reason": "known bug"}), ctx)
    assert result.short_circuit is None, "_XFailHandler should not short-circuit"
    assert result.wrapper is not None, "_XFailHandler should produce a wrapper function"


def test_xfail_wrapper_converts_failed_to_xfailed():
    ctx = _make_ctx()
    result = _XFailHandler().handle(MarkInfo("xfail", (), {"reason": "known bug"}), ctx)
    assert result.wrapper is not None, "_XFailHandler should produce a wrapper"
    wrapper = result.wrapper
    failed_result = TestResult(status=StatusKind.FAILED, message="oops")
    assert wrapper(lambda: failed_result).status == "xfailed", (
        "xfail wrapper should convert 'failed' result to 'xfailed'"
    )


def test_xfail_wrapper_converts_passed_to_xpassed():
    ctx = _make_ctx()
    result = _XFailHandler().handle(MarkInfo("xfail", (), {"reason": "known"}), ctx)
    assert result.wrapper is not None, "_XFailHandler should produce a wrapper"
    wrapper = result.wrapper
    passed_result = TestResult(status=StatusKind.PASSED)
    assert wrapper(lambda: passed_result).status == "xpassed", (
        "xfail wrapper should convert unexpectedly 'passed' result to 'xpassed'"
    )


def test_xfail_wrapper_passes_through_skipped():
    ctx = _make_ctx()
    result = _XFailHandler().handle(MarkInfo("xfail", (), {}), ctx)
    assert result.wrapper is not None, "_XFailHandler should produce a wrapper"
    wrapper = result.wrapper
    skipped_result = TestResult(status=StatusKind.SKIPPED, message="not my test")
    final = wrapper(lambda: skipped_result)
    assert final.status == "skipped", (
        f"xfail wrapper should pass through 'skipped' result unchanged, got "
        f"{final.status!r}"
    )


def test_evaluate_marks_returns_tuple():
    sc, wrappers = evaluate_marks([], _make_ctx())
    assert sc is None, f"evaluate_marks with no marks should return sc=None, got {sc!r}"
    assert wrappers == [], (
        f"evaluate_marks with no marks should return empty wrappers, got {wrappers!r}"
    )


def test_evaluate_marks_skip_returns_short_circuit():
    sc, wrappers = evaluate_marks([MarkInfo("skip", (), {"reason": "x"})], _make_ctx())
    assert sc is not None, "evaluate_marks with skip mark should return a short-circuit"
    assert sc.status == "skipped", (
        f"evaluate_marks skip short-circuit status should be 'skipped', got "
        f"{sc.status!r}"
    )
    assert wrappers == [], (
        f"evaluate_marks with skip should return no wrappers, got {wrappers!r}"
    )


# ── _MARK_REGISTRY ────────────────────────────────────────────────────────────


def test_all_builtin_handlers_registered():
    expected = {"usefixtures", "skip", "skipif", "xfail", "timeout"}
    assert set(_MARK_REGISTRY.keys()) == expected, (
        f"expected builtin mark handlers {expected}, got {set(_MARK_REGISTRY.keys())}"
    )


def test_registered_handlers_are_mark_handler_instances():
    for name, handler in _MARK_REGISTRY.items():
        assert isinstance(handler, MarkHandler), (
            f"handler for mark {name!r} should be a MarkHandler instance, "
            f"got {type(handler).__name__}"
        )


def test_handler_with_unknown_mark_name_not_in_registry():
    assert "nonexistent_mark" not in _MARK_REGISTRY, (
        "unknown mark name 'nonexistent_mark' should not be in _MARK_REGISTRY"
    )


def test_each_handler_has_mark_name_class_attr():
    for name, handler in _MARK_REGISTRY.items():
        assert hasattr(handler, "mark_name"), (
            f"{type(handler).__name__} missing mark_name class attribute"
        )
        assert handler.mark_name == name, (
            f"handler.mark_name should match registry key {name!r}, got "
            f"{handler.mark_name!r}"
        )


def test_exc_type_populated_on_assertion_error(tmp: TempDir) -> None:
    path = _write_test(tmp, "def test_foo(): assert False\n")
    result = run_test(path, "test_foo")
    assert result.exc_type == "AssertionError", (
        f"expected exc_type='AssertionError', got {result.exc_type!r}"
    )


def test_exc_type_populated_on_runtime_error(tmp: TempDir) -> None:
    path = _write_test(tmp, "def test_foo(): raise ValueError('boom')\n")
    result = run_test(path, "test_foo")
    assert result.exc_type == "ValueError", (
        f"expected exc_type='ValueError', got {result.exc_type!r}"
    )


def test_exc_type_empty_on_pass(tmp: TempDir) -> None:
    path = _write_test(tmp, "def test_foo(): pass\n")
    result = run_test(path, "test_foo")
    assert result.exc_type == "", f"expected exc_type='', got {result.exc_type!r}"
