"""Consolidated error hierarchy for oxitest internals."""

from __future__ import annotations

__all__ = [
    "AmbiguousFixtureError",
    "ArrangeError",
    "AsyncDependencyError",
    "AsyncFixtureAccessError",
    "BackendNotFoundError",
    "BoundaryError",
    "BroadFixtureTypeError",
    "CollectionError",
    "ConflictingBackendError",
    "ConflictingCoverageError",
    "ConflictingDebuggerError",
    "ExecutionError",
    "FixtureCycleError",
    "FixtureError",
    "FixtureNotFoundError",
    "FixtureSetupError",
    "FixtureTypeNotFoundError",
    "InternalError",
    "LoadError",
    "OxitestError",
    "OxitestTimeoutError",
    "ParametrizeError",
    "SharedFixtureMutationError",
    "TestContextUnavailableError",
    "TestIdentityUnavailableError",
    "UnannotatedFixtureParamError",
    "UsageError",
    "is_usage_error",
]


from typing import TYPE_CHECKING, Any

from oxitest._bridge._paths import format_path

if TYPE_CHECKING:
    from oxitest._bridge.result import TestResult


class OxitestError(Exception):
    """Base for every exception oxitest raises."""


class FixtureError(OxitestError):
    """Fixture-related errors."""


class ExecutionError(OxitestError):
    """Test execution errors."""


# ─── Fixture errors ─────────────────────────────────────────────────────────


def _default_fixture_not_found_message(name: str, namespace: str) -> str:
    """Build the default error message for a missing fixture lookup by name.

    The inline clause is unconditional rather than catalog-driven. Inline
    declarations register on module import, so whether this process knows about
    one depends on worker assignment and import order — a hint that appeared
    only sometimes would be worse than one that is always true and sometimes
    irrelevant.

    This holds for the *namespaced* message below. The bare shortcut message
    (``shortcut_miss_message``) does branch, on a pure name test that reads no
    catalog and so cannot vary with the schedule (#1759).
    """
    if namespace:
        return (
            f"fixture '{name}' not found in namespace '{namespace}'.\n"
            f"  Hint: check that '{namespace}' declares a fixture named "
            f"'{name}' — in the __fixtures__.py or __init__.py of the anchor "
            f"directory '{namespace}' — or verify the spelling.\n"
            f"  If '{name}' is declared inline in another test module it is "
            f"capped at 'module' lifetime and cannot be used here; move it to "
            f"__fixtures__.py to share it."
        )
    return (
        f"fixture '{name}' not found.\n"
        f"  Hint: declare it with @oxi.fixture in a __fixtures__.py, or have a "
        f"plugin provide it, and annotate the parameter with Fixture[<type>] "
        f"in the test signature."
    )


class FixtureNotFoundError(FixtureError):
    """Raised when a requested fixture name cannot be found in the registry.

    Votes for exit 4 (see ``is_usage_error``): reaching this error at run time
    means the test asked for a fixture it cannot see, which is a wiring
    mistake. A name that exists nowhere at all is refused earlier, by the
    name-based collection validator, and exits 3 without ever reaching the vote.
    """

    def __init__(
        self,
        name: str,
        *,
        namespace: str = "",
        message: str | None = None,
    ) -> None:
        if message is None:
            message = _default_fixture_not_found_message(name, namespace)
        super().__init__(message)
        self.fixture_name = name
        self.namespace = namespace


class FixtureTypeNotFoundError(FixtureNotFoundError):
    """Raised by get_fixture_by_type when no fixture is registered for the given type.

    Message names the three registration routes (BuiltinFixture, plugin
    FixtureProvider, declared fixture return annotation) instead of the
    by-name hint. ``FixtureProvider`` is deliberate: Q21 kept it out of
    #1720's scope, so it is still the route for a fixture whose value a
    plugin entry point computes.
    """

    def __init__(self, type_name: str) -> None:
        msg = (
            f"no fixture registered for type '{type_name}' — must be a "
            f"BuiltinFixture, a plugin-provided FixtureProvider with matching "
            f"fixture_type, or an @oxi.fixture declaration with '{type_name}' "
            f"as its return annotation."
        )
        super().__init__(type_name, message=msg)


