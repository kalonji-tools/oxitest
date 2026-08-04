"""Tests for module-source fixture registrar.

Task 6 of the fixture-redesign slice-1 plan. Scans a module for
@oxi.fixture-decorated functions, derives namespace from anchor-package
segment name, and registers each into FixtureRegistry as a
ModuleSource-backed FixtureDef. Enforces loud collision detection.
"""

from __future__ import annotations

import types
from pathlib import Path

from oxitest import TempDir, raises
from oxitest._bridge._errors import UsageError
from oxitest._bridge._fixture_decorator import fixture
from oxitest._bridge._fixture_registry import (
    ConftestSource,
    FixtureDef,
    FixtureRegistry,
    FixtureScope,
    ModuleSource,
)
from oxitest._bridge._lifetime import Lifetime
from oxitest._bridge._module_source_registrar import (
    register_module_source_fixtures,
)


def _make_fake_fixture_module(tmp: TempDir) -> types.ModuleType:
    """Fabricate an in-memory module with one @oxi.fixture-decorated func."""
    mod = types.ModuleType("slice1_pkg.__fixtures__")
    mod.__file__ = str(tmp / "slice1_pkg" / "__fixtures__.py")

    @fixture(lifetime="function")
    def conn() -> object:
        return object()

    setattr(mod, "conn", conn)  # noqa: B010 — dynamic module attr
    return mod


def test_registers_decorated_functions(tmp: TempDir) -> None:
    """Decorated functions land in the registry with correct source and scope."""
    registry = FixtureRegistry()
    (tmp / "slice1_pkg").mkdir()
    mod = _make_fake_fixture_module(tmp)

    register_module_source_fixtures(
        registry, mod, anchor_package_path=str(tmp / "slice1_pkg")
    )

    defn = registry.get_in_namespace("conn", "slice1_pkg")
    assert defn is not None, (
        "fixture must land in the registry after register_module_source_fixtures"
    )
    assert isinstance(defn.source, ModuleSource), (
        "registered fixture must have ModuleSource, not ConftestSource"
    )
    assert defn.source.lifetime is Lifetime.FUNCTION, (
        "ModuleSource must retain the lifetime tier passed to the decorator"
    )
    assert defn.scope is FixtureScope.EACH, (
        "Lifetime.FUNCTION must map to FixtureScope.EACH so existing per-test "
        "cache/teardown semantics apply"
    )


def test_declared_lifetime_reaches_fixture_def_scope(tmp: TempDir) -> None:
    """A module-lifetime declaration must survive into FixtureDef.scope.

    Slice 1 hardcoded ``scope=FixtureScope.EACH`` here. The tier survived on
    ``ModuleSource.lifetime`` but the caching machinery reads ``FixtureDef.scope``,
    so every declaration was silently function-scoped. That was invisible while
    ``"function"`` was the only legal value — this test is the tripwire that
    keeps the mapping wired as slices 3 and 4 add tiers.
    """
    registry = FixtureRegistry()
    (tmp / "slice2_pkg").mkdir()

    mod = types.ModuleType("slice2_pkg.__fixtures__")
    mod.__file__ = str(tmp / "slice2_pkg" / "__fixtures__.py")

    @fixture(lifetime="module")
    def conn() -> object:
        return object()

    setattr(mod, "conn", conn)  # noqa: B010 — dynamic module attr

    register_module_source_fixtures(
        registry, mod, anchor_package_path=str(tmp / "slice2_pkg")
    )

    defn = registry.get_in_namespace("conn", "slice2_pkg")
    assert defn is not None, (
        "fixture must land in the registry before its scope can be checked"
    )
    assert isinstance(defn.source, ModuleSource), (
        "registrar must produce a ModuleSource — the lifetime assertion below "
        "only exists on that variant"
    )
    assert defn.source.lifetime is Lifetime.MODULE, (
        "ModuleSource must retain the declared tier"
    )
    assert defn.scope is FixtureScope.MODULE, (
        "Lifetime.MODULE must map to FixtureScope.MODULE — the caching machinery "
        "reads scope, so a hardcoded EACH here makes every module-lifetime "
        "fixture silently rebuild per test"
    )


def test_skips_undecorated_functions(tmp: TempDir) -> None:
    """Functions without the @oxi.fixture marker must not be registered."""
    registry = FixtureRegistry()
    (tmp / "slice1_pkg").mkdir()

    mod = types.ModuleType("slice1_pkg.__fixtures__")
    mod.__file__ = str(tmp / "slice1_pkg" / "__fixtures__.py")

    def not_a_fixture() -> object:
        return object()

    setattr(mod, "not_a_fixture", not_a_fixture)  # noqa: B010 — dynamic module attr

    register_module_source_fixtures(
        registry, mod, anchor_package_path=str(tmp / "slice1_pkg")
    )

    assert registry.get_in_namespace("not_a_fixture", "slice1_pkg") is None, (
        "functions without the __oxitest_fixture__ marker must be ignored"
    )


