"""The awaitable returned by ``fx.<ns>.<name>`` for an async fixture.

``fx.pkg.conn`` is a plain attribute access — there is no hook on that path
where the framework could await anything, which is why an ``async def``
fixture reached through the proxy used to arrive as a raw coroutine
(kalonji-tools/oxitest#1733).

The fix is to make the access return something the caller awaits::

    conn = await fx.pkg.conn

So the syntax says what is happening. The handle exists because a bare
coroutine cannot be cached: ``_CachingProxy._get_cached`` memoises whatever
the attribute resolved to, and awaiting the same coroutine twice raises
``RuntimeError: cannot reuse already awaited coroutine``. The handle memoises
its own *awaited result* instead, so a second ``await`` in the same test is
free and returns the identical value.
"""

from __future__ import annotations

__all__ = [
    "AsyncFixtureHandle",
    "async_teardown_sink",
    "register_async_teardown",
]

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Generator


#: Per-test sink for async generators created lazily inside the test body.
#:
#: Function-lifetime fixtures must be drained while the body's event loop is
#: still open. ``_async_test_core`` owns that window — it already drains the
#: parameter-injected generators in a ``finally`` — so the sink points at that
#: same list and the existing drain covers both paths. Outside a test body the
#: value is ``None`` and registration is refused rather than silently dropped.
async_teardown_sink: ContextVar[list[tuple[str, Any]] | None] = ContextVar(
    "async_teardown_sink", default=None
)


def register_async_teardown(name: str, agen: Any) -> bool:
    """Queue *agen*'s post-``yield`` half for the current test's drain.

    Returns ``False`` when there is no active sink, so the caller can fall back
    to a wider boundary instead of dropping the teardown on the floor.
    """
    sink = async_teardown_sink.get()
    if sink is None:
        return False
    sink.append((name, agen))
    return True


class AsyncFixtureHandle:
    """Awaitable, memoising handle for one async fixture within one test.

    Constructed per access by the namespace proxy; the proxy's own cache keeps
    one handle per fixture per test, and this class keeps one *value* per
    handle. The two layers are deliberately separate — the proxy caches the
    attribute, the handle caches the resolution.
    """

    __slots__ = ("_build", "_name", "_resolved", "_value")

    def __init__(
        self,
        build: Callable[[], Coroutine[Any, Any, Any]],
        name: str,
    ) -> None:
        self._build = build
        self._name = name
        self._value: Any = None
        self._resolved = False

    def __await__(self) -> Generator[Any, None, Any]:
        return self._get().__await__()

    async def _get(self) -> Any:
        if not self._resolved:
            self._value = await self._build()
            self._resolved = True
        return self._value

    def __repr__(self) -> str:
        state = "resolved" if self._resolved else "pending"
        return f"<async fixture {self._name!r} ({state}) — await it to get its value>"

    def __getattr__(self, attr: str) -> Any:
        """Turn a forgotten ``await`` into a diagnostic instead of a puzzle.

        Without this, ``fx.pkg.conn.execute(...)`` raises a bare
        ``AttributeError: 'AsyncFixtureHandle' object has no attribute
        'execute'``, which says nothing about the actual mistake.
        """
        if attr.startswith("_"):
            raise AttributeError(attr)
        msg = (
            f"{self._name!r} is an async fixture — await it before use: "
            f"`value = await fx....{self._name}`, then `value.{attr}`"
        )
        raise AttributeError(msg)
