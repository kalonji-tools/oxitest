"""Tests for the mark API, mark decorators, handlers, and mark evaluation pipeline."""

from __future__ import annotations

import dataclasses
import time
import unittest
from collections.abc import Callable
from types import MappingProxyType
from typing import Any

import oxitest
import oxitest as oxi
from oxitest import TempDir, parametrize, raises
from oxitest._bridge._fn_metadata import get_metadata
from oxitest._bridge._mark_api import MarkInfo, _append_mark
from oxitest._bridge._mark_registry import (
    _MARK_REGISTRY,
    MarkHandler,
    MarksHalt,
    MarksProceed,
    PassThrough,
    ShortCircuit,
    Wrap,
    _PluginMarkHandler,
    _SkipHandler,
    _TimeoutHandler,
    _XFailHandler,
    evaluate_marks,
)
from oxitest._bridge.result import (
    ErrorResult,
    FailedResult,
    PassedResult,
    SkippedResult,
    TestResult,
    TimeoutResult,
    WarnedResult,
    XFailedResult,
    XPassedResult,
)
from tests import helpers


def test_mark_info_stores_name_args_kwargs() -> None:
    """MarkInfo preserves name, args, and kwargs exactly as supplied."""
    m = MarkInfo("slow", (), MappingProxyType({}))
    assert m.name == "slow", (
        "MarkInfo must preserve the exact name supplied at construction"
    )
    assert m.args == (), (
        "MarkInfo constructed with empty args must retain an empty tuple"
    )
    assert m.kwargs == {}, (
        "MarkInfo constructed with empty kwargs must retain an empty mapping"
    )


def test_append_mark_creates_list_on_first_call() -> None:
    """_append_mark initialises the marks list and stores the first mark."""

    def fn() -> None:
        pass

    _append_mark(fn, MarkInfo("slow", (), MappingProxyType({})))
    meta = get_metadata(fn)
    assert len(meta.marks) == 1, (
        "_append_mark must initialise the marks list on the first call"
    )
    assert meta.marks[0].name == "slow", (
        "_append_mark must store the MarkInfo verbatim so evaluate_marks can route it"
    )


def test_append_mark_stacks_multiple_marks() -> None:
    """Each successive _append_mark call grows the marks list by one."""

    def fn() -> None:
        pass

    _append_mark(fn, MarkInfo("slow", (), MappingProxyType({})))
    _append_mark(fn, MarkInfo("integration", (), MappingProxyType({})))
    assert len(get_metadata(fn).marks) == 2, (
        "each _append_mark call must grow the list so stacked decorators all register"
    )


def test_mark_bare_decorator_stamps_function() -> None:
    """Bare @mark.slow decorator attaches a MarkInfo with empty args/kwargs."""

    @oxitest.mark.slow
    def test_fn() -> None:
        pass

    meta = get_metadata(test_fn)
    assert len(meta.marks) == 1, "bare mark decorator should register mark in metadata"
    assert meta.marks[0].name == "slow", (
        "bare decorator must record the attribute name so evaluate_marks can route it"
    )
    assert meta.marks[0].args == (), (
        "bare decorator form has no call site, so args must be empty"
    )
    assert meta.marks[0].kwargs == {}, (
        "bare decorator form has no call site, so kwargs must be empty"
    )


def test_mark_parameterised_decorator_stores_args() -> None:
    """@mark.skip(reason=...) stores kwargs in the MarkInfo correctly."""

    @oxitest.mark.skip(reason="not ready")
    def test_fn() -> None:
        pass

    meta = get_metadata(test_fn)
    assert meta.marks[0].name == "skip", (
        "parameterised skip must register under 'skip' so evaluate_marks routes it to"
        " _SkipHandler"
    )
    assert meta.marks[0].kwargs == {"reason": "not ready"}, (
        "parameterised decorator must forward its kwargs verbatim so the handler"
        " receives them"
    )


