"""Fixture resolution and instantiation — extracted from FixtureSession."""

from __future__ import annotations

__all__ = [
    "AsyncPolicy",
    "ScopeRefs",
    "_FixtureOutcome",
    "_check_async_dep",
    "_reject_async_in_sync",
    "_reject_nonshared_async",
    "_resolve_deps",
    "_unpack_sync",
]

import inspect
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from oxitest._bridge._errors import FixtureSetupError
from oxitest._bridge._metadata import get_type_hints_cached as _get_hints
from oxitest._bridge._test_meta import TestMeta

if TYPE_CHECKING:
    from oxitest._bridge._fixture_session import FixtureSession


@dataclass(frozen=True, slots=True)
class ScopeRefs:
    """References to the scope a fixture should be cached/torn down in."""

    cache: dict[str, Any]
    teardowns: list[Callable[[], None]]
    hits: dict[str, int]
    misses: dict[str, int]


def _warn_teardown(name: str, exc: Exception, *, node_id: str = "") -> None:
    from oxitest._bridge._fixture_session import (
        FixtureTeardownWarning,
        _current_teardown_node_id,
    )

    effective_id = node_id or _current_teardown_node_id.get()
    if name and effective_id:
        msg = f"fixture '{name}' teardown failed during {effective_id}: {exc}"
    elif name:
        msg = f"error in teardown of fixture '{name}': {exc}"
    else:
        msg = f"error during teardown: {exc}"
    warnings.warn(FixtureTeardownWarning(msg), stacklevel=2)


def _check_async_dep(dep_name: str, dep_val: Any, fixture_name: str, msg: str) -> None:
    """Reject an async dependency value with a descriptive error message."""
    if inspect.iscoroutine(dep_val) or inspect.isasyncgen(dep_val):
        if inspect.iscoroutine(dep_val):
            dep_val.close()
        raise FixtureSetupError(fixture_name, RuntimeError(msg))


def _reject_async_in_sync(dep_name: str, dep_val: Any, fixture_name: str) -> None:
    """Sync fixtures cannot depend on async fixtures."""
    _check_async_dep(
        dep_name,
        dep_val,
        fixture_name,
        f"sync fixture '{fixture_name}' cannot depend on async fixture '{dep_name}'",
    )


def _reject_nonshared_async(dep_name: str, dep_val: Any, fixture_name: str) -> None:
    """Shared fixtures cannot depend on non-shared async fixtures."""
    _check_async_dep(
        dep_name,
        dep_val,
        fixture_name,
        f"shared fixture '{fixture_name}' cannot depend on "
        f"non-shared async fixture '{dep_name}' \u2014 "
        f"lifetime mismatch",
    )


AsyncPolicy = Callable[[str, Any, str], None]


def _resolve_deps(
    session: FixtureSession,
    fn: Callable[..., Any],
    module_path: str,
    fn_teardowns: list[Callable[[], None]],
    fn_name: str,
    resolve_user: Callable[[str], Any],
    async_policy: AsyncPolicy | None = None,
) -> dict[str, Any]:
    """Resolve fixture dependencies from type hints.

    async_policy: if provided, called as policy(dep_name, dep_val, fn_name)
    for each resolved dependency. Raises on invalid async dependency patterns.
    """
    # Build a minimal TestMeta for fixture-to-fixture resolution (builtins
    # only need module_path and fn_name; node_id/markers are test-level).
    dep_meta = TestMeta(module_path=module_path, fn_name=fn_name, node_id="")
    hints = _get_hints(fn)
    deps: dict[str, Any] = {}
    for param_name, hint in hints.items():
        if param_name == "return":
            continue
        resolved, value = session._resolve_param(
            param_name,
            hint,
            dep_meta,
            fn_teardowns=fn_teardowns,
            resolve_user_fixture=resolve_user,
        )
        if resolved:
            deps[param_name] = value
    if async_policy is not None:
        for dep_name, dep_val in deps.items():
            async_policy(dep_name, dep_val, fn_name)
    return deps


@dataclass
class _FixtureOutcome:
    """Result of unpacking a fixture function call."""

    value: Any
    teardown: Callable[[], None] | None = None


def _unpack_sync(result: Any, name: str) -> _FixtureOutcome:
    """Unpack a sync fixture call: plain value or generator."""
    if inspect.isgenerator(result):
        value = next(result)

        def teardown(gen: Any = result, n: str = name) -> None:
            try:
                next(gen)
            except StopIteration:
                pass
            except Exception as exc:
                _warn_teardown(n, exc)

        return _FixtureOutcome(value, teardown)
    return _FixtureOutcome(result)
