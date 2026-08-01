"""Tests for FixtureRegistry — register, resolve, namespace, type-based lookup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from oxitest import Fixture, raises
from oxitest._bridge._errors import (
    AmbiguousFixtureError,
    FixtureNotFoundError,
)
from oxitest._bridge._fixture_registry import (
    ConftestSource,
    FixtureDef,
    FixtureRegistry,
    FixtureScope,
    ModuleSource,
    PluginSource,
)
from oxitest._bridge._lifetime import Lifetime
from oxitest._bridge.result import Diagnostic, ViolationKind
from tests import helpers

# ── FixtureRegistry ───────────────────────────────────────────────────────────


def test_registry_get_returns_none_for_unknown() -> None:
    """FixtureRegistry.get() returns None when the name has never been registered."""
    reg = helpers.make_registry()
    assert reg.get("missing") is None, (
        "FixtureRegistry.get() for an unregistered name should return None"
    )


def test_registry_register_and_get() -> None:
    """Registering a FixtureDef and calling get() returns the same object."""
    defn = helpers.make_fixture_def("db", conftest_path="/c.py")
    reg = helpers.make_registry(defn)
    assert reg.get("db") is defn, (
        "FixtureRegistry.get('db') should return the exact FixtureDef that was "
        "registered"
    )


def test_registry_most_local_wins() -> None:
    """When the same fixture name is in two conftests, the leaf-most wins."""
    root = helpers.make_fixture_def("db", lambda: 1, conftest_path="/root/conftest.py")
    leaf = helpers.make_fixture_def(
        "db", lambda: 2, conftest_path="/root/tests/conftest.py"
    )
    reg = helpers.make_registry(root, leaf)
    assert reg.get("db") is leaf, (
        "FixtureRegistry should prefer the more-local (leaf) fixture over the root "
        "fixture"
    )


def test_registry_get_autouse_returns_only_autouse() -> None:
    """get_autouse() yields only fixtures registered with autouse=True."""
    auto = helpers.make_fixture_def("setup", autouse=True, conftest_path="/c.py")
    manual = helpers.make_fixture_def("db", conftest_path="/c.py")
    reg = helpers.make_registry(auto, manual)
    result = list(reg.get_autouse(None))
    assert len(result) == 1, (
        f"get_autouse() should return only 1 autouse fixture, got {len(result)}: "
        f"{[d.name for d in result]}"
    )
    assert result[0].name == "setup", (
        f"the autouse fixture should be named 'setup', got {result[0].name!r}"
    )


def test_registry_get_autouse_empty() -> None:
    """get_autouse() on an empty registry returns an empty sequence."""
    reg = helpers.make_registry()
    assert list(reg.get_autouse(None)) == [], (
        "get_autouse() on an empty registry should return an empty list"
    )


# ── FixtureRegistry: strict violations ─────────────────────────────────────────


def test_register_returns_violation_for_untyped_fixture() -> None:
    """No return annotation on a fixture yields MISSING_RETURN_ANNOTATION violation."""
    # Arrange
    reg = FixtureRegistry()
    defn = helpers.make_fixture_def(
        "db", lambda: None, conftest_path="/project/conftest.py"
    )

    # Act
    violations = reg.register(defn)

    # Assert
    assert len(violations) == 1, (
        f"registering an untyped fixture should produce 1 violation, "
        f"got {len(violations)}"
    )
    assert violations[0].kind == ViolationKind.MISSING_RETURN_ANNOTATION, (
        f"violation kind should be MISSING_RETURN_ANNOTATION, "
        f"got {violations[0].kind!r}"
    )
    assert violations[0].detail == "db", (
        f"violation detail should be fixture name 'db', got {violations[0].detail!r}"
    )
    assert violations[0].node_id == "/project/conftest.py", (
        f"violation node_id should be conftest path, got {violations[0].node_id!r}"
    )


def test_register_returns_empty_for_typed_fixture() -> None:
    """Registering a fully-annotated fixture produces no violations."""
    # Arrange
    reg = FixtureRegistry()

    def typed_fixture() -> int:
        return 42

    defn = helpers.make_fixture_def(
        "val", typed_fixture, conftest_path="/project/conftest.py"
    )

    # Act
    violations = reg.register(defn)

    # Assert
    assert violations == [], (
        "registering a typed fixture should produce no violations, "
        f"got {len(violations)}"
    )


# ── FixtureRegistry: shadow warnings ──────────────────────────────────────────


def test_register_duplicate_name_warns(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """Registering the same name from two conftests emits a shadow diagnostic."""
    # Arrange
    reg = FixtureRegistry()
    parent_def = helpers.make_fixture_def("db", conftest_path="conftest.py")
    child_def = helpers.make_fixture_def("db", conftest_path="tests/conftest.py")

    # Act
    reg.register(parent_def)
    reg.register(child_def)

    # Assert
    shadow_diags = [d for d in diag_collector if d.context == "fixture registration"]
    assert len(shadow_diags) == 1, (
        f"registering 'db' from a different conftest should emit 1 shadow diagnostic, "
        f"got {len(shadow_diags)}"
    )
    msg = shadow_diags[0].message
    assert "db" in msg, (
        "without the fixture name, users cannot tell which fixture was"
        f" shadowed: {msg!r}"
    )
    assert "conftest.py" in msg, (
        f"without the parent path, users cannot find the original definition: {msg!r}"
    )
    assert "tests/conftest.py" in msg, (
        f"without the child path, users cannot find the shadowing definition: {msg!r}"
    )


def test_register_first_fixture_no_shadow_diagnostic(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """First registration of a fixture name never emits a shadow diagnostic."""
    # Arrange
    reg = FixtureRegistry()
    defn = helpers.make_fixture_def("db", conftest_path="conftest.py")

    # Act
    reg.register(defn)

    # Assert
    shadow_diags = [d for d in diag_collector if d.context == "fixture registration"]
    assert shadow_diags == [], (
        "first registration of a fixture should not emit a shadow diagnostic, "
        f"got {len(shadow_diags)}"
    )


def test_register_same_conftest_no_shadow_diagnostic(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """Re-registering a fixture from the same conftest emits no shadow diagnostic."""
    # Arrange
    reg = FixtureRegistry()
    first = helpers.make_fixture_def("db", conftest_path="conftest.py")
    second = helpers.make_fixture_def("db", conftest_path="conftest.py")

    # Act
    reg.register(first)
    reg.register(second)

    # Assert
    shadow_diags = [d for d in diag_collector if d.context == "fixture registration"]
    assert shadow_diags == [], (
        "re-registering 'db' from the same conftest should not emit a shadow"
        f" diagnostic, got {len(shadow_diags)}"
    )


# ── FixtureNotFoundError namespace field ──────────────────────────────────────


def test_fixture_not_found_error_with_namespace() -> None:
    """FixtureNotFoundError includes both fixture name and namespace in the message."""
    exc = FixtureNotFoundError("conn", namespace="db")
    assert "conn" in str(exc), (
        f"FixtureNotFoundError with namespace should mention fixture name 'conn', got "
        f"{str(exc)!r}"
    )
    assert "db" in str(exc), (
        f"FixtureNotFoundError with namespace should mention namespace 'db', got "
        f"{str(exc)!r}"
    )
    assert exc.fixture_name == "conn", (
        f"exc.fixture_name should be 'conn', got {exc.fixture_name!r}"
    )
    assert exc.namespace == "db", f"exc.namespace should be 'db', got {exc.namespace!r}"


def test_fixture_not_found_error_without_namespace() -> None:
    """FixtureNotFoundError without a namespace formats as 'fixture not found'."""
    exc = FixtureNotFoundError("conn")
    assert str(exc).startswith("fixture 'conn' not found."), (
        f"FixtureNotFoundError without namespace should start with "
        f"\"fixture 'conn' not found.\", got {str(exc)!r}"
    )
    assert "Hint:" in str(exc), (
        f"FixtureNotFoundError should include a corrective hint, got {str(exc)!r}"
    )
    assert exc.namespace == "", (
        f"FixtureNotFoundError without namespace should have exc.namespace='', got "
        f"{exc.namespace!r}"
    )


# ── FixtureDef.namespace field ────────────────────────────────────────────────


def test_fixture_def_has_namespace_field() -> None:
    """FixtureDef stores the namespace name passed at construction."""
    defn = helpers.make_fixture_def(
        "conn", namespace="db", conftest_path="/path/conftest.py"
    )
    assert defn.namespace == "db", (
        f"FixtureDef(namespace='db') should store namespace='db', got "
        f"{defn.namespace!r}"
    )


def test_fixture_def_namespace_defaults_to_empty() -> None:
    """FixtureDef.namespace defaults to empty string when no namespace is given."""
    defn = helpers.make_fixture_def("conn")
    assert defn.namespace == "", (
        f"FixtureDef without namespace should default to '', got {defn.namespace!r}"
    )


# ── FixtureRegistry.get_in_namespace + has_namespace ─────────────────────────


def test_registry_get_in_namespace_returns_matching_def() -> None:
    """get_in_namespace() returns the FixtureDef registered under a given namespace."""
    defn = helpers.make_fixture_def("conn", lambda: 1, namespace="db")
    reg = helpers.make_registry(defn)
    result = reg.get_in_namespace("conn", "db")
    assert result is defn, (
        f"get_in_namespace('conn', 'db') should return the registered def, got "
        f"{result!r}"
    )


def test_registry_get_in_namespace_ignores_other_namespace() -> None:
    """get_in_namespace() distinguishes same-named fixtures in different namespaces."""
    db_def = helpers.make_fixture_def("conn", lambda: 1, namespace="db")
    http_def = helpers.make_fixture_def("conn", lambda: 2, namespace="http")
    reg = helpers.make_registry(db_def, http_def)
    assert reg.get_in_namespace("conn", "db") is db_def, (
        "get_in_namespace('conn', 'db') should return the db namespace fixture, not "
        "http"
    )
    assert reg.get_in_namespace("conn", "http") is http_def, (
        "get_in_namespace('conn', 'http') should return the http namespace fixture, "
        "not db"
    )


def test_registry_get_in_namespace_returns_none_when_missing() -> None:
    """get_in_namespace() returns None when the fixture or namespace is absent."""
    defn = helpers.make_fixture_def("conn", lambda: 1, namespace="db")
    reg = helpers.make_registry(defn)
    assert reg.get_in_namespace("conn", "http") is None, (
        "get_in_namespace('conn', 'http') should return None (wrong namespace)"
    )
    assert reg.get_in_namespace("missing", "db") is None, (
        "get_in_namespace('missing', 'db') should return None (fixture not found)"
    )


def test_registry_has_namespace_true() -> None:
    """has_namespace() returns True when at least one fixture with it is registered."""
    reg = helpers.make_registry(
        helpers.make_fixture_def("conn", lambda: 1, namespace="db")
    )
    assert reg.has_namespace("db") is True, (
        "has_namespace('db') should return True when a fixture with that namespace is "
        "registered"
    )


def test_registry_has_namespace_false() -> None:
    """has_namespace() returns False when no fixture is registered under a namespace."""
    reg = helpers.make_registry(
        helpers.make_fixture_def("conn", lambda: 1, namespace="db")
    )
    assert reg.has_namespace("http") is False, (
        "has_namespace('http') should return False when no fixture with that namespace "
        "exists"
    )


def test_registry_has_namespace_empty_registry() -> None:
    """has_namespace() returns False on an empty registry."""
    reg = helpers.make_registry()
    assert reg.has_namespace("db") is False, (
        "has_namespace('db') should return False on an empty registry"
    )


# ── FixtureRegistry: __contains__, __iter__, all_defs ─────────────────────────


def test_registry_contains_registered_name() -> None:
    """__contains__ returns True for a fixture name that has been registered."""
    reg = helpers.make_registry(
        helpers.make_fixture_def("db", conftest_path="conftest.py")
    )
    assert "db" in reg, "__contains__ should return True for a registered fixture name"


def test_registry_contains_returns_false_for_unknown() -> None:
    """__contains__ returns False for a name that has never been registered."""
    reg = helpers.make_registry()
    assert "missing" not in reg, (
        "__contains__ should return False for an unregistered fixture name"
    )


def test_registry_iter_yields_registered_names() -> None:
    """__iter__ yields all registered fixture names."""
    reg = helpers.make_registry(
        helpers.make_fixture_def("a", conftest_path="conftest.py"),
        helpers.make_fixture_def("b", conftest_path="conftest.py"),
    )
    assert set(reg) == {"a", "b"}, "__iter__ should yield all registered fixture names"


def test_registry_iter_empty() -> None:
    """__iter__ on an empty registry yields nothing."""
    reg = helpers.make_registry()
    assert list(reg) == [], "__iter__ on an empty registry should yield nothing"


def test_registry_all_defs_returns_all_entries() -> None:
    """all_defs() returns all FixtureDefs for a name in registration order."""
    reg = helpers.make_registry(
        helpers.make_fixture_def(
            "db", lambda: "root", conftest_path="root/conftest.py"
        ),
        helpers.make_fixture_def(
            "db", lambda: "leaf", conftest_path="root/sub/conftest.py"
        ),
    )
    defs = reg.all_defs("db")
    assert len(defs) == 2, (
        "all_defs should return all registered FixtureDefs for a name"
    )
    assert defs[0].conftest_path == "root/conftest.py", (
        "first entry should be the root conftest definition"
    )
    assert defs[1].conftest_path == "root/sub/conftest.py", (
        "second entry should be the leaf conftest definition"
    )


def test_registry_all_defs_returns_empty_for_unknown() -> None:
    """all_defs() returns an empty list for a name that was never registered."""
    reg = helpers.make_registry()
    assert reg.all_defs("missing") == (), (
        "all_defs for an unregistered name should return an empty tuple"
    )


# ── FixtureRegistry: dual-index type-based resolve ────────────────────────────


class DBSession:
    """Stub type used to test type-based fixture resolution."""


class AuthToken:
    """Stub type used to verify FixtureNotFoundError when no fixture matches a type."""


def test_registry_resolve_by_type_unique() -> None:
    """Single fixture for a type resolves regardless of qualifier."""
    defn = helpers.make_fixture_def(
        "db_session", DBSession, conftest_path="/c.py", fixture_type=DBSession
    )
    reg = helpers.make_registry(defn)

    result = reg.resolve(DBSession)
    assert result.name == "db_session", "should resolve the only match by type"

    result2 = reg.resolve(DBSession, qualifier="anything")
    assert result2.name == "db_session", "qualifier ignored when type is unique"


def test_registry_resolve_by_type_ambiguous_with_qualifier() -> None:
    """Two fixtures of same type -- qualifier disambiguates."""
    dev = helpers.make_fixture_def(
        "dev_db", DBSession, conftest_path="/c.py", fixture_type=DBSession
    )
    prod = helpers.make_fixture_def(
        "prod_db", DBSession, conftest_path="/c.py", fixture_type=DBSession
    )
    reg = helpers.make_registry(dev, prod)

    result = reg.resolve(DBSession, qualifier="dev_db")
    assert result.name == "dev_db", "qualifier should select dev_db"


def test_registry_resolve_ambiguous_no_match() -> None:
    """Two fixtures of same type, unknown qualifier -- AmbiguousFixtureError."""
    dev = helpers.make_fixture_def(
        "dev_db", DBSession, conftest_path="/c.py", fixture_type=DBSession
    )
    prod = helpers.make_fixture_def(
        "prod_db", DBSession, conftest_path="/c.py", fixture_type=DBSession
    )
    reg = helpers.make_registry(dev, prod)

    with raises(AmbiguousFixtureError, match="ambiguous"):
        reg.resolve(DBSession, qualifier="unknown")


def test_registry_resolve_no_match() -> None:
    """No fixture for type -- FixtureNotFoundError."""
    reg = helpers.make_registry()

    with raises(FixtureNotFoundError):
        reg.resolve(AuthToken)


def test_registry_override_precedence() -> None:
    """Last registered fixture of same type wins (leaf conftest overrides root)."""
    root = helpers.make_fixture_def(
        "db", lambda: "root", conftest_path="/conftest.py", fixture_type=DBSession
    )
    leaf = helpers.make_fixture_def(
        "db", lambda: "leaf", conftest_path="/tests/conftest.py", fixture_type=DBSession
    )
    reg = helpers.make_registry(root, leaf)

    result = reg.resolve(DBSession)
    assert result.conftest_path == "/tests/conftest.py", (
        "leaf conftest should override root"
    )


# ── FixtureRegistry: anchor-aware filtered queries (B1, #1713) ────────────────


def _module_def(
    name: str, namespace: str, anchor: str, *, autouse: bool = False
) -> FixtureDef[Any]:
    """A package-anchored FixtureDef, the only source B1 constrains."""
    return FixtureDef(
        name=name,
        fixture_type=object,
        scope=FixtureScope.EACH,
        source=ModuleSource(
            func=lambda: None,
            defining_module_path=f"{anchor}/__fixtures__.py",
            anchor_package_path=anchor,
            lifetime=Lifetime.FUNCTION,
        ),
        autouse=autouse,
        namespace=namespace,
    )


def test_same_basename_siblings_resolve_to_their_own_anchor() -> None:
    """`tests/api/v1` and `tests/admin/v1` both derive the namespace 'v1'."""
    # Arrange
    registry = FixtureRegistry()
    registry.register(_module_def("conn", "v1", "/t/api/v1"))
    registry.register(_module_def("conn", "v1", "/t/admin/v1"))

    # Act
    from_api = registry.get_visible_in_namespace("conn", "v1", "/t/api/v1/test_a.py")
    from_admin = registry.get_visible_in_namespace(
        "conn", "v1", "/t/admin/v1/test_a.py"
    )

    # Assert
    assert from_api is not None and from_api.anchor == "/t/api/v1", (
        "an anchor-blind lookup returns whichever def registered last, so which "
        "'conn' a test receives would be decided by filesystem walk order"
    )
    assert from_admin is not None and from_admin.anchor == "/t/admin/v1", (
        "the second package must get its own 'conn' — the whole point of "
        "filtering before picking is that disjoint subtrees never interfere"
    )


def test_nested_anchors_resolve_to_the_deepest_visible_one() -> None:
    """Both are visible from below; the nearer declaration overrides."""
    # Arrange — register the deep one FIRST so a `defs[-1]` implementation loses
    registry = FixtureRegistry()
    registry.register(_module_def("conn", "shared", "/t/api/v1"))
    registry.register(_module_def("conn", "shared", "/t"))

    # Act
    resolved = registry.get_visible_in_namespace(
        "conn", "shared", "/t/api/v1/test_a.py"
    )

    # Assert
    assert resolved is not None and resolved.anchor == "/t/api/v1", (
        "deepest-visible-wins is the locality rule conftest already had; "
        "registering parent-last must not flip the winner, or the answer "
        "depends on the order the collector happened to walk the tree in"
    )


def test_unanchored_defs_keep_last_registered_wins() -> None:
    """With no ModuleSource in play the ordering must be exactly as before."""
    # Arrange
    registry = FixtureRegistry()
    first = FixtureDef(
        name="conn",
        fixture_type=object,
        scope=FixtureScope.EACH,
        source=ConftestSource(func=lambda: None, conftest_path="/t/conftest.py"),
    )
    second = FixtureDef(
        name="conn",
        fixture_type=object,
        scope=FixtureScope.EACH,
        source=ConftestSource(func=lambda: None, conftest_path="/t/api/conftest.py"),
    )
    registry.register(first)
    registry.register(second)

    # Act
    resolved = registry.get_visible("conn", "/t/api/test_a.py")

    # Assert
    assert resolved is second, (
        "conftest fixtures are exempt from B1, so introducing anchor-depth "
        "ordering must not disturb pytest's most-local-conftest-wins semantics "
        "for the legacy API that slices 6-12 still run alongside"
    )


def test_namespace_visibility_and_anchors_are_separate_queries() -> None:
    """The two query modes: 'reachable from here' and 'known anywhere'."""
    # Arrange
    registry = FixtureRegistry()
    registry.register(_module_def("api_conn", "api", "/t/api"))

    # Act
    visible_here = registry.has_visible_anchor("api", "/t/api/v1/test_a.py")
    visible_elsewhere = registry.has_visible_anchor("api", "/t/admin/test_a.py")
    known_anywhere = registry.has_namespace("api")
    anchors = registry.namespace_anchors("api")

    # Assert
    assert visible_here is True and visible_elsewhere is False, (
        "the filtered query is what decides resolution — without it every "
        "namespace is reachable from every test and B1 does not exist"
    )
    assert known_anywhere is True, (
        "the full query must keep answering yes from outside the boundary; it "
        "is the only thing that lets the diagnostic say 'exists, elsewhere' "
        "instead of 'no such fixture'"
    )
    assert anchors == ("/t/api",), (
        "the BoundaryError message names where the namespace actually lives, "
        "so the registry has to be able to report it to a test that cannot "
        "see it"
    )


# ── FixtureRegistry: B1-filtered autouse enumeration (#1774) ──────────────────


@dataclass(frozen=True)
class _ProviderDouble:
    """Stand-in for a FixtureProvider; enumeration never calls it."""

    plugin_name: str = "plug"


def test_anchored_autouse_not_yielded_outside_boundary() -> None:
    """An anchored autouse def is enumerated only inside its B1 boundary."""
    # Arrange
    registry = FixtureRegistry()
    registry.register(_module_def("api_setup", "api", "/t/api", autouse=True))

    # Act
    inside = [defn.name for defn in registry.get_autouse("/t/api/v1/test_a.py")]
    outside = list(registry.get_autouse("/t/admin/test_b.py"))

    # Assert
    assert inside == ["api_setup"], (
        "tests inside the anchor's subtree are exactly who the autouse fixture "
        "was declared for — B1 filtering must not drop in-boundary candidates"
    )
    assert outside == [], (
        "resolution B1-filters and raises FixtureNotFoundError for names it "
        "cannot see, so an out-of-boundary candidate yielded here becomes a "
        "spurious hard error on a fixture the test never requested (#1774)"
    )


def test_unanchored_autouse_yielded_regardless_of_module_path() -> None:
    """Conftest and plugin autouse defs are ambient — they fire run-wide."""
    # Arrange
    conftest_def = helpers.make_fixture_def(
        "legacy_setup", autouse=True, conftest_path="/t/alpha/conftest.py"
    )
    plugin_def = FixtureDef(
        name="plugin_setup",
        fixture_type=object,
        scope=FixtureScope.EACH,
        source=PluginSource(provider=_ProviderDouble(), plugin_module="plug"),
        autouse=True,
    )
    registry = helpers.make_registry(conftest_def, plugin_def)

    # Act
    yielded = {defn.name for defn in registry.get_autouse("/t/beta/test_b.py")}

    # Assert
    assert yielded == {"legacy_setup", "plugin_setup"}, (
        "unanchored sources are exempt from B1 by design (ADR-0009 Rules 6-7); "
        "if the filter swallows them, every legacy conftest and plugin autouse "
        "fixture silently stops running for sibling packages"
    )


def test_autouse_enumeration_and_resolution_agree_on_multi_def_names() -> None:
    """The def that decides autouse-ness is the def resolution returns."""
    # Arrange — anchored autouse registered FIRST, unanchored non-autouse LAST,
    # so a defs[-1] implementation sees a non-autouse def and yields nothing.
    registry = FixtureRegistry()
    anchored = _module_def("setup", "api", "/t/api", autouse=True)
    registry.register(anchored)
    registry.register(helpers.make_fixture_def("setup", conftest_path="/t/conftest.py"))
    module_path = "/t/api/test_a.py"

    # Act
    yielded = list(registry.get_autouse(module_path))
    resolved = registry.get_visible("setup", module_path)

    # Assert
    assert yielded == [anchored] and resolved is anchored, (
        "enumeration deciding autouse-ness on defs[-1] while resolution picks "
        "deepest-visible means the def whose autouse=True queued the name is "
        "not the def that actually runs — one predicate must choose both (#1774)"
    )


def test_autouse_full_catalog_mode_uses_last_registered() -> None:
    """module_path=None is the validator's query: no filtering, defs[-1] wins."""
    # Arrange
    registry = FixtureRegistry()
    registry.register(_module_def("api_setup", "api", "/t/api", autouse=True))
    registry.register(
        helpers.make_fixture_def("legacy_setup", autouse=True, conftest_path="/c.py")
    )

    # Act
    names = {defn.name for defn in registry.get_autouse(None)}

    # Assert
    assert names == {"api_setup", "legacy_setup"}, (
        "find_unused_fixtures seeds used-ness from every autouse fixture in "
        "the run — filtering the catalog query would let a fixture used only "
        "inside its own boundary be flagged as unused"
    )
