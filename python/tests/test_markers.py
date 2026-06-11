from __future__ import annotations

import dataclasses

import oxitest
from conftest import helpers
from oxitest import TempDir, parametrize
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._mark_api import MarkInfo, _append_mark
from oxitest._bridge._mark_registry import (
    _MARK_REGISTRY,
    MarkEvalResult,
    MarkHandler,
    _HandlerContext,
    _PluginMarkHandler,
    _SkipHandler,
    _UsefixturesHandler,
    _XFailHandler,
    evaluate_marks,
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
    from oxitest._bridge._fn_metadata import get_metadata

    meta = get_metadata(fn)
    assert len(meta.marks) == 1, (
        f"expected 1 mark after first append, got {len(meta.marks)}"
    )
    assert meta.marks[0].name == "slow", (
        f"first mark name should be 'slow', got {meta.marks[0].name!r}"
    )


def test_append_mark_stacks_multiple_marks():
    def fn():
        pass

    _append_mark(fn, MarkInfo("slow", (), {}))
    _append_mark(fn, MarkInfo("integration", (), {}))
    from oxitest._bridge._fn_metadata import get_metadata

    assert len(get_metadata(fn).marks) == 2, (
        f"expected 2 marks after two appends, got {len(get_metadata(fn).marks)}"
    )


def test_mark_bare_decorator_stamps_function():
    @oxitest.mark.slow
    def test_fn():
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    meta = get_metadata(test_fn)
    assert len(meta.marks) == 1, "bare mark decorator should register mark in metadata"
    assert meta.marks[0].name == "slow", (
        f"mark name should be 'slow', got {meta.marks[0].name!r}"
    )
    assert meta.marks[0].args == (), (
        f"bare mark should have empty args, got {meta.marks[0].args!r}"
    )
    assert meta.marks[0].kwargs == {}, (
        f"bare mark should have empty kwargs, got {meta.marks[0].kwargs!r}"
    )


def test_mark_parameterised_decorator_stores_args():
    @oxitest.mark.skip(reason="not ready")
    def test_fn():
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    meta = get_metadata(test_fn)
    assert meta.marks[0].name == "skip", (
        f"mark name should be 'skip', got {meta.marks[0].name!r}"
    )
    assert meta.marks[0].kwargs == {"reason": "not ready"}, (
        f"mark kwargs should be {{'reason': 'not ready'}}, got {meta.marks[0].kwargs!r}"
    )


def test_mark_skip_when_true_stores_via_decorator():
    @oxitest.mark.skip(when=True, reason="always skip")
    def test_fn():
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    m = get_metadata(test_fn).marks[0]
    assert m.name == "skip", f"mark name should be 'skip', got {m.name!r}"
    assert m.kwargs == {"reason": "always skip"}, (
        f"skip kwargs should be {{'reason': 'always skip'}}, got {m.kwargs!r}"
    )


def test_skip_mark_rejects_positional_args():
    with oxitest.raises(TypeError, match="positional"):

        @oxitest.mark.skip(True, reason="nope")
        def test_fn():
            pass


def test_skip_mark_when_true_attaches_mark():
    @oxitest.mark.skip(when=True, reason="not ready")
    def test_fn():
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    meta = get_metadata(test_fn)
    assert len(meta.marks) == 1, f"expected 1 mark, got {len(meta.marks)}"
    assert meta.marks[0].name == "skip", (
        f"expected mark name 'skip', got {meta.marks[0].name!r}"
    )
    assert meta.marks[0].kwargs == {"reason": "not ready"}, (
        f"expected kwargs={{'reason': 'not ready'}}, got {meta.marks[0].kwargs!r}"
    )


def test_skip_mark_when_false_does_not_attach():
    @oxitest.mark.skip(when=False, reason="never")
    def test_fn():
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    assert len(get_metadata(test_fn).marks) == 0, (
        "when=False should not attach any mark"
    )


def test_skip_mark_bare_still_works():
    @oxitest.mark.skip
    def test_fn():
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    meta = get_metadata(test_fn)
    assert len(meta.marks) == 1, "bare @mark.skip should attach mark"
    assert meta.marks[0].name == "skip", (
        f"expected mark name 'skip', got {meta.marks[0].name!r}"
    )
    assert meta.marks[0].kwargs == {"reason": ""}, (
        f"bare @mark.skip should have reason='', got {meta.marks[0].kwargs!r}"
    )


def test_skip_mark_empty_parens_same_as_bare():
    @oxitest.mark.skip()
    def test_fn():
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    meta = get_metadata(test_fn)
    assert len(meta.marks) == 1, "@mark.skip() should attach mark"
    assert meta.marks[0].name == "skip", (
        f"expected mark name 'skip', got {meta.marks[0].name!r}"
    )
    assert meta.marks[0].kwargs == {"reason": ""}, (
        f"@mark.skip() should have reason='', got {meta.marks[0].kwargs!r}"
    )


def test_skip_mark_reason_only():
    @oxitest.mark.skip(reason="WIP")
    def test_fn():
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    meta = get_metadata(test_fn)
    assert meta.marks[0].kwargs == {"reason": "WIP"}, (
        f"expected kwargs={{'reason': 'WIP'}}, got {meta.marks[0].kwargs!r}"
    )


def test_skip_mark_rejects_unknown_kwargs():
    with oxitest.raises(TypeError, match="unexpected keyword"):

        @oxitest.mark.skip(bogus=True)
        def test_fn():
            pass


def test_mark_xfail_stores_strict_false():
    @oxitest.mark.xfail(strict=False, reason="flaky")
    def test_fn():
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    m = get_metadata(test_fn).marks[0]
    assert m.name == "xfail", f"mark name should be 'xfail', got {m.name!r}"
    assert m.kwargs == {"strict": False, "reason": "flaky"}, (
        f"xfail kwargs should be {{'strict': False, 'reason': 'flaky'}}, got "
        f"{m.kwargs!r}"
    )


def test_mark_usefixtures_stores_fixture_names():
    @oxitest.mark.usefixtures("db", "cache")
    def test_fn():
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    m = get_metadata(test_fn).marks[0]
    assert m.name == "usefixtures", f"mark name should be 'usefixtures', got {m.name!r}"
    assert m.args == ("db", "cache"), (
        f"usefixtures args should be ('db', 'cache'), got {m.args!r}"
    )


def test_mark_stacking_two_decorators():
    @oxitest.mark.slow
    @oxitest.mark.integration
    def test_fn():
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    names = [m.name for m in get_metadata(test_fn).marks]
    assert "slow" in names, f"'slow' should be in stacked marks, got {names}"
    assert "integration" in names, (
        f"'integration' should be in stacked marks, got {names}"
    )


# ── executor-level marker tests ───────────────────────────────────────────────


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
    skip_when_true=MarkerCase(
        code=(
            "@oxitest.mark.skip(when=True, reason='always')\n"
            "def test_foo(): assert False\n"
        ),
        expected_status="skipped",
    ),
    skip_when_false=MarkerCase(
        code="@oxitest.mark.skip(when=False, reason='never')\ndef test_foo(): pass\n",
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
    result = helpers.common.exec_inline(tmp, "import oxitest\n" + code, "test_foo")
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

    reg.register(helpers.common.make_fixture_def("my_fixture", side_effect_fixture))
    session = FixtureSession(reg)

    code = (
        "import oxitest\n"
        "@oxitest.mark.usefixtures('my_fixture')\n"
        "def test_foo(): pass\n"
    )
    result = helpers.common.exec_inline(
        tmp,
        code,
        "test_foo",
        session=session,
    )
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


def test_skip_when_false_not_in_marks():
    """when=False means no mark attached, so handler is never invoked."""

    @oxitest.mark.skip(when=False, reason="never")
    def test_fn():
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    assert len(get_metadata(test_fn).marks) == 0, (
        "when=False should not attach skip mark"
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
    expected = {"usefixtures", "skip", "xfail", "timeout"}
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
    result = helpers.common.exec_inline(
        tmp, "import oxitest\ndef test_foo(): assert False\n", "test_foo"
    )
    assert result.exc_type == "AssertionError", (
        f"expected exc_type='AssertionError', got {result.exc_type!r}"
    )


def test_exc_type_populated_on_runtime_error(tmp: TempDir) -> None:
    result = helpers.common.exec_inline(
        tmp, "import oxitest\ndef test_foo(): raise ValueError('boom')\n", "test_foo"
    )
    assert result.exc_type == "ValueError", (
        f"expected exc_type='ValueError', got {result.exc_type!r}"
    )


def test_exc_type_empty_on_pass(tmp: TempDir) -> None:
    result = helpers.common.exec_inline(
        tmp, "import oxitest\ndef test_foo(): pass\n", "test_foo"
    )
    assert result.exc_type == "", f"expected exc_type='', got {result.exc_type!r}"


class _FakePluginWrapper:
    marker = "custom_mark"

    def wrap(self, next_fn, args):
        result = next_fn()
        return dataclasses.replace(result, message=f"wrapped:{args}")


def test_plugin_mark_handler_wraps_correctly():
    pw = _FakePluginWrapper()
    handler = _PluginMarkHandler(pw)
    assert handler.mark_name == "custom_mark", (
        f"expected mark_name='custom_mark', got {handler.mark_name!r}"
    )
    mark = MarkInfo("custom_mark", ("arg1",), {"key": "val"})
    ctx = _HandlerContext(
        fn_raw=lambda: None,
        fn=lambda: None,
        all_kwargs={},
        session=FixtureSession(FixtureRegistry()),
        module_path="/fake.py",
        fn_teardowns=[],
    )
    result = handler.handle(mark, ctx)
    assert result.wrapper is not None, "handler should produce a wrapper"
    assert result.short_circuit is None, "handler should not short-circuit"

    # Execute the wrapper
    inner_result = TestResult(status=StatusKind.PASSED)
    wrapped_result = result.wrapper(lambda: inner_result)
    assert "wrapped:" in wrapped_result.message, (
        f"wrapper should modify message, got {wrapped_result.message!r}"
    )


def test_marker_composition_skip_takes_precedence_over_others():
    """When skip + xfail + timeout are all present, skip takes precedence."""
    marks = [
        MarkInfo("skip", (), {"reason": "not ready"}),
        MarkInfo("xfail", (), {"reason": "known bug"}),
        MarkInfo("timeout", (), {"seconds": 5}),
    ]
    ctx = _make_ctx()
    sc, wrappers = evaluate_marks(marks, ctx)

    assert sc is not None, "evaluate_marks with skip mark should return a short-circuit"
    assert sc.status == "skipped", (
        f"skip should take precedence; expected status='skipped', got {sc.status!r}"
    )
    assert sc.message == "not ready", (
        f"skip message should be 'not ready', got {sc.message!r}"
    )
    assert wrappers == [], (
        f"skip short-circuits before xfail/timeout wrappers are added, got {wrappers!r}"
    )


def test_evaluate_marks_dispatches_plugin_handlers():
    pw = _FakePluginWrapper()
    handler = _PluginMarkHandler(pw)
    marks = [MarkInfo("custom_mark", (), {})]
    ctx = _HandlerContext(
        fn_raw=lambda: None,
        fn=lambda: None,
        all_kwargs={},
        session=FixtureSession(FixtureRegistry()),
        module_path="/fake.py",
        fn_teardowns=[],
    )
    short_circuit, wrappers = evaluate_marks(marks, ctx, plugin_handlers=[handler])
    assert short_circuit is None, "should not short-circuit"
    assert len(wrappers) == 1, (
        f"expected 1 wrapper from plugin handler, got {len(wrappers)}"
    )
