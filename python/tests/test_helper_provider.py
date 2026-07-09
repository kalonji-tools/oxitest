"""Tests for the HelperProvider protocol and PluginRegistry helper_providers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._helper_registry import HelperRegistry
from oxitest._bridge.plugin_loader import PluginEntry, PluginRegistry
from oxitest.plugin import HelperProvider, Plugin


def _screenshot(selector: str) -> str:
    return f"screenshot:{selector}"


class ConformingProvider:
    """Test double that conforms to the HelperProvider structural protocol."""

    @property
    def name(self) -> str:
        """Return the helper name for registry lookup."""
        return "take_screenshot"

    @property
    def helper(self) -> Callable[..., Any]:
        """Return the callable that implements this helper."""
        return _screenshot


def test_helper_provider_isinstance_check() -> None:
    """A class with name and helper properties should satisfy HelperProvider."""
    provider = ConformingProvider()
    assert isinstance(provider, HelperProvider), (
        "class with name + helper properties should satisfy the protocol"
    )


def test_helper_provider_name() -> None:
    """Name property should return the registered helper name."""
    provider = ConformingProvider()
    assert provider.name == "take_screenshot", "name should be accessible"


def test_helper_provider_callable() -> None:
    """Helper property should return a callable that invokes the underlying function."""
    provider = ConformingProvider()
    result = provider.helper("#login")
    assert result == "screenshot:#login", "helper should return the callable"


def test_plugin_registry_helper_providers_empty() -> None:
    """An empty PluginRegistry should expose an empty helper_providers tuple."""
    reg = PluginRegistry()
    assert reg.helper_providers == (), "empty registry should return empty tuple"


def test_plugin_helpers_registered_in_session() -> None:
    """set_helper_registry populates a HelperRegistry with plugin helpers."""
    plugin = Plugin(helper_providers=(ConformingProvider(),))
    entry = PluginEntry(module_name="my_plugin", plugin=plugin)
    plugin_reg = PluginRegistry(entries=[entry])

    session = FixtureSession([], plugin_registry=plugin_reg)
    helper_reg = HelperRegistry()
    session.set_helper_registry(helper_reg)

    defs = helper_reg.all()
    assert len(defs) == 1, (
        "plugin helper provider should contribute exactly one HelperDef"
    )
    assert defs[0].name == "take_screenshot", (
        "registered helper name should match the provider's name property"
    )