def test_collision_with_conftest_source_is_loud(tmp: TempDir) -> None:
    """Same (namespace, name) in conftest and module raises UsageError."""

    def _existing_conn() -> object:
        return object()

    registry = FixtureRegistry()
    registry.register(
        FixtureDef(
            name="conn",
            fixture_type=object,
            scope=FixtureScope.EACH,
            source=ConftestSource(
                func=_existing_conn,
                conftest_path=str(tmp / "slice1_pkg" / "conftest.py"),
            ),
            namespace="slice1_pkg",
        )
    )
    (tmp / "slice1_pkg").mkdir()
    mod = _make_fake_fixture_module(tmp)

    with raises(UsageError, match="declared twice"):
        register_module_source_fixtures(
            registry, mod, anchor_package_path=str(tmp / "slice1_pkg")
        )


def _fixtures_module(path: str) -> types.ModuleType:
    """A synthetic ``__fixtures__.py`` declaring one fixture named ``conn``."""
    mod = types.ModuleType("synthetic_fixtures")
    mod.__file__ = path

    @fixture(lifetime="function")
    def conn() -> str:
        return "conn"

    setattr(mod, "conn", conn)  # noqa: B010 — dynamic module attr
    return mod


def test_a_symlinked_file_still_registers_as_an_inline_declaration(
    tmp: TempDir,
) -> None:
    """``anchor == defining`` must survive the two paths' different provenances.

    The anchor is canonicalised by ``collector.rs``; ``__file__`` is left as the
    import machinery found it, symlinks and all. ``_visibility.is_visible``
    reads the equality of those two strings as "this is an inline declaration"
    and is forbidden from touching the filesystem to check, so the registrar has
    to reconcile them first.
    """
    # Arrange — the same file reachable by a real path and through a symlink
    root = Path(str(tmp)).resolve()
    real_dir = root / "real"
    real_dir.mkdir()
    inline_module = real_dir / "test_orders.py"
    inline_module.write_text("", encoding="utf-8")
    (root / "link").symlink_to(real_dir, target_is_directory=True)
    registry = FixtureRegistry()
    module = _fixtures_module(str(root / "link" / "test_orders.py"))

    # Act — anchor is the canonical spelling of the very file __file__ names
    register_module_source_fixtures(
        registry, module, anchor_package_path=str(inline_module)
    )

    # Assert
    defn = registry.get_in_namespace("conn", "test_orders")
    assert defn is not None, (
        "an inline declaration's namespace is its module stem, so a missing "
        "'test_orders.conn' means the anchor was read as a directory"
    )
    assert isinstance(defn.source, ModuleSource), (
        "the registrar must produce a ModuleSource — defining_module_path only "
        "exists on that variant"
    )
    assert defn.anchor == defn.source.defining_module_path, (
        "the inline-versus-package discriminator is string equality between an "
        "anchor the Rust collector canonicalised and a __file__ Python did not; "
        "leave __file__ unresolved and a symlinked checkout turns every inline "
        "declaration into a package declaration whose 'directory' is a .py file"
    )


def test_disjoint_subtrees_may_share_a_namespace_and_name() -> None:
    """Two packages both named `v1` are not a duplicate declaration."""
    # Arrange
    registry = FixtureRegistry()
    register_module_source_fixtures(
        registry,
        _fixtures_module("/t/api/v1/__fixtures__.py"),
        anchor_package_path="/t/api/v1",
    )

    # Act — must not raise
    register_module_source_fixtures(
        registry,
        _fixtures_module("/t/admin/v1/__fixtures__.py"),
        anchor_package_path="/t/admin/v1",
    )

    # Assert
    assert len(registry.defs_in_namespace("conn", "v1")) == 2, (
        "both declarations must survive registration — an anchor-blind check "
        "aborts the run with \"fixture 'v1.conn' declared twice\" for two "
        "packages whose tests can never see each other's fixtures, so a legal "
        "tree that merely reuses a directory name becomes uncollectable"
    )


def test_nested_anchors_sharing_a_namespace_and_name_are_rejected() -> None:
    """An ancestor/descendant pair is a real clash — some test sees both."""
    # Arrange — same basename at two nested depths, so both derive namespace 'v1'
    registry = FixtureRegistry()
    register_module_source_fixtures(
        registry,
        _fixtures_module("/t/v1/__fixtures__.py"),
        anchor_package_path="/t/v1",
    )

    # Act / Assert
    with raises(UsageError):
        register_module_source_fixtures(
            registry,
            _fixtures_module("/t/v1/v1/__fixtures__.py"),
            anchor_package_path="/t/v1/v1",
        )


