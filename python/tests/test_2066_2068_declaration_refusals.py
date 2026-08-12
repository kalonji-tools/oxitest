"""#2066 and #2068: two declarations that are read wrongly and reported wrongly.

#2066 — a module-level definition that is both ``test_``-named and
``@oxi.fixture``-decorated is read two ways: once as a test, once as a fixture.
#2068 — a ``@oxi.fixture`` on a class method is read no ways: it registers
nothing, and every consumer reports ``fixture '<name>' not found``.

Both are refused at registration rather than in the prescan, because
registration reads a marker attribute and so sees every import spelling while
``prescan.rs:905`` recognises three.
"""

from __future__ import annotations

import shutil
import types
from pathlib import Path

from oxitest import raises
from oxitest._bridge._errors import UsageError
from oxitest._bridge._fixture_decorator import fixture
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._module_source_registrar import (
    register_module_source_fixtures,
)
from tests import helpers

_DATA_ROOT = Path(__file__).parent / "data"
_OVERLAP_YIELD = _DATA_ROOT / "overlap_yield"
_OVERLAP_RETURN_STRICT = _DATA_ROOT / "overlap_return_strict"
_METHOD_FIXTURE = _DATA_ROOT / "method_fixture"
# Controls, owned by #2067 — reused rather than duplicated.
_GEN_SYNC = _DATA_ROOT / "gen_test_sync"
_RETURN_STRICT = _DATA_ROOT / "return_value_strict"


def _module(path: str) -> types.ModuleType:
    """An in-memory module standing in for an imported file."""
    mod = types.ModuleType("probe")
    mod.__file__ = path
    return mod


def _attach(mod: types.ModuleType, name: str, obj: object) -> None:
    """Attach *obj* to *mod* the way an import or a definition would.

    A class defined inside a test function reports the *test* module as its
    ``__module__``. A real module's classes report that module. The registrar
    descends only into classes defined in the module it is registering, so
    without this adoption these doubles would be refused for the wrong reason
    — or not refused at all.
    """
    if isinstance(obj, type):
        obj.__module__ = mod.__name__
    setattr(mod, name, obj)


# --------------------------------------------------------------------------
# #2068 — a fixture on a class method
# --------------------------------------------------------------------------


def test_a_fixture_on_a_test_class_method_is_refused() -> None:
    """A method is not a declaration home, so the decorator must not be silent."""
    # Arrange
    registry = FixtureRegistry()
    mod = _module("/t/pkg/test_cls.py")

    class TestThing:
        @fixture(lifetime="function")
        def conn(self) -> int:
            return 7

    _attach(mod, "TestThing", TestThing)

    # Act / Assert
    with raises(UsageError) as caught:
        register_module_source_fixtures(
            registry, mod, anchor_package_path="/t/pkg/test_cls.py"
        )

    assert "conn" in str(caught.value), (
        "the refusal must name the declaration, or the user cannot find it"
    )
    assert "TestThing" in str(caught.value), (
        "the refusal must name the class — a module can hold several, and the "
        "user needs to know which one to edit"
    )


def test_a_staticmethod_fixture_is_refused() -> None:
    """The marker sits on __func__, so a plain vars(cls) read misses it."""
    # Arrange
    registry = FixtureRegistry()
    mod = _module("/t/pkg/test_cls.py")

    class TestThing:
        @staticmethod
        @fixture(lifetime="function")
        def conn() -> int:
            return 7

    _attach(mod, "TestThing", TestThing)

    # Act / Assert
    with raises(UsageError) as caught:
        register_module_source_fixtures(
            registry, mod, anchor_package_path="/t/pkg/test_cls.py"
        )

    assert "conn" in str(caught.value), (
        "a staticmethod is the spelling someone writes for a fixture taking no "
        "self, so it is the likeliest evasion route and must be refused"
    )


def test_a_classmethod_fixture_is_refused() -> None:
    """Classmethod hides the marker exactly as staticmethod does."""
    # Arrange
    registry = FixtureRegistry()
    mod = _module("/t/pkg/test_cls.py")

    class TestThing:
        @classmethod
        @fixture(lifetime="function")
        def conn(cls) -> int:
            return 7

    _attach(mod, "TestThing", TestThing)

    # Act / Assert
    with raises(UsageError) as caught:
        register_module_source_fixtures(
            registry, mod, anchor_package_path="/t/pkg/test_cls.py"
        )

    assert "conn" in str(caught.value), (
        "classmethod stores the marker on __func__ exactly as staticmethod "
        "does, so one unwrap must cover both"
    )


def test_a_fixture_in_a_nested_class_is_refused() -> None:
    """Nesting must not be an escape hatch — the scan recurses."""
    # Arrange
    registry = FixtureRegistry()
    mod = _module("/t/pkg/test_cls.py")

    class Outer:
        class Inner:
            @fixture(lifetime="function")
            def conn(self) -> int:
                return 7

    _attach(mod, "Outer", Outer)

    # Act / Assert
    with raises(UsageError) as caught:
        register_module_source_fixtures(
            registry, mod, anchor_package_path="/t/pkg/test_cls.py"
        )

    assert "conn" in str(caught.value), (
        "a nested class is still not a declaration home, and stopping the "
        "descent at depth one would leave a silent route"
    )


