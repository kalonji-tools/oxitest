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
        _adopt(obj, mod.__name__)
    setattr(mod, name, obj)


def _adopt(cls: type, home: str, seen: set[int] | None = None) -> None:
    """Give *cls* and every class nested in it *home* as their ``__module__``.

    Nested too, because a class defined inside another in a real module reports
    that module, not the outer class. Adopting only the outer one models a shape
    Python never produces, and the registrar would then skip the inner class.
    """
    seen = set() if seen is None else seen
    if id(cls) in seen:
        return
    seen.add(id(cls))
    cls.__module__ = home
    for nested in vars(cls).values():
        if isinstance(nested, type):
            _adopt(nested, home, seen)


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


# --------------------------------------------------------------------------
# #2066 — a definition that is both test_-named and a fixture
# --------------------------------------------------------------------------


def test_a_test_named_inline_fixture_is_refused() -> None:
    """Two walks read one definition, and neither refused it before #2066."""
    # Arrange
    registry = FixtureRegistry()
    mod = _module("/t/pkg/test_overlap.py")

    @fixture(lifetime="function")
    def test_both() -> int:
        return 1

    _attach(mod, "test_both", test_both)

    # Act / Assert
    with raises(UsageError) as caught:
        register_module_source_fixtures(
            registry, mod, anchor_package_path="/t/pkg/test_overlap.py"
        )

    assert "test_both" in str(caught.value), (
        "the refusal must name the definition that is read two ways"
    )


def test_a_test_named_declaration_home_fixture_is_not_refused() -> None:
    """A __fixtures__.py is not scanned for tests, so there is no overlap."""
    # Arrange
    registry = FixtureRegistry()
    mod = _module("/t/pkg/__fixtures__.py")

    @fixture(lifetime="function")
    def test_both() -> int:
        return 1

    _attach(mod, "test_both", test_both)

    # Act — must not raise
    register_module_source_fixtures(registry, mod, anchor_package_path="/t/pkg")

    # Assert
    assert registry.get_in_namespace("test_both", "pkg") is not None, (
        "the default python_files is test_*.py, so a __fixtures__.py holds no "
        "tests and a test_-named fixture there is read one way, not two"
    )


def test_a_non_test_named_inline_fixture_still_registers() -> None:
    """The refusal keys on the name, and must not disarm the inline route."""
    # Arrange
    registry = FixtureRegistry()
    mod = _module("/t/pkg/test_overlap.py")

    @fixture(lifetime="function")
    def conn() -> int:
        return 1

    _attach(mod, "conn", conn)

    # Act
    register_module_source_fixtures(
        registry, mod, anchor_package_path="/t/pkg/test_overlap.py"
    )

    # Assert
    assert registry.get_in_namespace("conn", "test_overlap") is not None, (
        "an ordinary inline declaration is the feature #1712 shipped and must "
        "survive a refusal aimed only at the test_ prefix"
    )


# --------------------------------------------------------------------------
# End-to-end: one defect, one name
#
# #2067 enforces "a test function returns None" at three points, two of which
# run at collection, upstream of Python registration. Both would otherwise
# report the #2066 overlap under a name that describes a different defect.
# These pin the deferral, and the two controls pin that it disarms nothing.
# --------------------------------------------------------------------------


def _run_cold(project: Path) -> tuple[str, str, int]:
    """Run *project* with no item cache, and return ``(stdout, stderr, rc)``.

    The item cache serves a file's collected items **without importing it**, and
    both refusals here happen at import. A cache left by an earlier run hides
    them completely — which is why #2068 also bumps ``CACHE_VERSION``.
    """
    shutil.rmtree(project / ".oxitest_cache", ignore_errors=True)
    return helpers.run_oxitest(project, "--warnings")


def test_the_yield_overlap_reports_the_overlap_not_the_generator() -> None:
    """#2067's collection guard defers, so the overlap owns this definition."""
    # Act
    stdout, stderr, rc = _run_cold(_OVERLAP_YIELD)
    output = stdout + stderr

    # Assert
    assert rc == 3, (
        f"the overlap is refused at registration, which exits 3\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "is a test by name and a fixture by decorator" in output, (
        f"the overlap owns this definition; the generator message names a "
        f"different defect, and its hint tells the user to move the generator "
        f"into a fixture, which is what they already did\n{output}"
    )
    assert "contains yield, so calling it returns a generator" not in output, (
        f"naming one defect twice under two names is what test_returns.rs and "
        f"prescan.rs both argue against\n{output}"
    )


def test_the_strict_return_overlap_reports_the_overlap() -> None:
    """Under strict the run aborts before import, so the check must defer."""
    # Act
    stdout, stderr, rc = _run_cold(_OVERLAP_RETURN_STRICT)
    output = stdout + stderr

    # Assert
    assert rc == 3, (
        f"the overlap is refused at registration, which exits 3\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "test-returns-value" not in output, (
        f"a strict run aborts before any module is imported, so while this "
        f"violation stands the registrar refusal is unreachable and the user "
        f"is told the wrong thing\n{output}"
    )
    assert "is a test by name and a fixture by decorator" in output, (
        f"the overlap must be what the user is told, in every strict setting\n{output}"
    )


def test_a_class_method_fixture_is_refused_end_to_end() -> None:
    """The registrar refusal reaches a real run, not only a direct call."""
    # Act
    stdout, stderr, rc = _run_cold(_METHOD_FIXTURE)
    output = stdout + stderr

    # Assert
    assert rc == 3, (
        f"a method fixture is refused at registration, which exits 3\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "is decorated @oxi.fixture on a method of class TestThing" in output, (
        f"the refusal must name the real cause\n{output}"
    )
    assert "fixture 'conn' not found" not in output.replace(
        "\"fixture 'conn' not found\"", ""
    ), (
        f"the misattributed message is the defect; it must not survive except "
        f"as the quotation inside the new message\n{output}"
    )


def test_a_plain_generator_test_is_still_refused() -> None:
    """CONTROL — the deferral must not disarm the guard it defers to."""
    # Act
    stdout, stderr, rc = _run_cold(_GEN_SYNC)
    output = stdout + stderr

    # Assert
    assert rc == 3, (
        f"#2067's collection guard owns every generator test that is not also "
        f"a declaration; a deferral that swallowed those would undo it\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "contains yield, so calling it returns a generator" in output, (
        f"the generator message must still be what a plain generator test "
        f"gets\n{output}"
    )


def test_a_plain_test_returning_a_value_still_violates_strict() -> None:
    """CONTROL — the strict check must still fire where no fixture is declared."""
    # Act
    stdout, stderr, rc = _run_cold(_RETURN_STRICT)
    output = stdout + stderr

    # Assert
    assert rc == 3, (
        f"strict = 'abort' turns the violation into a refusal, so a passing "
        f"run here would mean the check never fired\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "test-returns-value" in output, (
        f"#2067's check owns every test that is not also a declaration, and a "
        f"deferral that swallowed those would silently undo that issue\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
