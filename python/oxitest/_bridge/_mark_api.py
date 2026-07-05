from __future__ import annotations

__all__ = ["MarkInfo", "_append_mark", "mark", "skip"]

import unittest
from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, TypeVar

from oxitest._bridge._fn_metadata import get_or_create

_F = TypeVar("_F", bound=Callable[..., Any])


def skip(reason: str = "") -> None:
    """Unconditionally skip the current test.

    Equivalent to `@mark.skip` but callable from inside the test body to
    skip at runtime after setup has already begun.

    Args:
        reason: Human-readable explanation shown in the test report.

    Raises:
        unittest.SkipTest: Always. Caught by the test runner, not the test.

    Example:
        ```python
        def test_requires_network() -> None:
            if not network_available():
                oxitest.skip("no network")
            ...
        ```
    """
    raise unittest.SkipTest(reason)


# ── Mark system ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MarkInfo:
    """Metadata attached to a test function by a mark decorator.

    Collected at import time and read by the runner before each test.

    Attributes:
        name: The mark name (e.g. `"skip"`, `"xfail"`, `"timeout"`).
        args: Positional arguments passed to the mark decorator call.
        kwargs: Keyword arguments passed to the mark decorator call.
    """

    name: str
    args: tuple[object, ...]
    kwargs: MappingProxyType[str, object]


def _append_mark(f: object, info: MarkInfo) -> None:
    get_or_create(f).marks.append(info)


class _Mark:
    """A mark factory for a single named mark.

    Calling an instance decorates the target function and records a
    `MarkInfo`. Supports both bare application (`@mark.skip`) and
    call-with-args (`@mark.skip(reason="...")`).
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self, *args: Any, **kwargs: Any) -> _F | Callable[[_F], _F]:
        if len(args) == 1 and callable(args[0]) and not kwargs:
            _append_mark(args[0], MarkInfo(self.name, (), MappingProxyType({})))
            return args[0]  # type: ignore[return-value]
        info = MarkInfo(self.name, args, MappingProxyType(dict(kwargs)))

        def decorator(f: _F) -> _F:
            _append_mark(f, info)
            return f

        return decorator


class _TimeoutMark:
    """Decorator factory for @oxitest.mark.timeout(seconds=N).

    Validates that seconds > 0 at decoration time (import), not at run time.
    """

    def __call__(self, seconds: int | None = None, **kwargs: Any) -> Callable[[_F], _F]:
        if seconds is None:
            seconds = kwargs.get("seconds")
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
            msg = (
                "@oxitest.mark.timeout requires an integer"
                f" seconds > 0, got {seconds!r}"
            )
            raise ValueError(msg)
        info = MarkInfo("timeout", (), MappingProxyType({"seconds": seconds}))

        def decorator(f: _F) -> _F:
            _append_mark(f, info)
            return f

        return decorator


class _SkipMark:
    """Decorator factory for @oxitest.mark.skip(when=True, reason="").

    Validates keyword-only ``when`` and ``reason`` at decoration time.
    When ``when`` is falsy, no mark is attached — the test runs normally.
    """

    def __call__(self, *args: Any, **kwargs: Any) -> _F | Callable[[_F], _F]:
        # Bare: @mark.skip (no parens — function is the single arg)
        if len(args) == 1 and callable(args[0]) and not kwargs:
            _append_mark(
                args[0], MarkInfo("skip", (), MappingProxyType({"reason": ""}))
            )
            return args[0]  # type: ignore[return-value]

        if args:
            msg = (
                "@oxitest.mark.skip() does not accept positional arguments. "
                "Use keyword arguments: @mark.skip(when=..., reason='...')"
            )
            raise TypeError(msg)

        when = kwargs.pop("when", True)
        reason = kwargs.pop("reason", "")
        if kwargs:
            msg = (
                f"@oxitest.mark.skip() got unexpected keyword arguments: "
                f"{', '.join(sorted(kwargs))}"
            )
            raise TypeError(msg)

        if not when:

            def identity(f: _F) -> _F:
                return f

            setattr(identity, "_oxitest_noop_mark", True)  # noqa: B010 — dynamic marker not in type stubs
            return identity

        info = MarkInfo("skip", (), MappingProxyType({"reason": str(reason)}))

        def decorator(f: _F) -> _F:
            _append_mark(f, info)
            return f

        return decorator


_SPECIAL_MARKS: dict[str, type] = {
    "skip": _SkipMark,
    "timeout": _TimeoutMark,
}


class _MarkNamespace:
    """Decorator namespace for built-in and custom test marks.

    Access via `oxitest.mark`. Each attribute returns a decorator factory.

    Built-in marks:

    - `mark.skip(when=True, reason="...")` — skip when *when* is truthy.
    - `mark.xfail(reason="...", strict=True, raises=None)` — expect failure.
    - `mark.timeout(seconds)` — fail if the test exceeds *seconds*.
    - `mark.usefixtures(*names)` — inject fixtures by name without a parameter.
    - `mark.parametrize(**cases)` — alias for `@oxitest.parametrize`.

    Custom marks declared in `[tool.oxitest] markers` are also accessible here
    and carry no built-in behaviour beyond identification.

    Example:
        ```python
        @oxitest.mark.skip(when=sys.platform == "win32", reason="POSIX only")
        def test_symlinks() -> None:
            ...

        @oxitest.mark.xfail(reason="known regression", strict=True)
        def test_broken() -> None:
            ...
        ```
    """

    def __getattr__(self, name: str) -> Any:
        factory = _SPECIAL_MARKS.get(name)
        if factory is not None:
            return factory()
        return _Mark(name)


mark = _MarkNamespace()
