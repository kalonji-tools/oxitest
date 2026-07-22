"""Public plugin-facing result factory tests.

Issue: https://github.com/kalonji-tools/oxitest/issues/1588
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass, fields, is_dataclass
from enum import StrEnum
from typing import Any, get_args

import oxitest
import oxitest.plugin as _plugin_module
from oxitest import parametrize
from oxitest.plugin import (
    ExecutionWrapper,
    SkippedResult,
    StatusKind,
    TestResult,
    WarnedResult,
    XFailedResult,
    skipped,
    warned,
    xfailed,
)


@dataclass(frozen=True)
class FactoryCase:
    """Parametrize case: a plugin factory paired with its expected variant + status."""

    name: str
    factory: Callable[..., TestResult]
    expected_cls: type[SkippedResult | XFailedResult | WarnedResult]
    expected_status: StatusKind
    sample_message: str


FACTORY_CASES = {
    "skipped": FactoryCase(
        name="skipped",
        factory=skipped,
        expected_cls=SkippedResult,
        expected_status=StatusKind.SKIPPED,
        sample_message="requires NixOS",
    ),
    "xfailed": FactoryCase(
        name="xfailed",
        factory=xfailed,
        expected_cls=XFailedResult,
        expected_status=StatusKind.XFAILED,
        sample_message="known-broken on macOS",
    ),
    "warned": FactoryCase(
        name="warned",
        factory=warned,
        expected_cls=WarnedResult,
        expected_status=StatusKind.WARNED,
        sample_message="deprecated API used",
    ),
}


def test_variant_classes_publicly_importable_from_plugin() -> None:
    """SkippedResult, XFailedResult, WarnedResult are public dataclasses."""
    for cls in (SkippedResult, XFailedResult, WarnedResult):
        assert is_dataclass(cls), (
            f"{cls.__name__} must be a dataclass — plugin authors rely on "
            "field access and isinstance discrimination for result inspection"
        )


def test_status_kind_is_strenum_covering_all_outcomes() -> None:
    """StatusKind is a StrEnum with exactly the 8 canonical outcome names."""
    assert issubclass(StatusKind, StrEnum), (
        "StatusKind must be a StrEnum — its string values are already "
        "public via the LDJSON wire protocol (PROTOCOL_VERSION=3)"
    )
    actual = {m.name for m in StatusKind}
    expected = {
        "PASSED",
        "FAILED",
        "ERROR",
        "SKIPPED",
        "XFAILED",
        "XPASSED",
        "TIMEOUT",
        "WARNED",
    }
    assert actual == expected, (
        "StatusKind must cover all 8 result outcomes — plugin authors "
        f"discriminate any result variant through it; diff: {actual ^ expected}"
    )


def test_test_result_union_covers_all_eight_variants() -> None:
    """TestResult union includes all 8 result variant classes."""
    args = get_args(TestResult)
    variant_names = {a.__name__ for a in args}
    expected = {
        "PassedResult",
        "FailedResult",
        "ErrorResult",
        "SkippedResult",
        "XFailedResult",
        "XPassedResult",
        "TimeoutResult",
        "WarnedResult",
    }
    assert variant_names == expected, (
        "TestResult union must include exactly the 8 canonical result variants"
        f" — pattern-match consumers rely on it; diff: {variant_names ^ expected}"
    )


def test_runner_produced_variants_not_importable_from_plugin() -> None:
    """Runner-produced result variants are not top-level on oxitest.plugin."""
    private_names = (
        "PassedResult",
        "FailedResult",
        "ErrorResult",
        "TimeoutResult",
        "XPassedResult",
    )
    for name in private_names:
        assert not hasattr(_plugin_module, name), (
            f"{name} is runner-produced — a wrapper synthesising it "
            "misrepresents the runner's role. It must stay private on oxitest.plugin."
        )


@parametrize(**FACTORY_CASES)
def test_factory_produces_frozen_result_with_correct_shape(case: FactoryCase) -> None:
    """Each factory returns a frozen result with the right class + status + message."""
    result = case.factory(message=case.sample_message)

    assert isinstance(result, case.expected_cls), (
        f"{case.name}() must produce a {case.expected_cls.__name__} — "
        "plugin authors and the reporter both narrow on concrete variant type"
    )
    assert result.status is case.expected_status, (
        f"{case.expected_cls.__name__}.status must identify as "
        f"{case.expected_status.name} — reporter aggregation and rule filters "
        "read the status enum, not the class name"
    )
    assert result.message == case.sample_message, (
        "The factory's message must survive verbatim to the result — the "
        "reporter surfaces it as the user-facing outcome text"
    )
    with oxitest.raises(FrozenInstanceError):
        setattr(result, "message", "mutated")  # noqa: B010 — intentional frozen write


@parametrize(**FACTORY_CASES)
def test_factory_rejects_positional_arguments(case: FactoryCase) -> None:
    """Each plugin factory enforces keyword-only invocation."""
    factory_any: Any = case.factory
    with oxitest.raises(TypeError, match="positional"):
        factory_any(case.sample_message)


def test_warned_result_field_set_locked_to_factory_awareness() -> None:
    """Guard: WarnedResult shape changes force a fresh warned() factory decision.

    The `warned()` factory exposes `message` and deliberately omits
    `no_message_lines` (documented in its docstring). Any new WarnedResult
    field lands here as a failure so the omit-or-expose call has to be made
    consciously — either surface the field on the factory, or update this
    locked set and the factory docstring together.
    """
    actual = frozenset(f.name for f in fields(WarnedResult))
    locked = frozenset({"message", "no_message_lines"})
    assert actual == locked, (
        "WarnedResult field set drifted — reconcile with warned() factory: "
        f"unexpected additions={actual - locked}, removals={locked - actual}"
    )


@dataclass
class _FactoryReturningWrapper:
    """Test double: returns a pre-configured TestResult from wrap().

    Satisfies the ExecutionWrapper Protocol structurally — carries a
    `marker` field (satisfies the `marker: str` property requirement via
    structural isinstance) and a `wrap` method matching the protocol
    signature exactly. Enumerating `test_fn` and `marker_args` (instead of
    absorbing them via `**_`) means a signature drift in the protocol
    surfaces as a test-file diff during review, which
    `@runtime_checkable`'s attribute-only check cannot catch on its own.
    """

    outcome: TestResult
    marker: str = "test-wrapper"

    def wrap(self, *, test_fn: Any, marker_args: dict[str, Any]) -> Any:
        del test_fn, marker_args
        return self.outcome


@parametrize(**FACTORY_CASES)
def test_wrapper_returning_factory_result_reaches_consumer_intact(
    case: FactoryCase,
) -> None:
    """A wrapper returning a factory result satisfies the ExecutionWrapper protocol."""
    wrapper = _FactoryReturningWrapper(
        outcome=case.factory(message=case.sample_message)
    )

    assert isinstance(wrapper, ExecutionWrapper), (
        "The wrapper double must satisfy the runtime-checkable ExecutionWrapper "
        "protocol — this documents the API contract plugin authors must meet, "
        "even though plugin activation uses duck-typed dispatch rather than isinstance"
    )
    result = wrapper.wrap(test_fn=lambda: None, marker_args={})
    assert isinstance(result, case.expected_cls), (
        "The factory value must survive the wrap() return path unchanged — "
        "no coercion, no wrapping"
    )
    assert result.status is case.expected_status, (
        "The status carried on the factory-produced value must reach downstream "
        "consumers intact — reporter aggregation depends on it"
    )
