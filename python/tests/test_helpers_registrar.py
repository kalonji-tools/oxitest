"""Tests for the Helpers registrar — @helpers.helper decoration and namespace access."""

from __future__ import annotations

import oxitest
from oxitest import Helpers


def test_helper_decorator_registers() -> None:
    """Decorating a function with @h.helper adds it to the internal def list."""
    h = Helpers()

    @h.helper
    def my_fn() -> str:
        return "hello"

    assert len(h._defs) == 1, "decorator should register one def"
    assert h._defs[0].name == "my_fn", "name should come from function name"
    assert h._defs[0].func is my_fn, "func should be the original function"


def test_helper_decorator_with_name_override() -> None:
    """Passing name= to @h.helper registers the helper under the explicit name."""
    h = Helpers()

    @h.helper(name="custom")
    def my_fn() -> str:
        return "hello"

    assert h._defs[0].name == "custom", "name override should be respected"


def test_helper_decorator_preserves_function() -> None:
    """@h.helper returns the original function unchanged so it is directly callable."""
    h = Helpers()

    @h.helper
    def my_fn() -> str:
        return "hello"

    assert my_fn() == "hello", "decorated function should still be callable"


def test_helpers_getattr_returns_callable() -> None:
    """Accessing a registered helper name via attribute lookup returns its callable."""
    h = Helpers()

    @h.helper
    def greet(name: str) -> str:
        return f"hi {name}"

    assert h.greet("world") == "hi world", "__getattr__ should return the raw callable"


def test_helpers_getattr_unknown_raises() -> None:
    """Accessing an unregistered name on a Helpers instance raises AttributeError."""
    h = Helpers()
    with oxitest.raises(AttributeError, match="no registered helper"):
        _ = h.nonexistent


def test_helpers_namespace_from_init() -> None:
    """Helpers(name=...) stores the explicit namespace name for registry grouping."""
    h = Helpers(name="utils")
    assert h._namespace_name == "utils", "explicit name should be stored"


def test_helpers_namespace_defaults_empty() -> None:
    """Helpers() with no name argument defaults to an empty namespace string."""
    h = Helpers()
    assert h._namespace_name == "", "default namespace should be empty string"


def test_helpers_captures_source_line() -> None:
    """Helpers() records its source line for the allow-comment gate."""
    h = Helpers()
    assert hasattr(h, "_source_line"), "Helpers should capture source line"
    assert isinstance(h._source_line, int), "source line should be an int"
    assert h._source_line > 0, "source line should be positive"
