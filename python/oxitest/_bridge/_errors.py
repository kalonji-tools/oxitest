"""Consolidated error hierarchy for oxitest internals."""

from __future__ import annotations

__all__ = [
    "AmbiguousFixtureError",
    "BackendNotFoundError",
    "BroadFixtureTypeError",
    "ConflictingBackendError",
    "ConflictingCoverageError",
    "ConflictingDebuggerError",
    "ExecutionError",
    "FixtureCycleError",
    "FixtureError",
    "FixtureNotFoundError",
    "FixtureSetupError",
    "LoadError",
    "OxitestError",
    "OxitestTimeoutError",
    "ParametrizeError",
    "SharedFixtureMutationError",
    "UnannotatedFixtureParamError",
]

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oxitest._bridge.result import TestResult


class OxitestError(Exception):
    """Base for all oxitest internal errors."""


class FixtureError(OxitestError):
    """Fixture-related errors."""


class ExecutionError(OxitestError):
    """Test execution errors."""


# ─── Fixture errors ─────────────────────────────────────────────────────────


class FixtureNotFoundError(FixtureError):
    """Raised when a requested fixture name cannot be found in the registry."""

    def __init__(self, name: str, *, namespace: str = "") -> None:
        if namespace:
            msg = (
                f"fixture '{name}' not found in namespace '{namespace}'.\n"
                f"  Hint: check that a Fixtures() instance in conftest.py "
                f"defines a fixture named '{name}', or verify the spelling."
            )
        else:
            msg = (
                f"fixture '{name}' not found.\n"
                f"  Hint: ensure the fixture is defined in a Fixtures() "
                f"instance in conftest.py or provided by a plugin, and "
                f"annotated with Fixture[<type>] in the test signature."
            )
        super().__init__(msg)
        self.fixture_name = name
        self.namespace = namespace


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
    """Raised when code attempts to mutate a shared (immutable) fixture value."""


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
