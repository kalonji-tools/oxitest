"""Tests for _HelpersProxy — namespace-aware helper resolution from ContextVar."""

from __future__ import annotations

import oxitest
from oxitest._bridge._fixture_registry import ConftestSource
from oxitest._bridge._helper_registry import HelperDef, HelperRegistry
from oxitest._bridge._read_helpers import _helpers_registry_var, _HelpersProxy


def _greet(name: str) -> str:
    return f"hi {name}"


def test_proxy_resolves_namespace_and_callable() -> None:
    """Proxy should chain namespace access and callable invocation correctly."""
    reg = HelperRegistry()
    reg.register(
        HelperDef(
            name="greet",
            func=_greet,
            source=ConftestSource(func=_greet, conftest_path="/conftest.py"),
            namespace="utils",
        )
    )
    token = _helpers_registry_var.set(reg)
    try:
        proxy = _HelpersProxy()
        assert proxy.utils.greet("world") == "hi world", (
            "should resolve namespace then callable"
        )
    finally:
        _helpers_registry_var.reset(token)


def test_proxy_raises_outside_session() -> None:
    """Accessing a proxy namespace outside a session should raise AttributeError."""
    token = _helpers_registry_var.set(None)
    try:
        proxy = _HelpersProxy()
        with oxitest.raises(
            AttributeError, match="only available during a test session"
        ):
            proxy.utils
    finally:
        _helpers_registry_var.reset(token)


def test_proxy_raises_unknown_namespace() -> None:
    """Accessing a namespace with no registered helpers should raise AttributeError."""
    reg = HelperRegistry()
    token = _helpers_registry_var.set(reg)
    try:
        proxy = _HelpersProxy()
        with oxitest.raises(AttributeError, match="no helper namespace"):
            proxy.nonexistent
    finally:
        _helpers_registry_var.reset(token)
