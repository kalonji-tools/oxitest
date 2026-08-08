"""Trust-boundary helpers.

Provides ``safe_call`` for catching arbitrary exceptions at boundaries
where user/plugin code is executed, plus focused wrappers for common
patterns (type-hint resolution, fixture teardown).

Optional-callback parameters follow ADR-0007 Rule 5: the default is a
module-level no-op function of the same signature. Consumers call the
callback unconditionally.
"""

from __future__ import annotations

__all__ = [
    "advance_async_gen",
    "async_safe_call",
    "safe_call",
    "safe_teardown",
    "safe_type_hints",
    "setup_completed",
]

import inspect
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar, get_type_hints as _stdlib_hints
from weakref import WeakSet

from oxitest._oxitest import trace as _trace

_T = TypeVar("_T")


def _no_on_error(_exc: Exception) -> None:
    """No-op default for ``safe_call``/``async_safe_call`` ``on_error``."""


def _no_warn(_name: str, _exc: Exception) -> None:
    """No-op default for ``safe_teardown`` ``warn``."""


def safe_call(
    fn: Callable[[], _T],
    *,
    default: _T,
    on_error: Callable[[Exception], None] = _no_on_error,
) -> _T:
    """Call *fn* and return *default* if it raises any ``Exception``.

    Use at trust boundaries where user/plugin code can raise anything.
    The *on_error* callback receives the caught exception for logging or
    warning before *default* is returned. Defaults to a no-op that
    silently swallows the exception.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — boundary helper by design
        on_error(exc)
        return default


async def async_safe_call(
    coro: Coroutine[Any, Any, _T],
    *,
    default: _T,
    on_error: Callable[[Exception], None] = _no_on_error,
) -> _T:
    """Await *coro* and return *default* if it raises any ``Exception``.

    Async counterpart of ``safe_call`` for trust boundaries that
    execute user coroutines or async generators.
    """
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 — async boundary helper by design
        on_error(exc)
        return default


def safe_type_hints(obj: Any, **kwargs: Any) -> dict[str, Any] | None:
    """Call ``get_type_hints(obj, **kwargs)`` and return ``None`` on failure.

    User code may have unresolvable forward references, missing imports,
    or broken annotations — all of which make ``get_type_hints`` raise.
    Callers should handle ``None`` with their own fallback.
    """
    return safe_call(
        lambda: _stdlib_hints(obj, **kwargs),
        default=None,
        on_error=lambda exc: _trace(
            "debug", __name__, f"Could not resolve type hints for {obj!r}: {exc}"
        ),
    )


def setup_completed(gen: Any) -> bool:
    """Whether *gen*'s body ran far enough that it needs tearing down.

    Every generator fixture is registered for teardown **before** it is
    advanced, so the teardown lists hold generators whose setup may never have
    completed — the registration has to precede the advance, or an interrupt
    landing between the two strands a set-up fixture with nothing to dispose
    it (#1962). This is the predicate that sorts the two apart at drain time.

    ``True`` only once the body has reached its ``yield``. A generator that was
    never started has nothing to resume — and resuming it would *run the setup*
    during teardown — while one that raised before yielding has already
    unwound. Both report a state other than suspended:

    ==========================  ===============  =================
    Generator                   Never started    Raised pre-yield
    ==========================  ===============  =================
    sync (``GEN_*``)            ``CREATED``      ``CLOSED``
    async (``AGEN_*``)          ``CREATED``      ``CLOSED``
    ==========================  ===============  =================

    This is the same contract ``contextlib.contextmanager`` offers: ``__enter__``
    calls ``next(gen)``, and ``__exit__`` never runs if that raises. A fixture
    needing more than that — cleanup for a resource acquired *before* its
    ``yield`` — uses its own ``try``/``finally`` inside the body, which no
    framework can substitute for.
    """
    if inspect.isasyncgen(gen):
        return _async_setup_completed(gen)
    # Sync generators need no bookkeeping: `getgeneratorstate` has existed since
    # 3.2 and answers this exactly, on every version this project supports.
    return inspect.getgeneratorstate(gen) == inspect.GEN_SUSPENDED


#: ``inspect.getasyncgenstate`` is 3.12+; absent on 3.11. Resolved once, by
#: probing rather than by comparing version tuples — the capability is the fact,
#: a version number is a proxy for it that can drift.
_GETASYNCGENSTATE = getattr(inspect, "getasyncgenstate", None)
_AGEN_SUSPENDED = getattr(inspect, "AGEN_SUSPENDED", "AGEN_SUSPENDED")


def _async_setup_completed(agen: Any) -> bool:
    """Whether *agen* reached its first ``yield``, by the best means available.

    **Two arms, because neither alone covers the supported range**, and both
    were established by measurement rather than argument (#1962):

    *3.12+ asks the interpreter.* ``getasyncgenstate`` is exact and has no
    window — it reports what the generator *is*, however it got there.

    *3.11 reads the record kept by* :func:`advance_async_gen`, because 3.11 has
    no equivalent question to ask. ``getasyncgenstate`` and ``ag_suspended`` are
    both 3.12+, and the pre-3.12 ``ag_frame.f_lasti == -1`` idiom is wrong on
    3.11 *and* 3.12 — a *created* generator already reports ``f_lasti >= 0`` on
    both, measured at 3.12.13 locally and by a failing 3.11 CI job.

    **The bookkeeping arm is second, not first, and that ordering is the whole
    point.** A build that used the record on every version regressed
    ``test_async_yield_fixture_teardown_runs_on_timeout`` on macOS x86_64 while
    passing on three other platforms and locally 20/20 — the record has a
    one-bytecode window between the advance returning and the mark being taken,
    and an interrupt landing there loses the teardown. Introspection has no such
    window, so it is used wherever it exists. The record is confined to the one
    version that cannot be asked.
    """
    if _GETASYNCGENSTATE is not None:
        return _GETASYNCGENSTATE(agen) == _AGEN_SUSPENDED
    return agen in _ADVANCED_ASYNC_GENS


#: Async generators observed to have reached their first ``yield``.
#:
#: Written on **every** version and read only on 3.11 — see
#: :func:`_async_setup_completed` for why the read is confined there. Writing it
#: unconditionally keeps the one code path that maintains it exercised by the
#: whole suite on every version, so the 3.11 arm cannot rot unnoticed between
#: the rare occasions anything reads it.
#:
#: Weak, so a generator's membership dies with the generator and this cannot
#: retain fixtures. Per process: workers do not share it, and the async paths
#: it serves each run on a single loop.
_ADVANCED_ASYNC_GENS: WeakSet[Any] = WeakSet()


async def advance_async_gen(agen: Any) -> Any:
    """Run an async fixture body to its first ``yield`` and record that it did.

    The single place an async fixture generator is first advanced, so that
    "setup completed" has exactly one writer. Callers that need to drive this
    from sync code run it on their session: ``session.run(advance_async_gen(g))``.

    **The mark is taken after the advance, deliberately.** An interrupt in the
    gap between them leaves the generator suspended but unmarked, so its
    teardown is skipped. Marking *before* would invert the failure into resuming
    a generator that never started, which runs the fixture's setup during
    teardown — worse than a missed teardown, not better (#1962).

    That one-bytecode window is why the record is only *read* on 3.11, where
    nothing better exists; every other version asks the interpreter instead and
    has no window at all. See :func:`_async_setup_completed`.
    """
    value = await agen.__anext__()
    _ADVANCED_ASYNC_GENS.add(agen)
    return value


def safe_teardown(
    fn: Callable[[], None],
    name: str = "",
    *,
    warn: Callable[[str, Exception], None] = _no_warn,
) -> None:
    """Call a teardown function, warning on failure instead of propagating.

    The *warn* callback receives ``(name, exc)`` on failure. Defaults to
    a no-op — callers that want teardown failures surfaced must pass an
    explicit ``warn`` callback (e.g. ``_warn_teardown`` from
    ``_fixture_context``).
    """
    safe_call(
        fn,
        default=None,
        on_error=lambda exc: warn(name, exc),
    )