def test_a_non_test_named_class_is_descended_into() -> None:
    """walk_test_defs filters on Test*; a helper class is just as inert."""
    # Arrange
    registry = FixtureRegistry()
    mod = _module("/t/pkg/test_cls.py")

    class Helper:
        @fixture(lifetime="function")
        def conn(self) -> int:
            return 7

    _attach(mod, "Helper", Helper)

    # Act / Assert
    with raises(UsageError) as caught:
        register_module_source_fixtures(
            registry, mod, anchor_package_path="/t/pkg/test_cls.py"
        )

    assert "conn" in str(caught.value), (
        "a fixture on a helper class registers nothing exactly as one on a "
        "Test* class does, so filtering on the class name would leave a hole"
    )


def test_a_class_method_fixture_is_refused_in_a_declaration_home() -> None:
    """The defect is home-independent, so the refusal is not gated on is_inline."""
    # Arrange
    registry = FixtureRegistry()
    mod = _module("/t/pkg/__fixtures__.py")

    class Holder:
        @fixture(lifetime="function")
        def conn(self) -> int:
            return 7

    _attach(mod, "Holder", Holder)

    # Act / Assert
    with raises(UsageError) as caught:
        register_module_source_fixtures(registry, mod, anchor_package_path="/t/pkg")

    assert "conn" in str(caught.value), (
        "a method fixture registers nothing in a __fixtures__.py either, so "
        "gating this refusal on the home kind would leave that route silent"
    )


def test_a_fixture_defined_inside_a_function_body_is_not_refused() -> None:
    """The boundary of the descent — two such definitions live in this suite."""
    # Arrange
    registry = FixtureRegistry()
    mod = _module("/t/pkg/test_cls.py")

    def outer() -> None:
        @fixture(lifetime="function")
        def conn() -> int:
            return 7

    _attach(mod, "outer", outer)

    # Act — must not raise
    register_module_source_fixtures(
        registry, mod, anchor_package_path="/t/pkg/test_cls.py"
    )

    # Assert
    assert registry.get_in_namespace("conn", "test_cls") is None, (
        "a fixture declared inside a function body is unreachable and is "
        "neither registered nor refused; test_package_lifetime.py and "
        "test_public_fixture_export.py take this shape and must keep passing"
    )


def test_a_module_level_fixture_in_the_same_file_still_registers() -> None:
    """The class descent must not disarm the declaration route beside it."""
    # Arrange
    registry = FixtureRegistry()
    mod = _module("/t/pkg/test_cls.py")

    @fixture(lifetime="function")
    def conn() -> int:
        return 7

    _attach(mod, "conn", conn)

    # Act
    register_module_source_fixtures(
        registry, mod, anchor_package_path="/t/pkg/test_cls.py"
    )

    # Assert
    assert registry.get_in_namespace("conn", "test_cls") is not None, (
        "an inline module-level declaration is the feature #1712 shipped and "
        "must survive a change that only refuses class methods"
    )


def test_an_imported_class_is_not_refused_against_the_importing_module() -> None:
    """vars() holds imported names, and this file did not declare that class."""
    # Arrange — a class whose home is another module, merely bound here
    registry = FixtureRegistry()
    mod = _module("/t/pkg/test_importer.py")

    class Foreign:
        @fixture(lifetime="function")
        def conn(self) -> int:
            return 7

    Foreign.__module__ = "some.other.module"
    setattr(mod, "Foreign", Foreign)  # noqa: B010 — models an import binding

    # Act — must not raise
    register_module_source_fixtures(
        registry, mod, anchor_package_path="/t/pkg/test_importer.py"
    )

    # Assert
    assert registry.get_in_namespace("conn", "test_importer") is None, (
        "the declaration belongs to the module defining the class; naming the "
        "importing file is the misattribution #2068 exists to remove"
    )


def test_a_self_referential_class_does_not_recurse_forever() -> None:
    """A class can hold a reference to itself, and the descent must terminate."""
    # Arrange
    registry = FixtureRegistry()
    mod = _module("/t/pkg/test_loop.py")

    class Loop:
        @fixture(lifetime="function")
        def conn(self) -> int:
            return 7

    setattr(Loop, "me", Loop)  # noqa: B010 — a back-reference to itself
    _attach(mod, "Loop", Loop)

    # Act / Assert — a diagnostic, not a RecursionError
    with raises(UsageError) as caught:
        register_module_source_fixtures(
            registry, mod, anchor_package_path="/t/pkg/test_loop.py"
        )

    assert "conn" in str(caught.value), (
        "the cycle must be walked once and reported once; without a seen set "
        "this reaches the user as RecursionError instead of a diagnostic"
    )
