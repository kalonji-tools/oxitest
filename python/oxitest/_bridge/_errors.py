"""Consolidated error hierarchy for oxitest internals."""

from __future__ import annotations

__all__ = [
    "OxitestError",
    "FixtureError",
    "ExecutionError",
    "FixtureNotFoundError",
    "FixtureCycleError",
    "FixtureSetupError",
    "UnannotatedFixtureParamError",
    "SharedFixtureMutationError",
    "OxitestTimeoutError",
    "BackendNotFoundError",
    "ConflictingBackendError",
    "ParametrizeError",
    "LoadError",
]

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oxitest._bridge.result import TestResult


class OxitestError(Exception):  # pragma: no cover
    """Base for all oxitest internal errors."""


class FixtureError(OxitestError):  # pragma: no cover
    """Fixture-related errors."""


class ExecutionError(OxitestError):  # pragma: no cover
    """Test execution errors."""


# ─── Fixture errors ─────────────────────────────────────────────────────────


class FixtureNotFoundError(FixtureError):
    def __init__(self, name: str, *, namespace: str = "") -> None:
        if namespace:
            super().__init__(f"fixture '{name}' not found in namespace '{namespace}'")
        else:
            super().__init__(f"fixture '{name}' not found")
        self.fixture_name = name
        self.namespace = namespace


class FixtureCycleError(FixtureError):
    def __init__(self, name: str, chain: set[str]) -> None:
        path = " → ".join(sorted(chain)) + f" → {name}"
        super().__init__(f"fixture cycle detected: {path}")


class FixtureSetupError(FixtureError):
    def __init__(self, name: str, cause: Exception) -> None:
        super().__init__(f"Error in fixture '{name}': {cause}")
        self.fixture_name = name


class UnannotatedFixtureParamError(FixtureError):
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
            f"async backend '{name}' not found \u2014 is the plugin installed?"
        )
        self.backend_name = name


class ConflictingBackendError(OxitestError):
    """Raised when multiple plugins provide the same backend name."""

    def __init__(self, name: str, providers: list[str]) -> None:
        joined = ", ".join(providers)
        super().__init__(f"multiple plugins provide async backend '{name}': {joined}")
        self.backend_name = name
        self.providers = providers


# ─── Parametrize / loading errors ─────────────────────────────────────────────


class ParametrizeError(OxitestError):
    """Raised when parametrize case resolution fails due to misconfiguration."""


class LoadError(OxitestError):
    """Raised when a module cannot be loaded or a function cannot be resolved."""

    def __init__(self, result: TestResult) -> None:
        self.result = result
