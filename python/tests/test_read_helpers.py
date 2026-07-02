from __future__ import annotations

from collections.abc import Callable

import oxitest
from oxitest import Fixture
from oxitest._bridge._fixture_registry import ConftestSource
from oxitest._bridge._helper_registry import HelperDef, HelperRegistry
from oxitest._bridge._read_helpers import _HelpersProxy


def _greet(name: str) -> str:
    return f"hi {name}"


def test_proxy_resolves_namespace_and_callable(
    helpers_registry: Fixture[Callable],
) -> None:
    reg = HelperRegistry()
    reg.register(
        HelperDef(
            name="greet",
            func=_greet,
            source=ConftestSource(func=_greet, conftest_path="/conftest.py"),
            namespace="utils",
        )
    )
    helpers_registry(reg)
    proxy = _HelpersProxy()
    assert proxy.utils.greet("world") == "hi world", (
        "should resolve namespace then callable"
    )


def test_proxy_raises_outside_session(
    helpers_registry: Fixture[Callable],
) -> None:
    helpers_registry(None)
    proxy = _HelpersProxy()
    with oxitest.raises(AttributeError, match="only available during a test session"):
        proxy.utils


def test_proxy_raises_unknown_namespace(
    helpers_registry: Fixture[Callable],
) -> None:
    reg = HelperRegistry()
    helpers_registry(reg)
    proxy = _HelpersProxy()
    with oxitest.raises(AttributeError, match="no helper namespace"):
        proxy.nonexistent
