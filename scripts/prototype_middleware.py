#!/usr/bin/env python3
# ruff: noqa: D101, D102, D103, ARG001, ARG002, C901, PLR0911, PLR0912, E501, EXE001, SLF001
"""Wayfinder T4 prototype: Composable Middleware with Constructor Injection.

Wayfinder map #1551 (None-by-exception ADR + Composable Middleware validation).
Ticket #1555. Precedent: `scripts/prototype_queries.py` (ADR-0003 inspect rework).

## Question this prototype answers

Does the "Composable Middleware with Constructor Injection" pattern (per #1549's
closing comment) actually eliminate the Optionals that today live on
`ExecutionPlan` (`backend`, `shared_session`, `arrange_session`) without
introducing new pain (constructor combinatorics, discoverability, testing
burden, unruly builders)?

## v-history

- v1 — compared Design A (inline Optionals) vs Design B (SessionStrategy sum
  type). User picked B.
- v2 — added named registration slots (pre_guard, post_guard, pre_session)
  and an "always/conditional/plugin/session" zone visualisation.
- v3 (this) — folds in three follow-up questions:
    * Q: does `TimeoutMiddleware` belong in the "always" segment?
      Toggle `[T]` cycles three timeout designs (Optional / 0-sentinel /
      sum type) so you can watch what moves between zones.
    * Q: what are all the possible slot variants?
      Slot enumeration is now shown explicitly, with a "Why this slot?"
      justification on every plugin so ergonomics can be judged.
    * Q: enum vs string for slot names?
      A StrEnum sits alongside string usage — same call sites, so the ceremony
      cost is visible directly.

## Run

    python scripts/prototype_middleware.py
"""

from __future__ import annotations

import enum
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

# ---------------------------------------------------------------------------
# PURE LOGIC MODULE (portable — the bit worth keeping if the pattern lands)
# ---------------------------------------------------------------------------


# --- Plausible resource stubs (stand in for real AsyncBackend, AsyncSession) --


@dataclass(frozen=True, slots=True)
class AsyncBackend:
    name: str  # "asyncio", "trio"


@dataclass(frozen=True, slots=True)
class AsyncSession:
    id: int
    origin: str  # "shared", "arrange" — for tracing only


# --- Timeout treatment (three candidate designs) ---------------------------


class TimeoutDesign(enum.StrEnum):
    """Three candidate shapes for `default_timeout`."""

    OPTIONAL = "int | None"  # v2 default — semantic Optional (Rule 7)
    ZERO_SENTINEL = "int (0=off)"  # eliminates None, but 0 conflicts w/ asyncio
    SUM_TYPE = "TimeoutOff | Set(n)"  # explicit, no None, no magic number


@dataclass(frozen=True, slots=True)
class TimeoutOff:
    """Sum-type variant: no timeout configured."""


@dataclass(frozen=True, slots=True)
class TimeoutSet:
    """Sum-type variant: N seconds."""

    seconds: int


Timeout = TimeoutOff | TimeoutSet


