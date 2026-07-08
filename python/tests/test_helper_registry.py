"""Tests for HelperRegistry — storage, namespace scoping, and most-local resolution."""

from __future__ import annotations

import oxitest
from oxitest._bridge._fixture_registry import ConftestSource
from oxitest._bridge._helper_registry import HelperDef, HelperRegistry


def _dummy() -> str:
    return "hello"


def test_helper_def_stores_fields() -> None:
    """HelperDef stores name, func, source, and namespace on construction."""
    defn = HelperDef(
        name="dummy",
        func=_dummy,
        source=ConftestSource(func=_dummy, conftest_path="/conftest.py"),
        namespace="utils",
    )
    assert defn.name == "dummy", "name should be stored"
    assert defn.func is _dummy, "func should be the original callable"
    assert defn.namespace == "utils", "namespace should be stored"
    assert isinstance(defn.source, ConftestSource), "source should be ConftestSource"


def test_helper_def_is_frozen() -> None:
    """HelperDef is frozen; registered helper definitions cannot be mutated."""
    defn = HelperDef(
        name="dummy",
        func=_dummy,
        source=ConftestSource(func=_dummy, conftest_path="/conftest.py"),
    )
    with oxitest.raises(AttributeError, match="cannot assign to field"):
        setattr(defn, "name", "other")  # noqa: B010 — intentional frozen-dataclass write


def test_registry_register_and_get() -> None:
    """Registering a HelperDef makes it retrievable by name via HelperRegistry.get."""
    reg = HelperRegistry()
    defn = HelperDef(
        name="dummy",
        func=_dummy,
        source=ConftestSource(func=_dummy, conftest_path="/conftest.py"),
        namespace="utils",
    )
    reg.register(defn)
    assert reg.get("dummy") is defn, "get should return the registered def"


def test_registry_all_returns_most_local() -> None:
    """When two helpers share a name, all() returns only the most-local one."""
    reg = HelperRegistry()
    root_defn = HelperDef(
        name="dummy",
        func=_dummy,
        source=ConftestSource(func=_dummy, conftest_path="/root/conftest.py"),
        namespace="root",
    )

    def _leaf_dummy() -> str:
        return "leaf"

    leaf_defn = HelperDef(
        name="dummy",
        func=_leaf_dummy,
        source=ConftestSource(func=_leaf_dummy, conftest_path="/root/sub/conftest.py"),
        namespace="sub",
    )
    reg.register(root_defn)
    reg.register(leaf_defn)
    all_defs = reg.all()
    assert len(all_defs) == 1, "all() returns most-local per name"
    assert all_defs[0] is leaf_defn, "most-local (last registered) should win"


def test_registry_has_namespace() -> None:
    """has_namespace returns True only for namespaces with a registered helper."""
    reg = HelperRegistry()
    defn = HelperDef(
        name="dummy",
        func=_dummy,
        source=ConftestSource(func=_dummy, conftest_path="/conftest.py"),
        namespace="utils",
    )
    reg.register(defn)
    assert reg.has_namespace("utils"), "registered namespace should be found"
    assert not reg.has_namespace("other"), "unregistered namespace should not be found"


def test_registry_get_in_namespace() -> None:
    """get_in_namespace finds a helper only when both name and namespace match."""
    reg = HelperRegistry()
    defn = HelperDef(
        name="dummy",
        func=_dummy,
        source=ConftestSource(func=_dummy, conftest_path="/conftest.py"),
        namespace="utils",
    )
    reg.register(defn)
    assert reg.get_in_namespace("dummy", "utils") is defn, (
        "get_in_namespace should find the helper"
    )
    assert reg.get_in_namespace("dummy", "other") is None, (
        "wrong namespace should return None"
    )


def test_registry_contains_and_iter() -> None:
    """HelperRegistry supports `in` containment checks and iteration over names."""
    reg = HelperRegistry()
    defn = HelperDef(
        name="dummy",
        func=_dummy,
        source=ConftestSource(func=_dummy, conftest_path="/conftest.py"),
    )
    reg.register(defn)
    assert "dummy" in reg, "__contains__ should work"
    assert list(reg) == ["dummy"], "__iter__ should yield names"
