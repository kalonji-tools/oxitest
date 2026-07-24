"""Consolidated error hierarchy for oxitest internals."""

from __future__ import annotations

import os

__all__ = [
    "AmbiguousFixtureError",
    "ArrangeError",
    "AutouseRegistrationError",
    "BackendNotFoundError",
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
    "LoadError",
    "OxitestError",
    "OxitestTimeoutError",
    "ParametrizeError",
    "SharedFixtureMutationError",
    "UnannotatedFixtureParamError",
]

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oxitest._bridge.result import TestResult


class OxitestError(Exception):
    """Base for all oxitest internal errors."""


class FixtureError(OxitestError):
    """Fixture-related errors."""


class ExecutionError(OxitestError):
    """Test execution errors."""


# ─── Fixture errors ─────────────────────────────────────────────────────────


def _default_fixture_not_found_message(name: str, namespace: str) -> str:
    """Build the default error message for a missing fixture lookup by name."""
    if namespace:
        return (
            f"fixture '{name}' not found in namespace '{namespace}'.\n"
            f"  Hint: check that a Fixtures() instance in conftest.py "
            f"defines a fixture named '{name}', or verify the spelling."
        )
    return (
        f"fixture '{name}' not found.\n"
        f"  Hint: ensure the fixture is defined in a Fixtures() "
        f"instance in conftest.py or provided by a plugin, and "
        f"annotated with Fixture[<type>] in the test signature."
    )


class FixtureNotFoundError(FixtureError):
    """Raised when a requested fixture name cannot be found in the registry."""

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
    FixtureProvider, conftest return annotation) instead of the by-name hint.
    """

    def __init__(self, type_name: str) -> None:
        msg = (
            f"no fixture registered for type '{type_name}' — must be a "
            f"BuiltinFixture, a plugin-provided FixtureProvider with matching "
            f"fixture_type, or a conftest fixture with '{type_name}' as its "
            f"return annotation."
        )
        super().__init__(type_name, message=msg)


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

    Shared and session-scope fixtures are exposed to tests as immutable proxies
    so that per-test mutations cannot leak into sibling tests. Any write attempt
    (attribute assignment, item assignment, or the setter half of an augmented
    assign like ``x.attr += y``) on such a proxy fires this error.

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


class LoadError(OxitestError):
    """Raised when a module cannot be loaded or a function cannot be resolved."""

    def __init__(self, result: TestResult) -> None:
        self.result = result


class CollectionError(OxitestError):
    """Raised when a test function is misconfigured at collection time.

    Examples include @oxi.arrange specifying a fixture that is also declared
    as a parameter — a redundant declaration that risks double-instantiation.
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


def _relpath(path: str) -> str:
    """Format a source-code path for diagnostic messages.

    Returns the CWD-relative path when possible, falling back to the absolute
    path on cross-drive paths (Windows raises ValueError there). Intended as
    the shared formatter for `__code__.co_filename` in any error class in
    this module — matches the `Defined at:` / `Arranged at:` convention used
    across fixture diagnostics.
    """
    try:
        return os.path.relpath(path)
    except ValueError:
        return path


class AutouseRegistrationError(TypeError):
    """Raised at decorator time on an illegal async-each autouse registration.

    Triggered when @Fixtures.fixture registers a factory with the illegal
    combination `autouse=True` + `shared=False` + async factory. This has no
    legal semantics: it would only fire on async tests, silently skipping
    sync tests. oxitest is strict — refuse the combination at registration
    so the intent is stated up front.

    See #1535 (Q5) and #1538.

    Examples:
        Registering an async function-scope autouse fixture raises this
        at decorator time. The error message lists the two supported
        ways forward (drop ``autouse=True``, or pass ``shared=True``):

        >>> from oxitest import Fixtures, AutouseRegistrationError, raises
        >>> fx = Fixtures()
        >>> with raises(AutouseRegistrationError):
        ...     @fx.fixture(autouse=True, shared=False)
        ...     async def bad() -> int:
        ...         return 1

    """

    def __init__(self, func: Any) -> None:
        code = func.__code__
        defined_at = f"{_relpath(code.co_filename)}:{code.co_firstlineno}"
        message = (
            f"cannot register async fixture {func.__name__!r} as "
            f"function-scope autouse.\n"
            f"  Defined at:  {defined_at}\n"
            f"  Scope:       each  (autouse=True)\n"
            f"  Why:         a function-scope async autouse would only fire on\n"
            f"               async tests; silently skipping sync tests hides the\n"
            f"               mismatch. oxitest is strict: refuse the combination\n"
            f"               at registration so the intent is stated up front.\n"
            f"  Two ways forward:\n"
            f"    1. Drop autouse=True and use @arrange({func.__name__!r}) on\n"
            f"       the async tests that need it.\n"
            f"    2. Pass shared=True — a shared-scope async autouse\n"
            f"       applies to both sync and async tests.\n"
        )
        super().__init__(message)


# ─── Arrange errors ──────────────────────────────────────────────────────────


class ArrangeError(OxitestError):
    """Raised when @oxi.arrange is used with an incompatible fixture.

    Fires when a sync test arranges one or more async function-scope
    fixtures — the illegal cell in the (test kind x fixture kind) matrix.
    Async tests legally consume async-each fixtures; sync fixtures compose
    freely on either test kind.

    Other @arrange failure modes surface through the existing error
    hierarchy: missing arranged fixtures via `FixtureNotFoundError`
    (caught upstream by the Rust `FixtureValidationPhase`), factory
    raises via `FixtureSetupError`.

    Examples:
        Raised during test collection (not at decorator time), so the
        organic trigger requires a running collector. The error message
        lists three remediations (make the test async, widen the fixture
        scope, or convert the fixture to sync).

        Direct catch pattern:

        >>> from oxitest import ArrangeError, raises
        >>> def _fake_test(): pass
        >>> with raises(ArrangeError):
        ...     raise ArrangeError(_fake_test, [])

    """

    def __init__(self, fn: Any, illegal: list[tuple[str, Any]]) -> None:
        code = fn.__code__
        arranged_at = f"{_relpath(code.co_filename)}:{code.co_firstlineno}"
        count_word = (
            "1 illegal entry"
            if len(illegal) == 1
            else f"{len(illegal)} illegal entries"
        )
        illegal_lines = "\n".join(
            f"    - {name!r} (function scope) — defined at "
            f"{_relpath(defn.source.func.__code__.co_filename)}:"
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
            f"    2. Change fixture scope to 'shared' or 'session'\n"
            f"    3. Convert fixture to sync — remove `async` from def\n"
        )
        super().__init__(message)