# --- ExecutionPlan: pure test-shape data (NO session/backend fields) --------


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Same shape across all three timeout designs.

    `default_timeout` is typed as the union of all three representations so the
    prototype can toggle it. The real ExecutionPlan would use exactly one.
    """

    fn_name: str
    is_async: bool
    kwargs_has_async_value: bool
    marks: tuple[str, ...]
    no_message_lines: tuple[int, ...]
    default_timeout: int | None | Timeout


def _plan_optional_count(plan: ExecutionPlan) -> int:
    """Count None-valued fields (only default_timeout can be None)."""
    return 1 if plan.default_timeout is None else 0


# --- Middleware protocol -----------------------------------------------------


class Middleware(Protocol):
    def apply(
        self, plan: ExecutionPlan, next_fn: Callable[[], str]
    ) -> Callable[[], str]: ...

    def describe(self) -> str: ...


# --- Core middlewares -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AsyncDepGuardMiddleware:
    def apply(
        self, plan: ExecutionPlan, next_fn: Callable[[], str]
    ) -> Callable[[], str]:
        if not plan.is_async and plan.kwargs_has_async_value:
            return lambda: "ERROR: async fixture in sync test"
        return next_fn

    def describe(self) -> str:
        return "AsyncDepGuardMiddleware()"


@dataclass(frozen=True, slots=True)
class TimeoutMiddleware:
    """Adapts to whichever timeout design the ExecutionPlan carries.

    - OPTIONAL: `default_timeout=None` means "skip". Middleware is inserted
      conditionally by the builder (INSERT-time conditional).
    - ZERO_SENTINEL: `default_timeout=0` means "skip". Middleware is always
      inserted; apply() noops (APPLY-time conditional).
    - SUM_TYPE: `default_timeout=TimeoutOff()` means "skip". Middleware is
      always inserted; apply() dispatches on the variant.
    """

    def apply(
        self, plan: ExecutionPlan, next_fn: Callable[[], str]
    ) -> Callable[[], str]:
        if "timeout" in plan.marks:
            return next_fn
        secs = self._resolve(plan.default_timeout)
        if secs is None:
            return next_fn
        return lambda: f"[timeout={secs}s] {next_fn()}"

    @staticmethod
    def _resolve(dt: int | None | Timeout) -> int | None:
        if dt is None:
            return None
        if isinstance(dt, int):
            return dt if dt > 0 else None  # 0-sentinel
        if isinstance(dt, TimeoutOff):
            return None
        return dt.seconds

    def describe(self) -> str:
        return "TimeoutMiddleware()"


# --- Session-strategy middlewares (unchanged from v2) -----------------------


@dataclass(frozen=True, slots=True)
class SharedSessionMiddleware:
    session: AsyncSession

    def apply(
        self, plan: ExecutionPlan, next_fn: Callable[[], str]
    ) -> Callable[[], str]:
        return lambda: (
            f"session={self.session.origin}#{self.session.id}.run({next_fn()})"
        )

    def describe(self) -> str:
        return f"SharedSessionMiddleware(session={self.session!r})"


@dataclass(frozen=True, slots=True)
class ArrangeSessionMiddleware:
    session: AsyncSession

    def apply(
        self, plan: ExecutionPlan, next_fn: Callable[[], str]
    ) -> Callable[[], str]:
        return lambda: (
            f"session={self.session.origin}#{self.session.id}.run({next_fn()})"
        )

    def describe(self) -> str:
        return f"ArrangeSessionMiddleware(session={self.session!r})"


@dataclass(frozen=True, slots=True)
class AsyncBridgeMiddleware:
    backend: AsyncBackend

    def apply(
        self, plan: ExecutionPlan, next_fn: Callable[[], str]
    ) -> Callable[[], str]:
        return lambda: f"backend={self.backend.name}.acquire_session().run({next_fn()})"

    def describe(self) -> str:
        return f"AsyncBridgeMiddleware(backend={self.backend!r})"


# --- Session-strategy sum type ---------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionStrategy:
    """Sum-type root. Not directly instantiated."""


@dataclass(frozen=True, slots=True)
class Shared(SessionStrategy):
    session: AsyncSession


@dataclass(frozen=True, slots=True)
class Arrange(SessionStrategy):
    session: AsyncSession


@dataclass(frozen=True, slots=True)
class Fresh(SessionStrategy):
    backend: AsyncBackend


def resolve_strategy(
    *,
    used_shared: bool,
    shared: AsyncSession | None,
    arrange: AsyncSession | None,
    default_backend: AsyncBackend,
) -> SessionStrategy:
    """Optionals collapse HERE, at the edge — never inside builder/middlewares."""
    if used_shared and shared is not None:
        return Shared(shared)
    if arrange is not None:
        return Arrange(arrange)
    return Fresh(default_backend)


# --- Plugin slot enumeration (Q: what are all the slots?) -------------------


class Slot(enum.StrEnum):
    """The full set of registration points.

    Position in the fixed anchor chain:

        [PRE_GUARD]     AsyncDepGuard    [POST_GUARD]
                                                       TimeoutMiddleware
                                                                        [PRE_SESSION]
                                                                                     SessionStrategy

    There are only 3 slots because there are only 3 fixed anchors (guard,
    timeout, session). Anything before-guard, between-anchors, or immediately-
    before-session must fit into one of these. Middleware "inside" the session
    isn't a slot — session is innermost by definition (it owns the base runner).
    """

    PRE_GUARD = "pre_guard"
    POST_GUARD = "post_guard"
    PRE_SESSION = "pre_session"


# --- Plugin middlewares — each with a real reason for its slot --------------


@dataclass(frozen=True, slots=True)
class ScreenshotOnFailureMiddleware:
    """Captures screenshot when the test fails.

    Slot: PRE_GUARD. Must be OUTERMOST — needs to see the final result even
    when the guard short-circuits with an error. If it went post_guard, it
    would miss "async fixture in sync test" failures.
    """

    output_dir: str

    def apply(
        self, plan: ExecutionPlan, next_fn: Callable[[], str]
    ) -> Callable[[], str]:
        def wrapped() -> str:
            result = next_fn()
            if "ERROR" in result:
                return f"screenshot[{self.output_dir}/{plan.fn_name}.png]({result})"
            return result

        return wrapped

    def describe(self) -> str:
        return f"ScreenshotOnFailure(dir={self.output_dir!r})"


@dataclass(frozen=True, slots=True)
class RetryOnFlakyMiddleware:
    """Retries the test up to N times on failure.

    Slot: POST_GUARD. Must be BEFORE timeout so the timeout applies per-
    attempt, not to the whole retry loop. If pre_guard, the guard would
    only run once and any retries would re-run the guard needlessly.
    """

    max_attempts: int

    def apply(
        self, plan: ExecutionPlan, next_fn: Callable[[], str]
    ) -> Callable[[], str]:
        return lambda: f"retry[max={self.max_attempts}]({next_fn()})"

    def describe(self) -> str:
        return f"RetryOnFlaky(max_attempts={self.max_attempts})"


@dataclass(frozen=True, slots=True)
class CoverageTracerMiddleware:
    """Starts/stops coverage tracer around the test body.

    Slot: PRE_SESSION. Must be INNERMOST of the always segment — anything
    outside would trace oxitest's own framework code (guard, timeout, retry)
    as if it were test code, polluting coverage.
    """

    provider: str

    def apply(
        self, plan: ExecutionPlan, next_fn: Callable[[], str]
    ) -> Callable[[], str]:
        return lambda: f"cov[{self.provider}].start().run({next_fn()}).stop()"

    def describe(self) -> str:
        return f"CoverageTracer(provider={self.provider!r})"


# --- Pipeline builder -------------------------------------------------------


@dataclass(slots=True)
class MiddlewarePipeline:
    """Fixed anchor chain (guard, timeout, session) with three plugin slots.

    Anchor placement changes based on timeout design:

    OPTIONAL:      pre_guard AsyncDepGuard post_guard [Timeout if not None] pre_session session
    ZERO_SENTINEL: pre_guard AsyncDepGuard post_guard  Timeout               pre_session session
    SUM_TYPE:      pre_guard AsyncDepGuard post_guard  Timeout               pre_session session

    Under ZERO_SENTINEL/SUM_TYPE, Timeout moves from "conditional" (insert-time)
    to "always" (apply-time noop) — the shape is uniform across all tests.
    """

    timeout_design: TimeoutDesign
    default_timeout: int | None | Timeout
    pre_guard: list[Middleware] = field(default_factory=list)
    post_guard: list[Middleware] = field(default_factory=list)
    pre_session: list[Middleware] = field(default_factory=list)

    def register(self, mw: Middleware, *, slot: Slot | str) -> None:
        """Accepts Slot enum OR bare string — StrEnum makes them interchangeable."""
        getattr(self, str(slot)).append(mw)

    def build_for(
        self, plan: ExecutionPlan, strategy: SessionStrategy
    ) -> list[Middleware]:
        mws: list[Middleware] = []
        mws.extend(self.pre_guard)
        mws.append(AsyncDepGuardMiddleware())
        mws.extend(self.post_guard)
        # Under OPTIONAL, Timeout is conditional at insert-time.
        # Under ZERO_SENTINEL / SUM_TYPE, Timeout is always inserted (apply-time).
        if self.timeout_design == TimeoutDesign.OPTIONAL:
            if self.default_timeout is not None:
                mws.append(TimeoutMiddleware())
        else:
            mws.append(TimeoutMiddleware())
        mws.extend(self.pre_session)
        if plan.is_async:
            match strategy:
                case Shared(session=s):
                    mws.append(SharedSessionMiddleware(s))
                case Arrange(session=s):
                    mws.append(ArrangeSessionMiddleware(s))
                case Fresh(backend=b):
                    mws.append(AsyncBridgeMiddleware(b))
        return mws


def compose(middlewares: list[Middleware], plan: ExecutionPlan) -> Callable[[], str]:
    execute: Callable[[], str] = lambda: "<base_runner>"  # noqa: E731
    for mw in reversed(middlewares):
        execute = mw.apply(plan, execute)
    return execute


# ---------------------------------------------------------------------------
# THROWAWAY TUI
# ---------------------------------------------------------------------------


PLUGIN_CATALOG: dict[str, tuple[Slot, Middleware, str]] = {
    "screenshot": (
        Slot.PRE_GUARD,
        ScreenshotOnFailureMiddleware(output_dir="./failures"),
        "must be OUTERMOST — needs to see guard failures too",
    ),
    "retry": (
        Slot.POST_GUARD,
        RetryOnFlakyMiddleware(max_attempts=3),
        "must be BEFORE timeout so timeout applies per-attempt",
    ),
    "cov": (
        Slot.PRE_SESSION,
        CoverageTracerMiddleware(provider="coverage.py"),
        "must be INNERMOST — else it traces framework code as test code",
    ),
}


@dataclass(slots=True)
class Scenario:
    is_async: bool = True
    kwargs_has_async_value: bool = False
    has_timeout_mark: bool = False
    has_default_timeout: bool = True  # on/off toggle; value depends on design
    timeout_design: TimeoutDesign = TimeoutDesign.OPTIONAL
    has_shared: bool = False
    has_arrange: bool = False
    backend_name: str = "asyncio"
    active_plugins: set[str] = field(default_factory=set)
    _shared_session: AsyncSession = field(
        default_factory=lambda: AsyncSession(id=101, origin="shared")
    )
    _arrange_session: AsyncSession = field(
        default_factory=lambda: AsyncSession(id=202, origin="arrange")
    )

    def default_timeout_value(self) -> int | None | Timeout:
        if self.timeout_design == TimeoutDesign.OPTIONAL:
            return 60 if self.has_default_timeout else None
        if self.timeout_design == TimeoutDesign.ZERO_SENTINEL:
            return 60 if self.has_default_timeout else 0
        return TimeoutSet(60) if self.has_default_timeout else TimeoutOff()

    def to_plan(self) -> ExecutionPlan:
        return ExecutionPlan(
            fn_name="test_something",
            is_async=self.is_async,
            kwargs_has_async_value=self.kwargs_has_async_value,
            marks=("timeout",) if self.has_timeout_mark else (),
            no_message_lines=(),
            default_timeout=self.default_timeout_value(),
        )

    def strategy(self) -> SessionStrategy:
        return resolve_strategy(
            used_shared=self.has_shared,
            shared=self._shared_session if self.has_shared else None,
            arrange=self._arrange_session if self.has_arrange else None,
            default_backend=AsyncBackend(self.backend_name),
        )

    def make_pipeline(self) -> MiddlewarePipeline:
        pipeline = MiddlewarePipeline(
            timeout_design=self.timeout_design,
            default_timeout=self.default_timeout_value(),
        )
        for key in self.active_plugins:
            slot, mw, _ = PLUGIN_CATALOG[key]
            # Registration accepts Slot enum OR string — both work.
            pipeline.register(mw, slot=slot)
        return pipeline

    def build(self) -> list[Middleware]:
        return self.make_pipeline().build_for(self.to_plan(), self.strategy())


PRESETS: dict[str, tuple[str, Scenario]] = {
    "1": (
        "sync, no timeout — minimal chain",
        Scenario(is_async=False, has_default_timeout=False),
    ),
    "2": ("sync + default_timeout", Scenario(is_async=False)),
    "3": (
        "sync + async-value kwarg (guard trips)",
        Scenario(is_async=False, kwargs_has_async_value=True),
    ),
    "4": ("async, fresh session (default)", Scenario()),
    "5": ("async + arrange session", Scenario(has_arrange=True)),
    "6": ("async + shared session", Scenario(has_shared=True)),
    "7": (
        "async + shared+arrange (shared wins)",
        Scenario(has_shared=True, has_arrange=True),
    ),
}


BOLD = "\x1b[1m"
DIM = "\x1b[2m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"
MAGENTA = "\x1b[35m"
RED = "\x1b[31m"
RESET = "\x1b[0m"


def _b(s: str) -> str:
    return f"{BOLD}{s}{RESET}"


def _d(s: str) -> str:
    return f"{DIM}{s}{RESET}"


def _color(s: str, code: str) -> str:
    return f"{code}{s}{RESET}"


def _zone_of(mw: Middleware, pipeline: MiddlewarePipeline, plan: ExecutionPlan) -> str:
    if mw in pipeline.pre_guard:
        return "plugin"
    if isinstance(mw, AsyncDepGuardMiddleware):
        return "always"
    if mw in pipeline.post_guard:
        return "plugin"
    if isinstance(mw, TimeoutMiddleware):
        if pipeline.timeout_design == TimeoutDesign.OPTIONAL:
            return "conditional"
        # apply-time conditional — always zone, but noop marker if disabled
        return "always"
    if mw in pipeline.pre_session:
        return "plugin"
    return "session"


def _zone_style(zone: str) -> str:
    return {
        "always": GREEN,
        "conditional": YELLOW,
        "plugin": MAGENTA,
        "session": CYAN,
    }.get(zone, RESET)


def _render(scn: Scenario) -> str:
    plan = scn.to_plan()
    pipeline = scn.make_pipeline()
    mws = pipeline.build_for(plan, scn.strategy())
    result = compose(mws, plan)()
    none_ct = _plan_optional_count(plan)

    dt_repr = repr(plan.default_timeout)
    none_style = YELLOW if none_ct <= 1 else RED
    none_note = (
        f"{none_ct}  {_d('(default_timeout=None — semantic, Rule 7)')}"
        if none_ct == 1
        else str(none_ct)
    )

    # Timeout-design summary — the answer to Q: does Timeout belong to always?
    tm_zone = (
        "always" if scn.timeout_design != TimeoutDesign.OPTIONAL else "conditional"
    )
    tm_zone_c = _color(tm_zone, _zone_style(tm_zone))
    design_summary = {
        TimeoutDesign.OPTIONAL: f"Timeout is {tm_zone_c} — inserted only when default_timeout is not None. "
        f"{_d('Optional lives on the plan.')}",
        TimeoutDesign.ZERO_SENTINEL: f"Timeout is {tm_zone_c} — always inserted; apply() noops when value ≤ 0. "
        f"{_d('No Optional, but 0 conflicts with asyncio semantics.')}",
        TimeoutDesign.SUM_TYPE: f"Timeout is {tm_zone_c} — always inserted; apply() dispatches on variant. "
        f"{_d('No Optional, no magic number, but heavier declaration.')}",
    }[scn.timeout_design]

    lines = [
        _b("=== Composable Middleware prototype v3 (Wayfinder T4 / #1555) ==="),
        _d("Design B (SessionStrategy sum type) + zoned pipeline + slot enum."),
        "",
        _b("Timeout design")
        + f"   [{_color(scn.timeout_design.name, CYAN)}]  "
        + _d(f"type: {scn.timeout_design.value}"),
        f"  {design_summary}",
        "",
        _b("Scenario"),
        f"  fn                : {_color('async def' if scn.is_async else 'def', GREEN)} {plan.fn_name}(...)",
        f"  kwargs has async  : {scn.kwargs_has_async_value}",
        f"  @timeout mark     : {scn.has_timeout_mark}",
        f"  default_timeout   : {'set (60s)' if scn.has_default_timeout else 'unset/off'}  -> {dt_repr}",
        f"  has_shared_async  : {scn.has_shared}",
        f"  has_arrange       : {scn.has_arrange}",
        f"  backend           : {scn.backend_name}",
        "",
        _b("ExecutionPlan"),
        f"  default_timeout      = {dt_repr}",
        f"  {_d('None-valued fields   =')} {_color(none_note, none_style)}",
        "",
        _b("SessionStrategy") + f"    strategy = {scn.strategy()!r}",
        "",
        _b("Slot enumeration  ")
        + _d("(only 3 slots exist — bounded by the 3 fixed anchors)"),
        f"  {_color('PRE_GUARD', MAGENTA)}   : before AsyncDepGuard (outermost)",
        f"  {_color('POST_GUARD', MAGENTA)}  : between guard and timeout",
        f"  {_color('PRE_SESSION', MAGENTA)} : between timeout and session (innermost)",
        "",
        _b("Registration ergonomics  ")
        + _d("(Slot StrEnum accepts both enum and string)"),
        "  "
        + _d(
            "pipeline.register(mw, slot=Slot.PRE_GUARD)   # ty-checked, refactor-safe"
        ),
        "  "
        + _d(
            'pipeline.register(mw, slot="pre_guard")       # config-friendly (pyproject.toml)'
        ),
        "",
        _b("Registered plugins")
        + f"  ({len(scn.active_plugins)}/{len(PLUGIN_CATALOG)} active)",
    ]
    for key, (slot, mw, why) in PLUGIN_CATALOG.items():
        marker = _color("✓", GREEN) if key in scn.active_plugins else _d("·")
        lines.append(f"  {marker} {mw.describe():<45} {_color(slot.name, MAGENTA)}")
        lines.append(f"      {_d('why: ' + why)}")

    lines += [
        "",
        _b(f"Built chain  ->  {len(mws)} middleware(s)"),
    ]
    zone_letter = {"always": "A", "conditional": "C", "plugin": "P", "session": "S"}
    for i, mw in enumerate(mws):
        prefix = "  " + ("├─ " if i < len(mws) - 1 else "└─ ")
        zone = _zone_of(mw, pipeline, plan)
        tag = _color(f"[{zone_letter[zone]}]", _zone_style(zone))
        # For always-inserted TimeoutMiddleware, flag when it will noop.
        noop_note = ""
        if (
            isinstance(mw, TimeoutMiddleware)
            and TimeoutMiddleware._resolve(plan.default_timeout) is None
        ):
            noop_note = _d("  (noops — no effective timeout)")
        lines.append(f"{prefix}{tag} {mw.describe()}{noop_note}")

    lines += [
        _d(
            f"       {_color('[A]', GREEN)}=always  {_color('[C]', YELLOW)}=conditional  "
            f"{_color('[P]', MAGENTA)}=plugin  {_color('[S]', CYAN)}=session"
        ),
        "",
        _b("Composed call (outer -> inner)"),
        f"  {_color(result, CYAN)}",
        "",
        _b("Presets"),
    ]
    for k, (label, _) in PRESETS.items():
        lines.append(f"  {_b('[' + k + ']')} {label}")

    lines += [
        "",
        _b("Plugin toggles")
        + "   "
        + "   ".join(f"{_b('[' + k + ']')} {k}" for k in PLUGIN_CATALOG),
        "",
        _b("Scenario toggles"),
        f"  {_b('[f]')} sync/async     {_b('[t]')} default_timeout on/off   {_b('[m]')} @timeout mark",
        f"  {_b('[s]')} has_shared     {_b('[a]')} has_arrange              {_b('[x]')} kwargs-has-async",
        f"  {_b('[b]')} backend swap   {_b('[T]')} cycle timeout design     {_b('[q]')} quit",
    ]
    return "\n".join(lines)


def _clear() -> None:
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def _copy_preset(
    preset: Scenario, *, keep_plugins: set[str], keep_design: TimeoutDesign
) -> Scenario:
    return Scenario(
        is_async=preset.is_async,
        kwargs_has_async_value=preset.kwargs_has_async_value,
        has_timeout_mark=preset.has_timeout_mark,
        has_default_timeout=preset.has_default_timeout,
        timeout_design=keep_design,
        has_shared=preset.has_shared,
        has_arrange=preset.has_arrange,
        backend_name=preset.backend_name,
        active_plugins=set(keep_plugins),
    )


def _cycle_design(current: TimeoutDesign) -> TimeoutDesign:
    order = [
        TimeoutDesign.OPTIONAL,
        TimeoutDesign.ZERO_SENTINEL,
        TimeoutDesign.SUM_TYPE,
    ]
    return order[(order.index(current) + 1) % len(order)]


def main() -> int:
    scn = PRESETS["4"][1]
    while True:
        _clear()
        print(_render(scn))
        try:
            key = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not key:
            continue
        low = key.lower()
        if low == "q":
            return 0
        if low in PRESETS:
            _, preset = PRESETS[low]
            scn = _copy_preset(
                preset, keep_plugins=scn.active_plugins, keep_design=scn.timeout_design
            )
            continue
        if low in PLUGIN_CATALOG:
            scn.active_plugins.symmetric_difference_update({low})
            continue
        if key == "T":  # capital T = cycle design (distinct from [t] toggle)
            scn.timeout_design = _cycle_design(scn.timeout_design)
        elif low == "f":
            scn.is_async = not scn.is_async
        elif low == "t":
            scn.has_default_timeout = not scn.has_default_timeout
        elif low == "m":
            scn.has_timeout_mark = not scn.has_timeout_mark
        elif low == "s":
            scn.has_shared = not scn.has_shared
        elif low == "a":
            scn.has_arrange = not scn.has_arrange
        elif low == "x":
            scn.kwargs_has_async_value = not scn.kwargs_has_async_value
        elif low == "b":
            scn.backend_name = "trio" if scn.backend_name == "asyncio" else "asyncio"


if __name__ == "__main__":
    if not sys.stdin.isatty() and os.environ.get("PROTOTYPE_DEMO") != "1":
        print(__doc__)
        raise SystemExit(0)
    raise SystemExit(main())
