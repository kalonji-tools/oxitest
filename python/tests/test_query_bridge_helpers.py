"""Tests for helper_entries() in query_bridge."""

from __future__ import annotations


def test_helper_entries_returns_dicts() -> None:
    from oxitest._bridge._fixture_registry import ConftestSource
    from oxitest._bridge._helper_registry import HelperDef, HelperRegistry
    from oxitest._bridge.query_bridge import helper_entries

    def _greet(name: str) -> str:
        """Say hello."""
        return f"hi {name}"

    reg = HelperRegistry()
    reg.register(
        HelperDef(
            name="greet",
            func=_greet,
            source=ConftestSource(func=_greet, conftest_path="/conftest.py"),
            namespace="utils",
        )
    )
    entries = helper_entries(reg)
    assert len(entries) == 1, "should produce one entry"
    e = entries[0]
    assert e["name"] == "greet", "name field"
    assert e["source"] == "/conftest.py", "source field"
    assert e["namespace"] == "utils", "namespace field"
    assert e["docstring"] == "Say hello.", "docstring from __doc__"
    assert "name: str" in e["signature"], "signature should include params"
