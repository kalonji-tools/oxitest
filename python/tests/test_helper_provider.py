from __future__ import annotations

from collections.abc import Callable
from typing import Any

from oxitest._bridge.plugin_loader import PluginRegistry
from oxitest.plugin import HelperProvider


def _screenshot(selector: str) -> str:
    return f"screenshot:{selector}"


class ConformingProvider:
    @property
    def name(self) -> str:
        return "take_screenshot"

    @property
    def helper(self) -> Callable[..., Any]:
        return _screenshot


def test_helper_provider_isinstance_check() -> None:
    provider = ConformingProvider()
    assert isinstance(provider, HelperProvider), (
        "class with name + helper properties should satisfy the protocol"
    )


def test_helper_provider_name() -> None:
    provider = ConformingProvider()
    assert provider.name == "take_screenshot", "name should be accessible"


def test_helper_provider_callable() -> None:
    provider = ConformingProvider()
    result = provider.helper("#login")
    assert result == "screenshot:#login", "helper should return the callable"


def test_plugin_registry_helper_providers_empty() -> None:
    reg = PluginRegistry()
    assert reg.helper_providers == (), "empty registry should return empty tuple"


def test_plugin_helpers_registered_in_session() -> None:
    """FixtureSession._register_plugin_helpers populates a HelperRegistry."""
    from oxitest._bridge._fixture_session import FixtureSession
    from oxitest._bridge._helper_registry import HelperRegistry
    from oxitest._bridge.plugin_loader import PluginEntry, PluginRegistry
    from oxitest.plugin import Plugin

    plugin = Plugin(helper_providers=(ConformingProvider(),))
    entry = PluginEntry(module_name="my_plugin", plugin=plugin)
    plugin_reg = PluginRegistry(entries=[entry])

    session = FixtureSession([], plugin_registry=plugin_reg)
    helper_reg = HelperRegistry()
    session._register_plugin_helpers(helper_reg)

    defs = helper_reg.all()
    assert len(defs) == 1, (
        "plugin helper provider should contribute exactly one HelperDef"
    )
    assert defs[0].name == "take_screenshot", (
        "registered helper name should match the provider's name property"
    )