def test_mark_skip_when_true_stores_via_decorator() -> None:
    """@mark.skip(when=True) stores a skip MarkInfo with the given reason."""

    @oxitest.mark.skip(when=True, reason="always skip")
    def test_fn() -> None:
        pass

    m = get_metadata(test_fn).marks[0]
    assert m.name == "skip", (
        "skip(when=True) must register under 'skip' so evaluate_marks routes it to"
        " _SkipHandler"
    )
    assert m.kwargs == {"reason": "always skip"}, (
        "when=True must strip the 'when' kwarg and keep only 'reason' for the handler"
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
    assert len(meta.marks) == 1, (
        "when=True must attach exactly one mark — duplicates would double-skip"
    )
    assert meta.marks[0].name == "skip", (
        "when=True must register under 'skip' so the handler recognises it"
    )
    assert meta.marks[0].kwargs == {"reason": "not ready"}, (
        "the reason must survive the when-gate so the skip message is meaningful to the"
        " user"
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
        "bare @mark.skip must still register as 'skip' so evaluate_marks routes it"
        " correctly"
    )
    assert meta.marks[0].kwargs == {"reason": ""}, (
        "bare form must default reason to '' so _SkipHandler always receives a reason"
        " key"
    )


def test_skip_mark_empty_parens_same_as_bare() -> None:
    """@mark.skip() with empty parens behaves identically to bare @mark.skip."""

    @oxitest.mark.skip()
    def test_fn() -> None:
        pass

    meta = get_metadata(test_fn)
    assert len(meta.marks) == 1, "@mark.skip() should attach mark"
    assert meta.marks[0].name == "skip", (
        "empty-parens form must behave identically to bare — both route through"
        " _SkipHandler"
    )
    assert meta.marks[0].kwargs == {"reason": ""}, (
        "empty-parens must default reason to '' so the two forms are interchangeable"
    )


def test_skip_mark_reason_only() -> None:
    """@mark.skip(reason=...) stores the reason kwarg and no other kwargs."""

    @oxitest.mark.skip(reason="WIP")
    def test_fn() -> None:
        pass

    meta = get_metadata(test_fn)
    assert meta.marks[0].kwargs == {"reason": "WIP"}, (
        "reason-only call must store only 'reason' — extra keys would confuse"
        " _SkipHandler"
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
    assert m.name == "xfail", (
        "xfail decorator must register under 'xfail' so _XFailHandler receives it"
    )
    assert m.kwargs == {"strict": False, "reason": "flaky"}, (
        "strict and reason must both survive so _XFailHandler can decide pass-vs-fail"
        " semantics"
    )


def test_mark_stacking_two_decorators() -> None:
    """Stacking two mark decorators registers both marks on the function."""

    @oxitest.mark.slow
    @oxitest.mark.integration
    def test_fn() -> None:
        pass

    names = [m.name for m in get_metadata(test_fn).marks]
    assert "slow" in names, (
        "stacked decorators must all register — dropping one silently skips its handler"
    )
    assert "integration" in names, (
        "stacked decorators must all register — dropping one silently skips its handler"
    )


# ── executor-level marker tests ───────────────────────────────────────────────


# The variants reachable through the mark pipeline. Carrying the variant itself
# rather than a status string lets assert_result do the runtime check. Narrowing
# only reaches this union, so field access below needs getattr -- XPassedResult
# has no .message.
_MarkerResult = (
    PassedResult
    | FailedResult
    | ErrorResult
    | SkippedResult
    | XFailedResult
    | XPassedResult
)


@dataclasses.dataclass(frozen=True)
class MarkerCase:
    """Parametrize case for marker executor result tests."""

    code: str
    expected_type: type[_MarkerResult]
    message_contains: str = ""


@parametrize(
    skip_with_reason=MarkerCase(
        code="@oxitest.mark.skip(reason='not ready')\ndef test_foo(): assert False\n",
        expected_type=SkippedResult,
        message_contains="not ready",
    ),
    skip_bare=MarkerCase(
        code="@oxitest.mark.skip\ndef test_foo(): assert False\n",
        expected_type=SkippedResult,
    ),
    skip_when_true=MarkerCase(
        code=(
            "@oxitest.mark.skip(when=True, reason='always')\n"
            "def test_foo(): assert False\n"
        ),
        expected_type=SkippedResult,
    ),
    skip_when_false=MarkerCase(
        code="@oxitest.mark.skip(when=False, reason='never')\ndef test_foo(): pass\n",
        expected_type=PassedResult,
    ),
    xfail_failing=MarkerCase(
        code="@oxitest.mark.xfail(reason='known bug')\ndef test_foo(): assert False\n",
        expected_type=XFailedResult,
    ),
    xfail_passing_default=MarkerCase(
        code="@oxitest.mark.xfail\ndef test_foo(): pass\n",
        expected_type=XPassedResult,
    ),
    xfail_passing_strict_false=MarkerCase(
        code="@oxitest.mark.xfail(strict=False)\ndef test_foo(): pass\n",
        expected_type=XPassedResult,
    ),
    xfail_skipped_inside=MarkerCase(
        code=(
            "import unittest\n"
            "@oxitest.mark.xfail\n"
            "def test_foo(): raise unittest.SkipTest('skipped inside xfail')\n"
        ),
        expected_type=SkippedResult,
    ),
    xfail_raises_matching=MarkerCase(
        code=(
            "@oxitest.mark.xfail(raises=ValueError, reason='known')\n"
            "def test_foo(): raise ValueError('boom')\n"
        ),
        expected_type=XFailedResult,
    ),
    xfail_raises_not_matching=MarkerCase(
        code=(
            "@oxitest.mark.xfail(raises=TypeError, reason='known')\n"
            "def test_foo(): raise ValueError('boom')\n"
        ),
        expected_type=ErrorResult,
    ),
    xfail_raises_assertion_not_matching=MarkerCase(
        code=(
            "@oxitest.mark.xfail(raises=ValueError, reason='known')\n"
            "def test_foo(): assert False\n"
        ),
        expected_type=FailedResult,
    ),
    xfail_raises_assertion_matching=MarkerCase(
        code=(
            "@oxitest.mark.xfail(raises=AssertionError, reason='known')\n"
            "def test_foo(): assert False\n"
        ),
        expected_type=XFailedResult,
    ),
    xfail_raises_passes=MarkerCase(
        code=(
            "@oxitest.mark.xfail(raises=ValueError, reason='known')\n"
            "def test_foo(): pass\n"
        ),
        expected_type=XPassedResult,
    ),
)
def test_mark_executor_result(
    tmp: TempDir,
    code: str,
    expected_type: type[_MarkerResult],
    message_contains: str,
) -> None:
    """Each mark variant produces the expected executor status when run."""
    result = helpers.exec_inline(tmp, "import oxitest\n" + code, "test_foo")
    result = helpers.assert_result(
        result,
        expected_type,
        why="executor must honour the mark's semantics end-to-end, not just at the"
        " handler level",
    )
    if message_contains:
        assert message_contains in getattr(result, "message", ""), (
            "the mark's reason must propagate through the executor into the final"
            " result message"
        )


def test_mark_pass_through_is_inert() -> None:
    """PassThrough() is the inert mark action — no short-circuit and no wrapper."""
    r = PassThrough()
    assert not isinstance(r, ShortCircuit), (
        "PassThrough must not be a ShortCircuit — a ShortCircuit would skip the test"
    )
    assert not isinstance(r, Wrap), (
        "PassThrough must not be a Wrap — a Wrap would alter test outcomes"
    )


def test_skip_handler_returns_short_circuit() -> None:
    """_SkipHandler.handle produces a skipped short-circuit result and no wrapper."""
    action = _SkipHandler().handle(
        MarkInfo("skip", (), MappingProxyType({"reason": "not ready"}))
    )
    assert isinstance(action, ShortCircuit), (
        "_SkipHandler should produce a ShortCircuit action"
    )
    helpers.assert_result(
        action.result,
        SkippedResult,
        why="the reason given to @mark.skip must survive the handler into the result"
        " -- it is the only explanation the report can show for a test that never ran",
        message="not ready",
    )
    assert not isinstance(action, Wrap), "_SkipHandler should not produce a wrapper"


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
    action = _XFailHandler().handle(
        MarkInfo("xfail", (), MappingProxyType({"reason": "known bug"}))
    )
    assert not isinstance(action, ShortCircuit), (
        "_XFailHandler should not short-circuit"
    )
    assert isinstance(action, Wrap), "_XFailHandler should produce a Wrap action"


def test_xfail_wrapper_converts_failed_to_xfailed() -> None:
    """The xfail wrapper turns a failed inner result into an xfailed outcome."""
    action = _XFailHandler().handle(
        MarkInfo("xfail", (), MappingProxyType({"reason": "known bug"}))
    )
    assert isinstance(action, Wrap), "_XFailHandler should produce a Wrap action"
    wrapper = action.wrapper
    failed_result = FailedResult(message="oops")
    assert wrapper(lambda: failed_result).status == "xfailed", (
        "xfail wrapper should convert 'failed' result to 'xfailed'"
    )


def test_xfail_wrapper_converts_passed_to_xpassed() -> None:
    """The xfail wrapper turns an unexpectedly passing inner result into xpassed."""
    action = _XFailHandler().handle(
        MarkInfo("xfail", (), MappingProxyType({"reason": "known"}))
    )
    assert isinstance(action, Wrap), "_XFailHandler should produce a Wrap action"
    wrapper = action.wrapper
    passed_result = PassedResult()
    assert wrapper(lambda: passed_result).status == "xpassed", (
        "xfail wrapper should convert unexpectedly 'passed' result to 'xpassed'"
    )


def test_xfail_wrapper_passes_through_skipped() -> None:
    """The xfail wrapper leaves a skipped inner result unchanged."""
    action = _XFailHandler().handle(MarkInfo("xfail", (), MappingProxyType({})))
    assert isinstance(action, Wrap), "_XFailHandler should produce a Wrap action"
    wrapper = action.wrapper
    skipped_result = SkippedResult(message="not my test")
    final = wrapper(lambda: skipped_result)
    assert final.status == "skipped", (
        "xfail must not intercept skips — SkipTest inside xfail is still a skip, not an"
        " xfail"
    )


def test_timeout_handler_returns_wrapper() -> None:
    """_TimeoutHandler.handle() returns a Wrap action with no short-circuit outcome."""
    action = _TimeoutHandler().handle(
        MarkInfo("timeout", (), MappingProxyType({"seconds": 3}))
    )
    assert isinstance(action, Wrap), (
        "TimeoutHandler.handle() should return a Wrap action, got non-Wrap"
    )
    assert not isinstance(action, ShortCircuit), (
        "TimeoutHandler.handle() should not short-circuit"
    )


def test_timeout_handler_wrapper_passes_fast_test() -> None:
    """The timeout wrapper passes a PassedResult when the test finishes in time."""
    action = _TimeoutHandler().handle(
        MarkInfo("timeout", (), MappingProxyType({"seconds": 5}))
    )
    assert isinstance(action, Wrap), (
        "TimeoutHandler.handle() should produce a Wrap action"
    )
    wrapper = action.wrapper
    fast_result = PassedResult()
    assert wrapper(lambda: fast_result).status == "passed", (
        "timeout wrapper should pass through 'passed' result when test finishes quickly"
    )


def test_timeout_handler_wrapper_returns_timeout_on_expiry() -> None:
    """The timeout wrapper returns TimeoutResult when the test exceeds the deadline."""
    action = _TimeoutHandler().handle(
        MarkInfo("timeout", (), MappingProxyType({"seconds": 1}))
    )
    assert isinstance(action, Wrap), (
        "TimeoutHandler.handle() should produce a Wrap action"
    )
    wrapper = action.wrapper

    def slow_next() -> PassedResult:
        time.sleep(5)
        return PassedResult()

    outcome = wrapper(slow_next)
    timed_out = helpers.assert_result(
        outcome,
        TimeoutResult,
        why="an overrun must be reported as a timeout in its own right, not folded"
        " into a generic failure -- only TimeoutResult carries the limit exceeded",
    )
    assert "1s" in timed_out.message, (
        "timeout result must include the limit so the developer knows which deadline"
        " was exceeded"
    )


@dataclasses.dataclass(frozen=True)
class InvalidTimeout:
    """Parameters for a single invalid timeout test case."""

    seconds: int


@oxi.parametrize(
    zero=InvalidTimeout(seconds=0),
    negative=InvalidTimeout(seconds=-1),
)
def test_timeout_mark_rejects_invalid_seconds(seconds: int) -> None:
    """@mark.timeout raises ValueError for zero or negative seconds."""
    with raises(ValueError, match="seconds > 0"):

        @oxitest.mark.timeout(seconds=seconds)
        def test_bad() -> None:
            pass


def test_timeout_mark_stores_seconds() -> None:
    """@mark.timeout stores the seconds value in the function's mark metadata."""

    @oxitest.mark.timeout(seconds=5)
    def test_ok() -> None:
        pass

    marks = get_metadata(test_ok).marks
    assert len(marks) == 1, (
        "@mark.timeout must attach exactly one mark — duplicates would nest timeout"
        " wrappers"
    )
    assert marks[0].name == "timeout", (
        "timeout decorator must register under 'timeout' so _TimeoutHandler receives it"
    )
    assert marks[0].kwargs["seconds"] == 5, (
        "seconds must survive in kwargs because _TimeoutHandler reads it to set the"
        " deadline"
    )


def test_evaluate_marks_returns_proceed_when_empty() -> None:
    """evaluate_marks with no marks returns MarksProceed with empty wrappers."""
    outcome = evaluate_marks([])
    assert isinstance(outcome, MarksProceed), (
        "no marks means no handler short-circuited, so evaluate_marks must return"
        " MarksProceed"
    )
    assert outcome.wrappers == (), (
        "no marks means no handler ran, so no wrappers should be produced"
    )


def test_evaluate_marks_skip_returns_short_circuit() -> None:
    """evaluate_marks with a skip mark short-circuits to a MarksHalt(skipped)."""
    outcome = evaluate_marks(
        [MarkInfo("skip", (), MappingProxyType({"reason": "x"}))],
    )
    assert isinstance(outcome, MarksHalt), (
        "evaluate_marks with skip mark should return MarksHalt to short-circuit"
    )
    assert outcome.result.status == "skipped", (
        "skip short-circuit must carry 'skipped' status so the reporter tallies it"
        " correctly"
    )


# ── _MARK_REGISTRY ────────────────────────────────────────────────────────────


def test_all_builtin_handlers_registered() -> None:
    """_MARK_REGISTRY contains exactly the three built-in mark handlers."""
    expected = {"skip", "xfail", "timeout"}
    assert set(_MARK_REGISTRY.keys()) == expected, (
        "adding or removing a builtin handler without updating this test means"
        " evaluate_marks routing is wrong"
    )


def test_registered_handlers_are_mark_handler_instances() -> None:
    """Every entry in _MARK_REGISTRY is a MarkHandler subclass instance."""
    for handler in _MARK_REGISTRY.values():
        assert isinstance(handler, MarkHandler), (
            "every registry entry must be a MarkHandler so evaluate_marks can call"
            " .handle() on it"
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
            "handler.mark_name must match its registry key or evaluate_marks will"
            " dispatch to the wrong handler"
        )


def test_exc_type_populated_on_assertion_error(tmp: TempDir) -> None:
    """A failing assertion populates exc_type with 'AssertionError'."""
    result = helpers.exec_inline(
        tmp, "import oxitest\ndef test_foo(): assert False\n", "test_foo"
    )
    result = helpers.assert_result(
        result,
        FailedResult,
        why="a failing assertion is a test failure, not an infrastructure error, and"
        " only the failed variant carries exc_type",
    )
    assert result.exc_type == "AssertionError", (
        "executor must capture the exception class name so reporters can distinguish"
        " assertion failures from other errors"
    )


def test_exc_type_populated_on_runtime_error(tmp: TempDir) -> None:
    """An uncaught runtime exception sets exc_type to the exception class name."""
    result = helpers.exec_inline(
        tmp, "import oxitest\ndef test_foo(): raise ValueError('boom')\n", "test_foo"
    )
    result = helpers.assert_result(
        result,
        ErrorResult,
        why="an uncaught non-assertion exception is an 'error', distinct from the"
        " 'failed' an assertion produces, and only that variant carries exc_type",
    )
    assert result.exc_type == "ValueError", (
        "executor must capture the actual exception class name so reporters can show"
        " what was raised"
    )


def test_exc_type_absent_on_pass(tmp: TempDir) -> None:
    """A passing test produces a PassedResult, which has no exc_type attribute."""
    result = helpers.exec_inline(
        tmp, "import oxitest\ndef test_foo(): pass\n", "test_foo"
    )
    assert not hasattr(result, "exc_type"), (
        "PassedResult must omit exc_type — its presence would mislead reporters into"
        " treating a pass as an error"
    )


class _FakePluginWrapper:
    """Minimal stand-in for a plugin that wraps test results."""

    marker = "custom_mark"

    def wrap(
        self, *, test_fn: Callable[[], TestResult], marker_args: dict[int | str, Any]
    ) -> TestResult:
        result = test_fn()
        return dataclasses.replace(result, message=f"wrapped:{marker_args}")


def test_plugin_mark_handler_wraps_correctly() -> None:
    """_PluginMarkHandler delegates wrapping to the plugin and returns a Wrap action."""
    pw = _FakePluginWrapper()
    handler = _PluginMarkHandler(pw)
    assert handler.mark_name == "custom_mark", (
        "_PluginMarkHandler must derive mark_name from the plugin so evaluate_marks"
        " routes correctly"
    )
    mark = MarkInfo("custom_mark", ("arg1",), MappingProxyType({"key": "val"}))
    action = handler.handle(mark)
    assert isinstance(action, Wrap), "handler should produce a Wrap action"
    assert not isinstance(action, ShortCircuit), "handler should not short-circuit"

    # Execute the wrapper — use WarnedResult since it has a message field
    inner_result = WarnedResult(message="original")
    wrapped_result = action.wrapper(lambda: inner_result)
    wrapped = helpers.assert_result(
        wrapped_result,
        WarnedResult,
        why="a plugin wrapper may edit the result it is handed but never reclassify"
        " it -- swapping the variant rewrites the outcome of every test with its mark",
    )
    assert "wrapped:" in wrapped.message, (
        "plugin wrapper must transform the result so its side effects are visible to"
        " the reporter"
    )


def test_marker_composition_skip_takes_precedence_over_others() -> None:
    """When skip + xfail + timeout are all present, skip takes precedence."""
    marks = [
        MarkInfo("skip", (), MappingProxyType({"reason": "not ready"})),
        MarkInfo("xfail", (), MappingProxyType({"reason": "known bug"})),
        MarkInfo("timeout", (), MappingProxyType({"seconds": 5})),
    ]
    outcome = evaluate_marks(marks)

    assert isinstance(outcome, MarksHalt), (
        "evaluate_marks with skip mark should return MarksHalt to short-circuit"
    )
    helpers.assert_result(
        outcome.result,
        SkippedResult,
        why="when skip wins the composition it must win whole -- the surviving result"
        " has to be the skip's own, reason and all, not one built from the mark that"
        " lost",
        message="not ready",
    )


def test_evaluate_marks_dispatches_plugin_handlers() -> None:
    """evaluate_marks routes plugin marks through the supplied plugin_handlers list."""
    pw = _FakePluginWrapper()
    handler = _PluginMarkHandler(pw)
    marks = [MarkInfo("custom_mark", (), MappingProxyType({}))]
    outcome = evaluate_marks(
        marks,
        plugin_handlers=[handler],
    )
    assert isinstance(outcome, MarksProceed), (
        "plugin wrap must not short-circuit, so evaluate_marks must return MarksProceed"
    )
    assert len(outcome.wrappers) == 1, (
        "plugin handler must contribute its wrapper or the plugin's test transformation"
        " is silently lost"
    )


def test_stacked_timeout_marks_are_refused_at_decoration() -> None:
    """Two deadlines on one test is always an authoring mistake.

    Before #2001 the innermost silently won, so @timeout(2) stacked over
    @timeout(20) ran under 20s and the author never learned which limit was
    lost. The effective deadline is now the shorter of the live deadlines, so
    stacking is merely harmless -- but harmless is not the bar for something
    statically visible at import.
    """
    with oxitest.raises(ValueError):

        @oxitest.mark.timeout(seconds=2)
        @oxitest.mark.timeout(seconds=20)
        def test_two_deadlines() -> None:
            pass


# ── skip() and bare mark access ─────────────────────────────────────────────
#
# Moved from test_fixtures_dsl.py, which #1720 deletes. These four never had
# anything to do with the Fixtures() registry that file was named for.


def test_skip_raises_skip_test() -> None:
    """oxitest.skip() raises SkipTest with the provided reason message."""
    with raises(unittest.SkipTest) as exc_info:
        oxitest.skip("not ready")
    assert str(exc_info.value) == "not ready", (
        f"oxitest.skip('not ready') should produce message 'not ready', got "
        f"{str(exc_info.value)!r}"
    )


def test_skip_no_reason() -> None:
    """oxitest.skip() with no argument still raises SkipTest."""
    with raises(unittest.SkipTest):
        oxitest.skip()


def test_mark_attribute_callable_without_error() -> None:
    """Mark attributes are callable with arbitrary args and don't raise on access."""
    oxitest.mark.skip(reason="reason")
    _ = oxitest.mark.xfail
    oxitest.mark.anything("value")


def test_mark_used_as_decorator_returns_function() -> None:
    """Mark used as a bare decorator passes through the decorated function unchanged."""

    @oxitest.mark.skip
    def fn() -> None:
        pass

    assert callable(fn), (
        "@oxitest.mark.skip used as decorator should return the original callable, got "
        "non-callable"
    )
