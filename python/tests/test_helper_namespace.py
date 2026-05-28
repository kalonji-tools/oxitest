from __future__ import annotations

import types
from pathlib import Path

import oxitest as oxi
from oxitest._bridge._helper_namespace import HelperNamespace, build_helpers
from oxitest._bridge.fixtures import Fixtures


def _make_module(attrs: dict, name: str = "mod") -> types.ModuleType:
    """Create a module with the given attributes."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# ── HelperNamespace basics ────────────────────────────────────────────────────


def test_empty_namespace_repr():
    ns = HelperNamespace()
    assert repr(ns) == "HelperNamespace()", f"got {repr(ns)!r}"


def test_namespace_with_attrs_repr():
    ns = HelperNamespace()
    setattr(ns, "foo", lambda: None)
    setattr(ns, "bar", lambda: None)
    assert repr(ns) == "HelperNamespace(bar, foo)", f"got {repr(ns)!r}"


# ── build_helpers ─────────────────────────────────────────────────────────────


def test_build_helpers_no_modules_returns_empty():
    ns = build_helpers([])
    assert repr(ns) == "HelperNamespace()", f"got {repr(ns)!r}"


def test_build_helpers_collects_public_functions():
    mod = _make_module({"greet": lambda: "hi", "add": lambda a, b: a + b})
    ns = build_helpers([(mod, Path("/project/tests"))])
    assert ns.tests.greet() == "hi", f"greet() returned {ns.tests.greet()!r}"
    assert ns.tests.add(1, 2) == 3, f"add(1, 2) returned {ns.tests.add(1, 2)!r}"


def test_build_helpers_collects_classes():
    class FakeDB:
        pass

    mod = _make_module({"FakeDB": FakeDB})
    ns = build_helpers([(mod, Path("/project/tests"))])
    assert ns.tests.FakeDB is FakeDB, "FakeDB class should be collected as-is"


def test_build_helpers_excludes_private_names():
    mod = _make_module({"_internal": lambda: None, "public": lambda: None})
    ns = build_helpers([(mod, Path("/project/tests"))])
    assert hasattr(ns.tests, "public"), "public name should be collected"
    assert not hasattr(ns.tests, "_internal"), "_internal should be excluded"


def test_build_helpers_excludes_fixtures_instances():
    fx = Fixtures()
    mod = _make_module({"fx": fx, "helper": lambda: None})
    ns = build_helpers([(mod, Path("/project/tests"))])
    assert hasattr(ns.tests, "helper"), "helper should be collected"
    assert not hasattr(ns.tests, "fx"), "Fixtures instance should be excluded"


def test_build_helpers_excludes_non_callables():
    mod = _make_module({"TIMEOUT": 30, "helper": lambda: None})
    ns = build_helpers([(mod, Path("/project/tests"))])
    assert hasattr(ns.tests, "helper"), "helper should be collected"
    assert not hasattr(ns.tests, "TIMEOUT"), "non-callable TIMEOUT should be excluded"


def test_build_helpers_uses_directory_name_as_namespace():
    mod = _make_module({"fn": lambda: None})
    ns = build_helpers([(mod, Path("/project/integration"))])
    assert hasattr(ns, "integration"), "namespace 'integration' should exist"
    assert ns.integration.fn() is None, "fn() should return None"


def test_build_helpers_respects_namespace_override():
    mod = _make_module(
        {
            "__helpers_namespace__": "integ",
            "fn": lambda: None,
        }
    )
    ns = build_helpers([(mod, Path("/project/integration"))])
    assert hasattr(ns, "integ"), "namespace override 'integ' should be used"
    assert not hasattr(ns, "integration"), "default name 'integration' should not exist"


def test_build_helpers_multiple_scopes():
    root_mod = _make_module({"root_fn": lambda: "root"})
    sub_mod = _make_module({"sub_fn": lambda: "sub"})
    ns = build_helpers(
        [
            (root_mod, Path("/project/tests")),
            (sub_mod, Path("/project/tests/unit")),
        ]
    )
    assert ns.tests.root_fn() == "root", f"root_fn() returned {ns.tests.root_fn()!r}"
    assert ns.unit.sub_fn() == "sub", f"sub_fn() returned {ns.unit.sub_fn()!r}"


def test_build_helpers_duplicate_namespace_raises():
    mod1 = _make_module({"fn1": lambda: None})
    mod2 = _make_module({"fn2": lambda: None})
    with oxi.raises(ValueError, match="same helpers namespace"):
        build_helpers(
            [
                (mod1, Path("/project/tests")),
                (mod2, Path("/project/utils/tests")),
            ]
        )


def test_build_helpers_duplicate_namespace_error_suggests_dunder():
    mod1 = _make_module({"fn1": lambda: None})
    mod2 = _make_module({"fn2": lambda: None})
    with oxi.raises(ValueError, match="__helpers_namespace__"):
        build_helpers(
            [
                (mod1, Path("/project/tests")),
                (mod2, Path("/project/utils/tests")),
            ]
        )


def test_build_helpers_validates_namespace_name():
    mod = _make_module({"fn": lambda: None})
    with oxi.raises(ValueError, match="Python keyword"):
        build_helpers([(mod, Path("/project/for"))])


def test_build_helpers_excludes_dunder_attrs():
    mod = _make_module(
        {
            "__helpers_namespace__": "custom",
            "__builtins__": {},
            "fn": lambda: None,
        }
    )
    ns = build_helpers([(mod, Path("/project/tests"))])
    assert hasattr(ns.custom, "fn"), "fn should be collected under 'custom'"
    assert not hasattr(ns.custom, "__helpers_namespace__"), (
        "__helpers_namespace__ dunder should be excluded"
    )
    assert not hasattr(ns.custom, "__builtins__"), (
        "__builtins__ dunder should be excluded"
    )
