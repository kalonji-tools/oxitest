from __future__ import annotations

import oxitest
from oxitest._bridge._helpers import Helpers


def test_helper_decorator_registers() -> None:
    h = Helpers()

    @h.helper
    def my_fn() -> str:
        """A helper."""
        return "hello"

    assert len(h._defs) == 1, "decorator should register one def"
    assert h._defs[0].name == "my_fn", "name should come from function name"
    assert h._defs[0].func is my_fn, "func should be the original function"


def test_helper_decorator_with_name_override() -> None:
    h = Helpers()

    @h.helper(name="custom")
    def my_fn() -> str:
        return "hello"

    assert h._defs[0].name == "custom", "name override should be respected"


def test_helper_decorator_preserves_function() -> None:
    h = Helpers()

    @h.helper
    def my_fn() -> str:
        return "hello"

    assert my_fn() == "hello", "decorated function should still be callable"


def test_helpers_getattr_returns_callable() -> None:
    h = Helpers()

    @h.helper
    def greet(name: str) -> str:
        return f"hi {name}"

    assert h.greet("world") == "hi world", "__getattr__ should return the raw callable"


def test_helpers_getattr_unknown_raises() -> None:
    h = Helpers()
    with oxitest.raises(AttributeError, match="no registered helper"):
        h.nonexistent


def test_helpers_namespace_from_init() -> None:
    h = Helpers(name="utils")
    assert h._namespace_name == "utils", "explicit name should be stored"


def test_helpers_namespace_defaults_empty() -> None:
    h = Helpers()
    assert h._namespace_name == "", "default namespace should be empty string"
