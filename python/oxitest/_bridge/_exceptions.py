from __future__ import annotations


class FixtureNotFoundError(Exception):
    def __init__(self, name: str, *, namespace: str = "") -> None:
        if namespace:
            super().__init__(f"fixture '{name}' not found in namespace '{namespace}'")
        else:
            super().__init__(f"fixture '{name}' not found")
        self.fixture_name = name
        self.namespace = namespace


class FixtureCycleError(Exception):
    def __init__(self, name: str, chain: set[str]) -> None:
        path = " → ".join(sorted(chain)) + f" → {name}"
        super().__init__(f"fixture cycle detected: {path}")


class FixtureSetupError(Exception):
    def __init__(self, name: str, cause: Exception) -> None:
        super().__init__(f"Error in fixture '{name}': {cause}")
        self.fixture_name = name


class UnannotatedFixtureParamError(Exception):
    def __init__(self, param_name: str, fn_name: str) -> None:
        super().__init__(
            f"parameter '{param_name}' in {fn_name} is not injected.\n"
            f"To request a fixture, annotate it: {param_name}: Fixture[<type>]\n"
            f"Unannotated parameters are not resolved by oxitest."
        )
        self.param_name = param_name
        self.fn_name = fn_name


class SharedFixtureMutationError(RuntimeError):
    """Raised when code attempts to mutate a shared (immutable) fixture value."""
