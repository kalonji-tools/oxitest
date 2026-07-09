"""Tests for the mark API, mark decorators, handlers, and mark evaluation pipeline."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from types import MappingProxyType
from typing import Any

import oxitest
from oxitest import TempDir, helpers, parametrize
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._fn_metadata import get_metadata
from oxitest._bridge._mark_api import MarkInfo, _append_mark
from oxitest._bridge._mark_registry import (
    _MARK_REGISTRY,
    MarkEvalResult,
    MarkHandler,
    _PluginMarkHandler,
    _SkipHandler,
    _XFailHandler,
    evaluate_marks,
)
from oxitest._bridge.result import (
    FailedResult,
    PassedResult,
    SkippedResult,
    TestResult,
    WarnedResult,
)


def test_mark_info_stores_name_args_kwargs() -> None:
    """MarkInfo preserves name, args, and kwargs exactly as supplied."""
    m = MarkInfo("slow", (), MappingProxyType({}))
    assert m.name == "slow", f"expected name='slow', got {m.name!r}"
    assert m.args == (), f"expected args=(), got {m.args!r}"
    assert m.kwargs == {}, f"expected kwargs={{}}, got {m.kwargs!r}"


def test_append_mark_creates_list_on_first_call() -> None:
    """_append_mark initialises the marks list and stores the first mark."""

    def fn() -> None:
        pass

    _append_mark(fn, MarkInfo("slow", (), MappingProxyType({})))
    meta = get_metadata(fn)
    assert len(meta.marks) == 1, (
        f"expected 1 mark after first append, got {len(meta.marks)}"
    )
    assert meta.marks[0].name == "slow", (
        f"first mark name should be 'slow', got {meta.marks[0].name!r}"
    )


def test_append_mark_stacks_multiple_marks() -> None:
    """Each successive _append_mark call grows the marks list by one."""

    def fn() -> None:
        pass

    _append_mark(fn, MarkInfo("slow", (), MappingProxyType({})))
    _append_mark(fn, MarkInfo("integration", (), MappingProxyType({})))
    assert len(get_metadata(fn).marks) == 2, (
        f"expected 2 marks after two appends, got {len(get_metadata(fn).marks)}"
    )


def test_mark_bare_decorator_stamps_function() -> None:
    """Bare @mark.slow decorator attaches a MarkInfo with empty args/kwargs."""

    @oxitest.mark.slow
    def test_fn() -> None:
        pass

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


def test_mark_parameterised_decorator_stores_args() -> None:
    """@mark.skip(reason=...) stores kwargs in the MarkInfo correctly."""

    @oxitest.mark.skip(reason="not ready")
    def test_fn() -> None:
        pass

    meta = get_metadata(test_fn)
    assert meta.marks[0].name == "skip", (
        f"mark name should be 'skip', got {meta.marks[0].name!r}"
    )
    assert meta.marks[0].kwargs == {"reason": "not ready"}, (
        f"mark kwargs should be {{'reason': 'not ready'}}, got {meta.marks[0].kwargs!r}"
    )


def test_mark_skip_when_true_stores_via_decorator() -> None:
    """@mark.skip(when=True) stores a skip MarkInfo with the given reason."""

    @oxitest.mark.skip(when=True, reason="always skip")
    def test_fn() -> None:
        pass

    m = get_metadata(test_fn).marks[0]
    assert m.name == "skip", f"mark name should be 'skip', got {m.name!r}"
    assert m.kwargs == {"reason": "always skip"}, (
        f"skip kwargs should be {{'reason': 'always skip'}}, got {m.kwargs!r}"
    )


def test_skip_mark_rejects_positional_args() -> None:
    """@mark.skip raises TypeError when given a positional boolean argument."""
    with oxitest.raises(TypeError, match="positional"):

        @oxitest.mark.skip(True, reason="nope")  # noqa: FBT003
        def test_fn() -> None:
            pass


def test_skip_mark_when_true_attaches_mark() -> None:
    """@mark.skip(when=True) attaches exactly one skip mark with the supplied reason."""

    @oxitest.mark.skip(when=True, reason="not ready")
    def test_fn() -> None:
        pass

    meta = get_metadata(test_fn)
    assert len(meta.marks) == 1, f"expected 1 mark, got {len(meta.marks)}"
    assert meta.marks[0].name == "skip", (
        f"expected mark name 'skip', got {meta.marks[0].name!r}"
    )
    assert meta.marks[0].kwargs == {"reason": "not ready"}, (
        f"expected kwargs={{'reason': 'not ready'}}, got {meta.marks[0].kwargs!r}"
    )


def test_skip_mark_when_false_does_not_attach() -> None:
    """@mark.skip(when=False) is a no-op and leaves the marks list empty."""

    @oxitest.mark.skip(when=False, reason="never")
    def test_fn() -> None:
        pass

    assert len(get_metadata(test_fn).marks) == 0, (
        "when=False should not attach any mark"
    )


def test_skip_mark_bare_still_works() -> None:
    """Bare @mark.skip (no call) attaches a skip mark with an empty reason string."""

    @oxitest.mark.skip
    def test_fn() -> None:
        pass

    meta = get_metadata(test_fn)
    assert len(meta.marks) == 1, "bare @mark.skip should attach mark"
    assert meta.marks[0].name == "skip", (
        f"expected mark name 'skip', got {meta.marks[0].name!r}"
    )
    assert meta.marks[0].kwargs == {"reason": ""}, (
        f"bare @mark.skip should have reason='', got {meta.marks[0].kwargs!r}"
    )


def test_skip_mark_empty_parens_same_as_bare() -> None:
    """@mark.skip() with empty parens behaves identically to bare @mark.skip."""

    @oxitest.mark.skip()
    def test_fn() -> None:
        pass

    meta = get_metadata(test_fn)
    assert len(meta.marks) == 1, "@mark.skip() should attach mark"
    assert meta.marks[0].name == "skip", (
        f"expected mark name 'skip', got {meta.marks[0].name!r}"
    )
    assert meta.marks[0].kwargs == {"reason": ""}, (
        f"@mark.skip() should have reason='', got {meta.marks[0].kwargs!r}"
    )


def test_skip_mark_reason_only() -> None:
    """@mark.skip(reason=...) stores the reason kwarg and no other kwargs."""

    @oxitest.mark.skip(reason="WIP")
    def test_fn() -> None:
        pass

    meta = get_metadata(test_fn)
    assert meta.marks[0].kwargs == {"reason": "WIP"}, (
        f"expected kwargs={{'reason': 'WIP'}}, got {meta.marks[0].kwargs!r}"
    )


def test_skip_mark_rejects_unknown_kwargs() -> None:
    """@mark.skip raises TypeError when given an unrecognised keyword argument."""
    with oxitest.raises(TypeError, match="unexpected keyword"):

        @oxitest.mark.skip(bogus=True)
        def test_fn() -> None:
            pass


def test_mark_xfail_stores_strict_false() -> None:
    """@mark.xfail(strict=False) stores both strict and reason in kwargs."""

    @oxitest.mark.xfail(strict=False, reason="flaky")
    def test_fn() -> None:
        pass

    m = get_metadata(test_fn).marks[0]
    assert m.name == "xfail", f"mark name should be 'xfail', got {m.name!r}"
    assert m.kwargs == {"strict": False, "reason": "flaky"}, (
        f"xfail kwargs should be {{'strict': False, 'reason': 'flaky'}}, got "
        f"{m.kwargs!r}"
    )


def test_mark_usefixtures_stores_fixture_names() -> None:
    """@mark.usefixtures stores fixture names as positional args on the MarkInfo."""

    @oxitest.mark.usefixtures("db", "cache")
    def test_fn() -> None:
        pass

    m = get_metadata(test_fn).marks[0]
    assert m.name == "usefixtures", f"mark name should be 'usefixtures', got {m.name!r}"
    assert m.args == ("db", "cache"), (
        f"usefixtures args should be ('db', 'cache'), got {m.args!r}"
    )


def test_mark_stacking_two_decorators() -> None:
    """Stacking two mark decorators registers both marks on the function."""

    @oxitest.mark.slow
    @oxitest.mark.integration
    def test_fn() -> None:
        pass

    names = [m.name for m in get_metadata(test_fn).marks]
    assert "slow" in names, f"'slow' should be in stacked marks, got {names}"
    assert "integration" in names, (
        f"'integration' should be in stacked marks, got {names}"
    )


# ── executor-level marker tests ───────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class MarkerCase:
    """Parametrize case for marker executor result tests."""

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
    """Each mark variant produces the expected executor status when run."""
    result = helpers.common.exec_inline(tmp, "import oxitest\n" + code, "test_foo")
    assert result.status == expected_status, (
        f"mark executor result: expected status={expected_status!r}, "
        f"got {result.status!r} (message={result.message!r})"
    )
    if message_contains:
        assert message_contains in result.message, (
            f"expected {message_contains!r} in result.message, got {result.message!r}"
        )


def test_usefixtures_resolves_fixture(tmp: TempDir) -> None:
    """The usefixtures mark runs the named fixture, producing its side effects."""
    reg = FixtureRegistry()
    log: list[str] = []

    def side_effect_fixture() -> None:
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


def test_mark_eval_result_defaults() -> None:
    """MarkEvalResult() starts with short_circuit=None and wrapper=None."""
    r = MarkEvalResult()
    assert r.short_circuit is None, (
        f"MarkEvalResult() short_circuit should default to None, got "
        f"{r.short_circuit!r}"
    )
    assert r.wrapper is None, (
        f"MarkEvalResult() wrapper should default to None, got {r.wrapper!r}"
    )


def test_usefixtures_mark_resolves_via_evaluate_marks() -> None:
    """Usefixtures is resolved inline in evaluate_marks, not by a MarkHandler."""
    session = FixtureSession(FixtureRegistry())
    marks = [MarkInfo("usefixtures", (), MappingProxyType({}))]
    sc, wrappers = evaluate_marks(marks, session, "test_fake.py", [])
    assert sc is None, "usefixtures should not short-circuit"
    assert wrappers == [], "usefixtures should not produce wrappers"


def test_skip_handler_returns_short_circuit() -> None:
    """_SkipHandler.handle produces a skipped short-circuit result and no wrapper."""
    result = _SkipHandler().handle(
        MarkInfo("skip", (), MappingProxyType({"reason": "not ready"}))
    )
    assert result.short_circuit is not None, (
        "_SkipHandler should produce a short_circuit result"
    )
    assert result.short_circuit.status == "skipped", (
        f"_SkipHandler short_circuit status should be 'skipped', got "
        f"{result.short_circuit.status!r}"
    )
    helpers.common.assert_result(
        result.short_circuit, SkippedResult, message="not ready"
    )
    assert result.wrapper is None, "_SkipHandler should not produce a wrapper"


def test_skip_when_false_not_in_marks() -> None:
    """when=False means no mark attached, so handler is never invoked."""

    @oxitest.mark.skip(when=False, reason="never")
    def test_fn() -> None:
        pass

    assert len(get_metadata(test_fn).marks) == 0, (
        "when=False should not attach skip mark"
    )


def test_xfail_handler_returns_wrapper() -> None:
    """_XFailHandler.handle produces a wrapper function and no short-circuit."""
    result = _XFailHandler().handle(
        MarkInfo("xfail", (), MappingProxyType({"reason": "known bug"}))
    )
    assert result.short_circuit is None, "_XFailHandler should not short-circuit"
    assert result.wrapper is not None, "_XFailHandler should produce a wrapper function"


def test_xfail_wrapper_converts_failed_to_xfailed() -> None:
    """The xfail wrapper turns a failed inner result into an xfailed outcome."""
    result = _XFailHandler().handle(
        MarkInfo("xfail", (), MappingProxyType({"reason": "known bug"}))
    )
    assert result.wrapper is not None, "_XFailHandler should produce a wrapper"
    wrapper = result.wrapper
    failed_result = FailedResult(message="oops")
    assert wrapper(lambda: failed_result).status == "xfailed", (
        "xfail wrapper should convert 'failed' result to 'xfailed'"
    )


def test_xfail_wrapper_converts_passed_to_xpassed() -> None:
    """The xfail wrapper turns an unexpectedly passing inner result into xpassed."""
    result = _XFailHandler().handle(
        MarkInfo("xfail", (), MappingProxyType({"reason": "known"}))
    )
    assert result.wrapper is not None, "_XFailHandler should produce a wrapper"
    wrapper = result.wrapper
    passed_result = PassedResult()
    assert wrapper(lambda: passed_result).status == "xpassed", (
        "xfail wrapper should convert unexpectedly 'passed' result to 'xpassed'"
    )


def test_xfail_wrapper_passes_through_skipped() -> None:
    """The xfail wrapper leaves a skipped inner result unchanged."""
    result = _XFailHandler().handle(MarkInfo("xfail", (), MappingProxyType({})))
    assert result.wrapper is not None, "_XFailHandler should produce a wrapper"
    wrapper = result.wrapper
    skipped_result = SkippedResult(message="not my test")
    final = wrapper(lambda: skipped_result)
    assert final.status == "skipped", (
        f"xfail wrapper should pass through 'skipped' result unchanged, got "
        f"{final.status!r}"
    )


def test_evaluate_marks_returns_tuple() -> None:
    """evaluate_marks with no marks returns (None, [])."""
    sc, wrappers = evaluate_marks(
        [], FixtureSession(FixtureRegistry()), "test_fake.py", []
    )
    assert sc is None, f"evaluate_marks with no marks should return sc=None, got {sc!r}"
    assert wrappers == [], (
        f"evaluate_marks with no marks should return empty wrappers, got {wrappers!r}"
    )


def test_evaluate_marks_skip_returns_short_circuit() -> None:
    """evaluate_marks with a skip mark short-circuits to a skipped result."""
    sc, wrappers = evaluate_marks(
        [MarkInfo("skip", (), MappingProxyType({"reason": "x"}))],
        FixtureSession(FixtureRegistry()),
        "test_fake.py",
        [],
    )
    assert sc is not None, "evaluate_marks with skip mark should return a short-circuit"
    assert sc.status == "skipped", (
        f"evaluate_marks skip short-circuit status should be 'skipped', got "
        f"{sc.status!r}"
    )
    assert wrappers == [], (
        f"evaluate_marks with skip should return no wrappers, got {wrappers!r}"
    )


# ── _MARK_REGISTRY ────────────────────────────────────────────────────────────


def test_all_builtin_handlers_registered() -> None:
    """_MARK_REGISTRY contains exactly the three built-in mark handlers."""
    expected = {"skip", "xfail", "timeout"}
    assert set(_MARK_REGISTRY.keys()) == expected, (
        f"expected builtin mark handlers {expected}, got {set(_MARK_REGISTRY.keys())}"
    )


def test_registered_handlers_are_mark_handler_instances() -> None:
    """Every entry in _MARK_REGISTRY is a MarkHandler subclass instance."""
    for name, handler in _MARK_REGISTRY.items():
        assert isinstance(handler, MarkHandler), (
            f"handler for mark {name!r} should be a MarkHandler instance, "
            f"got {type(handler).__name__}"
        )


def test_handler_with_unknown_mark_name_not_in_registry() -> None:
    """An arbitrary unknown mark name is absent from _MARK_REGISTRY."""
    assert "nonexistent_mark" not in _MARK_REGISTRY, (
        "unknown mark name 'nonexistent_mark' should not be in _MARK_REGISTRY"
    )


def test_each_handler_has_mark_name_class_attr() -> None:
    """Each registered handler exposes a mark_name attribute matching its key."""
    for name, handler in _MARK_REGISTRY.items():
        assert hasattr(handler, "mark_name"), (
            f"{type(handler).__name__} missing mark_name class attribute"
        )
        assert handler.mark_name == name, (
            f"handler.mark_name should match registry key {name!r}, got "
            f"{handler.mark_name!r}"
        )


def test_exc_type_populated_on_assertion_error(tmp: TempDir) -> None:
    """A failing assertion populates exc_type with 'AssertionError'."""
    result = helpers.common.exec_inline(
        tmp, "import oxitest\ndef test_foo(): assert False\n", "test_foo"
    )
    assert result.exc_type == "AssertionError", (
        f"expected exc_type='AssertionError', got {result.exc_type!r}"
    )


def test_exc_type_populated_on_runtime_error(tmp: TempDir) -> None:
    """An uncaught runtime exception sets exc_type to the exception class name."""
    result = helpers.common.exec_inline(
        tmp, "import oxitest\ndef test_foo(): raise ValueError('boom')\n", "test_foo"
    )
    assert result.exc_type == "ValueError", (
        f"expected exc_type='ValueError', got {result.exc_type!r}"
    )


def test_exc_type_absent_on_pass(tmp: TempDir) -> None:
    """A passing test produces a PassedResult, which has no exc_type attribute."""
    result = helpers.common.exec_inline(
        tmp, "import oxitest\ndef test_foo(): pass\n", "test_foo"
    )
    assert not hasattr(result, "exc_type"), (
        f"PassedResult should not have exc_type, got "
        f"{getattr(result, 'exc_type', None)!r}"
    )


class _FakePluginWrapper:
    """Minimal stand-in for a plugin that wraps test results."""

    marker = "custom_mark"

    def wrap(
        self, next_fn: Callable[[], TestResult], args: dict[int | str, Any]
    ) -> TestResult:
        result = next_fn()
        return dataclasses.replace(result, message=f"wrapped:{args}")


def test_plugin_mark_handler_wraps_correctly() -> None:
    """_PluginMarkHandler delegates wrapping to the plugin and returns a wrapper."""
    pw = _FakePluginWrapper()
    handler = _PluginMarkHandler(pw)
    assert handler.mark_name == "custom_mark", (
        f"expected mark_name='custom_mark', got {handler.mark_name!r}"
    )
    mark = MarkInfo("custom_mark", ("arg1",), MappingProxyType({"key": "val"}))
    result = handler.handle(mark)
    assert result.wrapper is not None, "handler should produce a wrapper"
    assert result.short_circuit is None, "handler should not short-circuit"

    # Execute the wrapper — use WarnedResult since it has a message field
    inner_result = WarnedResult(message="original")
    wrapped_result = result.wrapper(lambda: inner_result)
    r = helpers.common.assert_result(wrapped_result, WarnedResult)
    assert "wrapped:" in r.message, f"wrapper should modify message, got {r.message!r}"


def test_marker_composition_skip_takes_precedence_over_others() -> None:
    """When skip + xfail + timeout are all present, skip takes precedence."""
    marks = [
        MarkInfo("skip", (), MappingProxyType({"reason": "not ready"})),
        MarkInfo("xfail", (), MappingProxyType({"reason": "known bug"})),
        MarkInfo("timeout", (), MappingProxyType({"seconds": 5})),
    ]
    sc, wrappers = evaluate_marks(
        marks, FixtureSession(FixtureRegistry()), "test_fake.py", []
    )

    assert sc is not None, "evaluate_marks with skip mark should return a short-circuit"
    assert sc.status == "skipped", (
        f"skip should take precedence; expected status='skipped', got {sc.status!r}"
    )
    helpers.common.assert_result(sc, SkippedResult, message="not ready")
    assert wrappers == [], (
        f"skip short-circuits before xfail/timeout wrappers are added, got {wrappers!r}"
    )


def test_evaluate_marks_dispatches_plugin_handlers() -> None:
    """evaluate_marks routes plugin marks through the supplied plugin_handlers list."""
    pw = _FakePluginWrapper()
    handler = _PluginMarkHandler(pw)
    marks = [MarkInfo("custom_mark", (), MappingProxyType({}))]
    short_circuit, wrappers = evaluate_marks(
        marks,
        FixtureSession(FixtureRegistry()),
        "/fake.py",
        [],
        plugin_handlers=[handler],
    )
    assert short_circuit is None, "should not short-circuit"
    assert len(wrappers) == 1, (
        f"expected 1 wrapper from plugin handler, got {len(wrappers)}"
    )