def test_a_conftest_declaration_still_clashes_with_any_anchor() -> None:
    """Unanchored sources are run-wide, so they clash regardless of tree shape."""
    # Arrange
    registry = FixtureRegistry()
    registry.register(
        FixtureDef(
            name="conn",
            fixture_type=object,
            scope=FixtureScope.EACH,
            source=ConftestSource(func=lambda: None, conftest_path="/t/conftest.py"),
            namespace="v1",
        )
    )

    # Act / Assert
    with raises(UsageError):
        register_module_source_fixtures(
            registry,
            _fixtures_module("/t/api/v1/__fixtures__.py"),
            anchor_package_path="/t/api/v1",
        )


# ── ADR-0009 Rule 2: the home-kind cap (#1859) ────────────────────────────────
#
# The cap moved here from the Rust prescan, which recognized three decorator
# spellings and so silently did not apply to any other. These exercise the
# registrar directly; the end-to-end path is covered by
# test_1859_alias_enforcement.py, which runs oxitest as a subprocess and
# therefore cannot see this code from a coverage run.


def _module_declaring(path: str, **lifetimes: str) -> types.ModuleType:
    """A synthetic module declaring one fixture per name → lifetime pair.

    The home *kind* is decided by what the caller passes as
    ``anchor_package_path``, not here: passing *path* itself makes the module an
    inline declaration home, while passing its parent directory makes it a
    package home. Both shapes are needed, because the cap applies to one and
    must not apply to the other.
    """
    mod = types.ModuleType("synthetic_inline")
    mod.__file__ = path
    for name, lifetime in lifetimes.items():

        @fixture(lifetime=lifetime)
        def declared() -> str:
            return "value"

        setattr(mod, name, declared)
    return mod


def test_inline_declaration_may_not_declare_package_lifetime() -> None:
    """An inline fixture outlives the only scope that can see it."""
    # Arrange
    registry = FixtureRegistry()
    path = "/t/pkg/test_inline.py"

    # Act / Assert
    with raises(UsageError, match="capped at"):
        register_module_source_fixtures(
            registry,
            _module_declaring(path, engine="package"),
            anchor_package_path=path,
        )


def test_inline_declaration_may_not_declare_process_lifetime() -> None:
    """`process` is the second tier above the cap, and the set is complete."""
    # Arrange
    registry = FixtureRegistry()
    path = "/t/pkg/test_inline.py"

    # Act / Assert
    with raises(UsageError, match="capped at"):
        register_module_source_fixtures(
            registry,
            _module_declaring(path, engine="process"),
            anchor_package_path=path,
        )


def test_a_package_home_may_still_declare_package_lifetime() -> None:
    """The cap is conditional on home *kind* — it is not a blanket rejection.

    The declared tier must be one the cap actually rejects inline, or this
    proves nothing: an earlier version of this test registered a
    ``function``-lifetime fixture, which the cap never inspects, and a mutant
    that dropped the ``is_inline`` condition — applying the cap to every home
    kind — passed it. A negative control has to sit on the boundary it guards.
    """
    # Arrange — a *directory* anchor, so this home is not inline.
    registry = FixtureRegistry()

    # Act
    register_module_source_fixtures(
        registry,
        _module_declaring("/t/pkg/__fixtures__.py", engine="package"),
        anchor_package_path="/t/pkg",
    )

    # Assert
    defn = registry.get_in_namespace("engine", "pkg")
    assert defn is not None, (
        "a declaration home anchored to a directory is not inline, so the "
        "home-kind cap must not apply to it — rejecting here would break every "
        "legitimate package-lifetime declaration in the suite"
    )


def test_inline_cap_violations_accumulate_into_one_error() -> None:
    """Every offender in a module is named by a single run."""
    # Arrange
    registry = FixtureRegistry()
    path = "/t/pkg/test_inline.py"
    module = _module_declaring(path, first_bad="package", second_bad="process")

    # Act
    with raises(UsageError) as caught:
        register_module_source_fixtures(registry, module, anchor_package_path=path)

    # Assert
    message = str(caught.value)
    for name in ("first_bad", "second_bad"):
        assert name in message, (
            f"violations accumulate so one run names them all; a fail-fast "
            f"check would report only the first, and someone whose aliased "
            f"declarations were silently ignored likely has several. {name!r} "
            f"missing from:\n{message}"
        )


def test_inline_cap_message_names_the_sibling_fixtures_home() -> None:
    """#1711's review lesson: an unactionable hint is not a hint."""
    # Arrange
    registry = FixtureRegistry()
    path = "/t/pkg/test_inline.py"

    # Act
    with raises(UsageError) as caught:
        register_module_source_fixtures(
            registry,
            _module_declaring(path, engine="package"),
            anchor_package_path=path,
        )

    # Assert
    message = str(caught.value)
    assert str(Path("/t/pkg") / "__fixtures__.py") in message, (
        f"the hint must name the destination file, not merely say 'move it "
        f"elsewhere' — the user cannot derive the target otherwise; got:\n"
        f"{message}"
    )