class BoundaryError(FixtureError):
    """Raised when a test reaches a fixture outside its anchor package.

    When it fires:
        ADR-0009's B1 strict boundary — a fixture is usable only by tests in
        its anchor package or a descendant of it. A sibling or unrelated
        package reaching in raises here, at access time. Collection time cannot
        do it: the prescan extracts fixture *declarations*, never *usages*, and
        ``getattr(fx, name)`` would defeat a static gate anyway (#1758).

        Distinct from ``FixtureNotFoundError`` on purpose. The fixture exists —
        reporting "not found" would send the user hunting for a typo in a name
        that is spelled correctly.

    How to fix:
        Three legal restructurings, all listed in the message: move the
        declaration up to a common ancestor package, move the test into the
        fixture's package, or declare a matching fixture in the test's own package.
        There is no allow-comment escape hatch and no ``strict = "warn"``
        softening.

    See Also:
        - ``FixtureNotFoundError`` — the segment is unknown anywhere in the run.

    Examples:
        >>> from oxitest import BoundaryError, raises
        >>> with raises(BoundaryError):
        ...     raise BoundaryError("conn", "api", "/t/api", "/t/admin/test_a.py")

    """

    #: Stable diagnostic code — lets docs link this failure and CI grep for it
    #: without matching prose. The ``fixture-shortcut-in-strict`` code once
    #: cited here never existed; ADR-0009 Amendment 3 retracted its dial.
    CODE = "fixture-boundary"

    def __init__(  # noqa: PLR0913 — four name the access, and the last two are the display base and the leaf probe
        self,
        name: str,
        namespace: str,
        anchor: str,
        module_path: str,
        *,
        leaf_exists: bool = True,
        rootdir: str | None = None,
    ) -> None:
        qualified = f"{namespace}.{name}" if namespace else name
        anchor_rel = format_path(anchor, rootdir)
        test_rel = format_path(module_path, rootdir)
        message = (
            f"[{self.CODE}] fixture '{qualified}' is not visible from this test.\n"
            f"  Fixture anchor: {anchor_rel}\n"
            f"  This test:      {test_rel}\n"
            f"  B1: a fixture is usable only by tests in its anchor package or\n"
            f"      below (ADR-0009 Rule 3).\n"
            f"  Three ways forward:\n"
            f"    1. Move the declaration to a package that is an ancestor of both\n"
            f"    2. Move the test into {anchor_rel} or a package below it\n"
            f"    3. Declare a fixture of the same shape in this test's own package\n"
        )
        if not leaf_exists:
            message += (
                f"  Also: namespace '{namespace}' has no fixture named "
                f"'{name}' — fixing the spelling alone "
                f"will not make this access legal.\n"
            )
        super().__init__(message)
        self.fixture_name = name
        self.namespace = namespace
        self.anchor = anchor
        self.module_path = module_path


class FixtureCycleError(FixtureError):
    """Raised when a circular dependency is detected in the fixture graph."""

    def __init__(self, name: str, chain: set[str]) -> None:
        path = " → ".join(sorted(chain)) + f" → {name}"
        super().__init__(
            f"fixture cycle detected: {path}\n"
            f"  Hint: break the cycle by removing a dependency or "
            f"extracting shared setup into a separate fixture."
        )


class FixtureSetupError(FixtureError):
    """Raised when a fixture function raises an exception during setup."""

    def __init__(self, name: str, cause: Exception) -> None:
        super().__init__(
            f"Error in fixture '{name}': {cause}\n"
            f"  Hint: check the fixture function body for the exception above. "
            f"If using a yield fixture, the error is in setup (before yield)."
        )
        self.fixture_name = name


class AsyncDependencyError(FixtureSetupError):
    """Raised when a fixture dependency's lifetime cannot hold its value.

    When it fires:
        Raised only by ``_check_async_dep``, which covers all three refusals:
        a fixture that outlives the test depending on a shorter-lived async
        fixture, a sync fixture depending on an async one, and a shared fixture
        depending on a non-shared async one. An async value is bound to one
        test's event loop, so a wider-lived holder would hand it to tests whose
        loop is gone.

    Why a subclass rather than a flag on ``FixtureSetupError``:
        That error also wraps genuine exceptions raised inside a user's fixture
        body, and those are ordinary failures rather than wiring mistakes.
        Marking the parent would exit 4 for every broken fixture body. Every
        existing ``except FixtureSetupError`` still catches this unchanged.
    """


class TestContextUnavailableError(FixtureError):
    """Raised when ``TestContext.current()`` is called outside a test body (#1949).

    ``current()`` reads ambient state, so it has to refuse rather than guess.
    The position is named because the illegal positions need different fixes: a
    fixture body should declare ``ctx: TestContext``, import-time code has no
    test to describe at all, and a wider-lifetime teardown runs after the test
    it might have meant is already over.

    Public so that a helper which must work both inside and outside a test can
    catch it — refusing is only usable if the refusal has a name callers can
    name.

    Examples:
        >>> from oxitest import TestContextUnavailableError, raises
        >>> with raises(TestContextUnavailableError):
        ...     raise TestContextUnavailableError("inside a fixture body")

    """

    def __init__(self, position: str) -> None:
        super().__init__(
            f"TestContext.current() is not available {position}.\n"
            f"  It is legal only from the body of a running test, and from "
            f"plain functions that body calls.\n"
            f"  Inside a fixture, declare `ctx: TestContext` as a parameter "
            f"instead — that context supports teardown registration."
        )


class TestIdentityUnavailableError(FixtureError):
    """Raised when ``TestContext`` identity is read outside a test (#1874).

    A ``TestContext`` injected into a *fixture* describes the resolution, not a
    test: there is no node id, no marks, and no test name. Reading one used to
    hand back a wrong-but-well-formed value — ``ctx.name`` returned the
    fixture's own name, so ``f"test_{ctx.name}"`` produced an identical string
    for every test in the run.
    """

    def __init__(self, accessed: str, subject: str = "TestContext") -> None:
        super().__init__(
            f"{subject}.{accessed} is not available here.\n"
            f"  This context was built for a fixture resolution, not for a "
            f"test, so there is no test to name.\n"
            f"  Inside a fixture, ctx supports teardown registration only: "
            f"ctx.addfinalizer(...) / ctx.on_teardown(...).\n"
            f"  To read the test's identity in a fixture, declare "
            f'`test: TestIdentity` and lifetime="function" (#1879).\n'
            f"  On a test itself, use oxi.current_test()."
        )


class UnannotatedFixtureParamError(FixtureError):
    """Raised when a parameter matches a fixture name but lacks `Fixture[T]`.

    Oxitest requires explicit opt-in to fixture injection via the `Fixture[T]`
    type annotation.  Unannotated parameters are never resolved automatically.
    """

    def __init__(self, param_name: str, fn_name: str) -> None:
        super().__init__(
            f"parameter '{param_name}' in {fn_name} is not injected.\n"
            f"To request a fixture, annotate it: {param_name}: Fixture[<type>]\n"
            f"Unannotated parameters are not resolved by oxitest."
        )
        self.param_name = param_name
        self.fn_name = fn_name


class SharedFixtureMutationError(RuntimeError, OxitestError):
    """Raised when code attempts to mutate a shared (immutable) fixture value.

    When it fires:
        Shared and session-scope fixtures are exposed to tests as immutable
        ``FrozenProxy`` wrappers so that per-test mutations cannot leak into
        sibling tests. Any write attempt — attribute assignment, item
        assignment, or the setter half of an augmented assign like
        ``x.attr += y`` — on such a proxy raises.

    How to fix:
        If per-test mutation is intentional, declare the fixture
        ``@oxi.fixture(lifetime="function")`` so each test gets its own
        value. If it is unintentional, treat the fixture value as
        read-only — build a fresh derived value instead of writing to the
        cached one.

    See Also:
        - ``oxitest.fixture`` for the lifetime tiers.

    Examples:
        Mutating a shared-fixture proxy raises this error:

        >>> from oxitest import SharedFixtureMutationError, raises
        >>> from oxitest._bridge.proxy import FrozenProxy
        >>> class Config:
        ...     mode = "prod"
        >>> proxy = FrozenProxy(Config())
        >>> with raises(SharedFixtureMutationError):
        ...     proxy.mode = "dev"

    """


# ─── Execution errors ────────────────────────────────────────────────────────


class OxitestTimeoutError(ExecutionError):
    """Raised inside a test when its deadline fires."""


class BackendNotFoundError(OxitestError):
    """Raised when the configured async backend name matches no provider."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"async backend '{name}' not found.\n"
            f"  Hint: install the backend plugin (e.g. oxitest-asyncio) "
            f"and ensure it is listed in [tool.oxitest] plugins."
        )
        self.backend_name = name


class ConflictingBackendError(OxitestError):
    """Raised when multiple plugins provide the same backend name."""

    def __init__(self, name: str, providers: list[str]) -> None:
        joined = ", ".join(providers)
        super().__init__(f"multiple plugins provide async backend '{name}': {joined}")
        self.backend_name = name
        self.providers = providers


class ConflictingDebuggerError(OxitestError):
    """Raised when multiple plugins provide a debugger backend."""

    def __init__(self, providers: list[str]) -> None:
        joined = ", ".join(providers)
        super().__init__(f"multiple plugins provide a debugger backend: {joined}")
        self.providers = providers


class ConflictingCoverageError(OxitestError):
    """Multiple plugins provide a CoverageProvider."""

    def __init__(self, providers: list[str]) -> None:
        joined = ", ".join(providers)
        super().__init__(f"multiple plugins provide a coverage provider: {joined}")
        self.providers = providers


# ─── Parametrize / loading errors ─────────────────────────────────────────────


class ParametrizeError(OxitestError):
    """Raised when parametrize case resolution fails due to misconfiguration."""


class UsageError(OxitestError):
    """Raised when a user-facing API is used incorrectly."""


class InternalError(OxitestError):
    """Raised when an oxitest invariant is broken.

    Not a usage error: the request was valid and oxitest failed to hold a
    property of its own. Deliberately outside ``_USAGE_ERROR_TYPES`` — exit 4
    tells the user that their request was invalid, which is the wrong thing to
    tell someone who has found a bug (#2172).
    """


class LoadError(OxitestError):
    """Raised when a module cannot be loaded or a function cannot be resolved."""

    def __init__(self, result: TestResult) -> None:
        self.result = result


class CollectionError(OxitestError):
    """Raised when a test function is misconfigured at collection time.

    Examples include @oxi.arrange specifying a fixture that is also declared
    as a parameter — a redundant declaration that risks double-instantiation.
    """


class TestReturnedValueError(OxitestError):
    """Raised when calling a test returned a generator instead of running (#2067).

    The runtime half of *a test function returns None*. Collection refuses
    every shape a static answer can see; a ``functools.wraps`` wrapper is not
    one of them, because ``isgeneratorfunction`` answers False on the wrapper
    while the call still returns a generator.

    Its own class rather than ``UsageError`` so it can be enrolled in
    ``_USAGE_ERROR_TYPES`` alone. ``UsageError`` is also raised from places
    that run during a test — ``_fixture_session.py`` among them — and
    enrolling the whole class would re-vote those to exit 4 as a side effect.
    """


# ─── Unified fixture backend errors ──────────────────────────────────────────


class AmbiguousFixtureError(FixtureError):
    """Raised when multiple fixtures match a binding type.

    The qualifier doesn't disambiguate among the candidates.
    """

    def __init__(self, type_name: str, candidates: list[str]) -> None:
        candidates_str = ", ".join(f"'{c}'" for c in sorted(candidates))
        super().__init__(
            f"ambiguous fixture: {len(candidates)} fixtures provide type"
            f" '{type_name}': {candidates_str}."
            f" Use the fixture name as the parameter name to disambiguate."
        )
        self.type_name = type_name
        self.candidates = candidates


class BroadFixtureTypeError(FixtureError):
    """Raised in strict mode when Fixture[Any] or Fixture[object] is used."""

    def __init__(self, param_name: str, broad_type: type) -> None:
        super().__init__(
            f"parameter '{param_name}' uses Fixture[{broad_type.__name__}]"
            f" which is too broad for type-based resolution."
            f" Use a concrete binding type."
        )
        self.param_name = param_name
        self.broad_type = broad_type


class FixtureTypeMismatchError(FixtureError):
    """Raised when the qualifier names a fixture that provides another type.

    Distinct from :class:`AmbiguousFixtureError`: exactly one fixture carries
    the name, so nothing is ambiguous. The name and the annotation disagree.
    """

    def __init__(self, param_name: str, provided: str, annotated: str) -> None:
        super().__init__(
            f"fixture '{param_name}' provides '{provided}',"
            f" but the parameter is annotated Fixture[{annotated}]."
            f" Correct the annotation, or name a different fixture."
        )
        self.param_name = param_name
        self.provided = provided
        self.annotated = annotated


# ─── Arrange errors ──────────────────────────────────────────────────────────


class AsyncFixtureAccessError(OxitestError):
    """Raised when a sync test reaches an async fixture through the ``fx.`` proxy.

    When it fires:
        The same illegal cell ``ArrangeError`` covers for ``@arrange``, on the
        other access path. A sync test cannot await anything, so handing it an
        async fixture can only produce an un-awaited coroutine — the silent
        failure kalonji-tools/oxitest#1733 exists to remove. Raised at
        **access**, before the factory runs, so the traceback points at the
        ``fx.`` line rather than into the fixture body.

    How to fix:
        The options depend on the lifetime, because this route refuses at
        every tier while the ``Fixture[T]`` route refuses only at ``function``
        (ADR-0006 Amendment 2).

        At ``function`` lifetime, two options:

        - Make the test async — ``async def test_...``, then ``await`` it.
        - Convert the fixture to sync (remove ``async`` from its ``def``).

        Above ``function``, a third: take the fixture as a ``Fixture[T]``
        parameter. It is built outside the test and already awaited, so a sync
        test receives the value.

        *Raising the lifetime* is not offered. It is what makes the parameter
        route work, and it never helps here.

    See Also:
        - ``ArrangeError`` — the same cell reached via ``@oxi.arrange``.

    Examples:
        >>> from oxitest import raises
        >>> from oxitest._bridge._errors import AsyncFixtureAccessError
        >>> with raises(AsyncFixtureAccessError):
        ...     raise AsyncFixtureAccessError("conn", "db", "function")

    """

    def __init__(self, name: str, namespace: str, lifetime: str) -> None:
        qualified = f"fx.{namespace}.{name}" if namespace else f"fx.{name}"
        # The caller holds a FixtureScope and this message is read by a user,
        # who writes a Lifetime. The two agree on every tier except the
        # narrowest: the scope is `each`, the lifetime is `function`. Printing
        # `Lifetime: each` named a value no @oxi.fixture call accepts (#1876).
        tier = "function" if lifetime == "each" else lifetime
        # Raising the lifetime is only a way forward on the *parameter* route,
        # and only above `function`. This route refuses at every tier — measured
        # at `function`, `module` and `package` — so offering it here sent the
        # reader to a change that cannot help them. Above `function` the real
        # exit is to stop using the proxy: `Fixture[T]` injection delivers the
        # value, already awaited, and ADR-0006 Amendment 2 makes that legal.
        exits = [
            f"Make the test async — `async def test_...`, then `await {qualified}`"
        ]
        wider_than_function = tier != "function"
        if wider_than_function:
            exits.append(
                f"Take it as a parameter instead — `{name}: Fixture[...]` is "
                f"built outside the test, so a sync test receives the value"
            )
        exits.append("Convert fixture to sync — remove `async` from def")
        count = "Three" if wider_than_function else "Two"
        numbered = "".join(
            f"    {index}. {exit_}\n" for index, exit_ in enumerate(exits, start=1)
        )
        message = (
            f"async fixture {name!r} cannot be used by a sync test.\n"
            f"  Accessed as: {qualified}\n"
            f"  Test kind:   sync (`def test_...`)\n"
            f"  Lifetime:    {tier}\n"
            f"  {count} ways forward:\n"
            f"{numbered}"
        )
        super().__init__(message)


class ArrangeError(OxitestError):
    """Raised when ``@arrange`` is used with an incompatible fixture.

    When it fires:
        A sync test arranges one or more async function-scope fixtures —
        the illegal cell in the (test kind x fixture kind) matrix. Detected
        during collection, not at decorator time. Async tests legally
        consume async-each fixtures; sync fixtures compose freely on either
        test kind.

    How to fix:
        Three options:

        - Make the test async — ``async def test_...``.
        - Widen the fixture lifetime to ``module``, ``package`` or ``process``.
        - Convert the fixture to sync (remove ``async`` from its ``def``).

    See Also:
        - ``FixtureNotFoundError`` — raised when ``@arrange`` names a
          fixture that doesn't exist (caught upstream by the Rust
          ``FixtureValidationPhase``).
        - ``FixtureSetupError`` — raised when an arranged fixture's
          factory itself raises.

    Examples:
        Collection-time raises can't be triggered from a doctest (no
        running collector). Direct catch pattern — the second constructor
        arg is normally ``list[tuple[str, FixtureDef]]`` naming the
        illegal fixtures:

        >>> from oxitest import ArrangeError, raises
        >>> def _fake_test(): pass
        >>> with raises(ArrangeError):
        ...     raise ArrangeError(_fake_test, [])

    """

    def __init__(
        self,
        fn: Any,
        illegal: list[tuple[str, Any]],
        *,
        rootdir: str | None = None,
    ) -> None:
        code = fn.__code__
        arranged_at = f"{format_path(code.co_filename, rootdir)}:{code.co_firstlineno}"
        count_word = (
            "1 illegal entry"
            if len(illegal) == 1
            else f"{len(illegal)} illegal entries"
        )
        illegal_lines = "\n".join(
            f"    - {name!r} (function scope) — defined at "
            f"{format_path(defn.source.func.__code__.co_filename, rootdir)}:"
            f"{defn.source.func.__code__.co_firstlineno}"
            for name, defn in illegal
        )
        message = (
            f"cannot arrange async fixture(s) on a sync test — {count_word}.\n"
            f"  Arranged at:  {arranged_at}\n"
            f"  Test kind:    sync (`def test_...`)\n"
            f"  Illegal:\n{illegal_lines}\n"
            f"  Three ways forward:\n"
            f"    1. Make the test async — `async def test_...`\n"
            f"    2. Widen the fixture lifetime to 'module', 'package' or 'process'\n"
            f"    3. Convert fixture to sync — remove `async` from def\n"
        )
        super().__init__(message)


#: Errors that mean the suite is wired wrong, rather than that a test failed.
#:
#: A tuple rather than a marker base class, so enrolling an error changes one
#: place instead of that error's declaration. ``isinstance`` covers subclasses
#: either way — ``FixtureTypeNotFoundError`` votes through
#: ``FixtureNotFoundError`` without appearing here.
#:
#: ``FixtureSetupError`` is deliberately absent. It also wraps genuine
#: exceptions from a user's fixture body, and those are ordinary failures;
#: only ``AsyncDependencyError`` names the lifetime refusal.
_USAGE_ERROR_TYPES: tuple[type[BaseException], ...] = (
    AsyncDependencyError,
    AsyncFixtureAccessError,
    BoundaryError,
    FixtureNotFoundError,
    TestReturnedValueError,
)


def is_usage_error(exc: BaseException) -> bool:
    """True when *exc* means the request was invalid, not that a test failed.

    The single source of truth for the vote. Every error funnel asks this
    rather than testing types itself; a funnel that decided for itself would be
    a second definition, and the two would drift.

    An error that votes raises the run's exit code to ``ExitCode::UsageError``
    (4) without stopping the run — every test still reports (#1761). Exit 4 is
    defined by the class of the error, not by when oxitest detects it
    (ADR-0014).
    """
    return isinstance(exc, _USAGE_ERROR_TYPES)
