"""Tests for FixtureRegistry — register, resolve, namespace, type-based lookup."""

from __future__ import annotations

from oxitest import WarnCapture, helpers, raises
from oxitest._bridge._errors import (
    AmbiguousFixtureError,
    FixtureNotFoundError,
)
from oxitest._bridge._fixture_registry import (
    FixtureRegistry,
    FixtureShadowWarning,
)
from oxitest._bridge.result import ViolationKind

# ── FixtureRegistry ───────────────────────────────────────────────────────────


def test_registry_get_returns_none_for_unknown() -> None:
    """FixtureRegistry.get() returns None when the name has never been registered."""
    reg = helpers.common.make_registry()
    assert reg.get("missing") is None, (
        "FixtureRegistry.get() for an unregistered name should return None"
    )


def test_registry_register_and_get() -> None:
    """Registering a FixtureDef and calling get() returns the same object."""
    defn = helpers.common.make_fixture_def("db", conftest_path="/c.py")
    reg = helpers.common.make_registry(defn)
    assert reg.get("db") is defn, (
        "FixtureRegistry.get('db') should return the exact FixtureDef that was "
        "registered"
    )


def test_registry_most_local_wins() -> None:
    """When the same fixture name is in two conftests, the leaf-most wins."""
    root = helpers.common.make_fixture_def(
        "db", lambda: 1, conftest_path="/root/conftest.py"
    )
    leaf = helpers.common.make_fixture_def(
        "db", lambda: 2, conftest_path="/root/tests/conftest.py"
    )
    reg = helpers.common.make_registry(root, leaf)
    assert reg.get("db") is leaf, (
        "FixtureRegistry should prefer the more-local (leaf) fixture over the root "
        "fixture"
    )


def test_registry_get_autouse_returns_only_autouse() -> None:
    """get_autouse() yields only fixtures registered with autouse=True."""
    auto = helpers.common.make_fixture_def("setup", autouse=True, conftest_path="/c.py")
    manual = helpers.common.make_fixture_def("db", conftest_path="/c.py")
    reg = helpers.common.make_registry(auto, manual)
    result = list(reg.get_autouse())
    assert len(result) == 1, (
        f"get_autouse() should return only 1 autouse fixture, got {len(result)}: "
        f"{[d.name for d in result]}"
    )
    assert result[0].name == "setup", (
        f"the autouse fixture should be named 'setup', got {result[0].name!r}"
    )


def test_registry_get_autouse_empty() -> None:
    """get_autouse() on an empty registry returns an empty sequence."""
    reg = helpers.common.make_registry()
    assert list(reg.get_autouse()) == [], (
        "get_autouse() on an empty registry should return an empty list"
    )


# ── FixtureRegistry: strict violations ─────────────────────────────────────────


def test_register_returns_violation_for_untyped_fixture() -> None:
    """No return annotation on a fixture yields MISSING_RETURN_ANNOTATION violation."""
    # Arrange
    reg = FixtureRegistry()
    defn = helpers.common.make_fixture_def(
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

    defn = helpers.common.make_fixture_def(
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


def test_register_duplicate_name_warns(warn: WarnCapture) -> None:
    """Registering the same name from two conftests emits a FixtureShadowWarning."""
    # Arrange
    reg = FixtureRegistry()
    parent_def = helpers.common.make_fixture_def("db", conftest_path="conftest.py")
    child_def = helpers.common.make_fixture_def("db", conftest_path="tests/conftest.py")

    # Act
    reg.register(parent_def)
    reg.register(child_def)

    # Assert
    shadow_warnings = [
        w for w in warn.list if issubclass(w.category, FixtureShadowWarning)
    ]
    assert len(shadow_warnings) == 1, (
        f"registering 'db' from a different conftest should emit 1 shadow warning, "
        f"got {len(shadow_warnings)}"
    )
    msg = str(shadow_warnings[0].message)
    assert "db" in msg, f"warning should mention fixture name 'db', got {msg!r}"
    assert "conftest.py" in msg, f"warning should mention parent path, got {msg!r}"
    assert "tests/conftest.py" in msg, f"warning should mention child path, got {msg!r}"


def test_register_first_fixture_no_shadow_warning(warn: WarnCapture) -> None:
    """First registration of a fixture name never emits a shadow warning."""
    # Arrange
    reg = FixtureRegistry()
    defn = helpers.common.make_fixture_def("db", conftest_path="conftest.py")

    # Act
    reg.register(defn)

    # Assert
    shadow_warnings = [
        w for w in warn.list if issubclass(w.category, FixtureShadowWarning)
    ]
    assert shadow_warnings == [], (
        "first registration of a fixture should not emit a shadow warning, "
        f"got {len(shadow_warnings)}"
    )


def test_register_same_conftest_no_shadow_warning(warn: WarnCapture) -> None:
    """Re-registering a fixture from the same conftest emits no shadow warning."""
    # Arrange
    reg = FixtureRegistry()
    first = helpers.common.make_fixture_def("db", conftest_path="conftest.py")
    second = helpers.common.make_fixture_def("db", conftest_path="conftest.py")

    # Act
    reg.register(first)
    reg.register(second)

    # Assert
    shadow_warnings = [
        w for w in warn.list if issubclass(w.category, FixtureShadowWarning)
    ]
    assert shadow_warnings == [], (
        "re-registering 'db' from the same conftest should not emit a shadow warning, "
        f"got {len(shadow_warnings)}"
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
    assert str(exc) == "fixture 'conn' not found", (
        f"FixtureNotFoundError without namespace should format as \"fixture 'conn' not "
        f'found", got {str(exc)!r}'
    )
    assert exc.namespace == "", (
        f"FixtureNotFoundError without namespace should have exc.namespace='', got "
        f"{exc.namespace!r}"
    )


# ── FixtureDef.namespace field ────────────────────────────────────────────────


def test_fixture_def_has_namespace_field() -> None:
    """FixtureDef stores the namespace name passed at construction."""
    defn = helpers.common.make_fixture_def(
        "conn", namespace="db", conftest_path="/path/conftest.py"
    )
    assert defn.namespace == "db", (
        f"FixtureDef(namespace='db') should store namespace='db', got "
        f"{defn.namespace!r}"
    )


def test_fixture_def_namespace_defaults_to_empty() -> None:
    """FixtureDef.namespace defaults to empty string when no namespace is given."""
    defn = helpers.common.make_fixture_def("conn")
    assert defn.namespace == "", (
        f"FixtureDef without namespace should default to '', got {defn.namespace!r}"
    )


# ── FixtureRegistry.get_in_namespace + has_namespace ─────────────────────────


def test_registry_get_in_namespace_returns_matching_def() -> None:
    """get_in_namespace() returns the FixtureDef registered under a given namespace."""
    defn = helpers.common.make_fixture_def("conn", lambda: 1, namespace="db")
    reg = helpers.common.make_registry(defn)
    result = reg.get_in_namespace("conn", "db")
    assert result is defn, (
        f"get_in_namespace('conn', 'db') should return the registered def, got "
        f"{result!r}"
    )


def test_registry_get_in_namespace_ignores_other_namespace() -> None:
    """get_in_namespace() distinguishes same-named fixtures in different namespaces."""
    db_def = helpers.common.make_fixture_def("conn", lambda: 1, namespace="db")
    http_def = helpers.common.make_fixture_def("conn", lambda: 2, namespace="http")
    reg = helpers.common.make_registry(db_def, http_def)
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
    defn = helpers.common.make_fixture_def("conn", lambda: 1, namespace="db")
    reg = helpers.common.make_registry(defn)
    assert reg.get_in_namespace("conn", "http") is None, (
        "get_in_namespace('conn', 'http') should return None (wrong namespace)"
    )
    assert reg.get_in_namespace("missing", "db") is None, (
        "get_in_namespace('missing', 'db') should return None (fixture not found)"
    )


def test_registry_has_namespace_true() -> None:
    """has_namespace() returns True when at least one fixture with it is registered."""
    reg = helpers.common.make_registry(
        helpers.common.make_fixture_def("conn", lambda: 1, namespace="db")
    )
    assert reg.has_namespace("db") is True, (
        "has_namespace('db') should return True when a fixture with that namespace is "
        "registered"
    )


def test_registry_has_namespace_false() -> None:
    """has_namespace() returns False when no fixture is registered under a namespace."""
    reg = helpers.common.make_registry(
        helpers.common.make_fixture_def("conn", lambda: 1, namespace="db")
    )
    assert reg.has_namespace("http") is False, (
        "has_namespace('http') should return False when no fixture with that namespace "
        "exists"
    )


def test_registry_has_namespace_empty_registry() -> None:
    """has_namespace() returns False on an empty registry."""
    reg = helpers.common.make_registry()
    assert reg.has_namespace("db") is False, (
        "has_namespace('db') should return False on an empty registry"
    )


# ── FixtureRegistry: __contains__, __iter__, all_defs ─────────────────────────


def test_registry_contains_registered_name() -> None:
    """__contains__ returns True for a fixture name that has been registered."""
    reg = helpers.common.make_registry(
        helpers.common.make_fixture_def("db", conftest_path="conftest.py")
    )
    assert "db" in reg, "__contains__ should return True for a registered fixture name"


def test_registry_contains_returns_false_for_unknown() -> None:
    """__contains__ returns False for a name that has never been registered."""
    reg = helpers.common.make_registry()
    assert "missing" not in reg, (
        "__contains__ should return False for an unregistered fixture name"
    )


def test_registry_iter_yields_registered_names() -> None:
    """__iter__ yields all registered fixture names."""
    reg = helpers.common.make_registry(
        helpers.common.make_fixture_def("a", conftest_path="conftest.py"),
        helpers.common.make_fixture_def("b", conftest_path="conftest.py"),
    )
    assert set(reg) == {"a", "b"}, "__iter__ should yield all registered fixture names"


def test_registry_iter_empty() -> None:
    """__iter__ on an empty registry yields nothing."""
    reg = helpers.common.make_registry()
    assert list(reg) == [], "__iter__ on an empty registry should yield nothing"


def test_registry_all_defs_returns_all_entries() -> None:
    """all_defs() returns all FixtureDefs for a name in registration order."""
    reg = helpers.common.make_registry(
        helpers.common.make_fixture_def(
            "db", lambda: "root", conftest_path="root/conftest.py"
        ),
        helpers.common.make_fixture_def(
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
    reg = helpers.common.make_registry()
    assert reg.all_defs("missing") == [], (
        "all_defs for an unregistered name should return an empty list"
    )


# ── FixtureRegistry: dual-index type-based resolve ────────────────────────────


class DBSession:
    """Stub type used to test type-based fixture resolution."""


class AuthToken:
    """Stub type used to verify FixtureNotFoundError when no fixture matches a type."""


def test_registry_resolve_by_type_unique() -> None:
    """Single fixture for a type resolves regardless of qualifier."""
    defn = helpers.common.make_fixture_def(
        "db_session", DBSession, conftest_path="/c.py", fixture_type=DBSession
    )
    reg = helpers.common.make_registry(defn)

    result = reg.resolve(DBSession)
    assert result.name == "db_session", "should resolve the only match by type"

    result2 = reg.resolve(DBSession, qualifier="anything")
    assert result2.name == "db_session", "qualifier ignored when type is unique"


def test_registry_resolve_by_type_ambiguous_with_qualifier() -> None:
    """Two fixtures of same type -- qualifier disambiguates."""
    dev = helpers.common.make_fixture_def(
        "dev_db", DBSession, conftest_path="/c.py", fixture_type=DBSession
    )
    prod = helpers.common.make_fixture_def(
        "prod_db", DBSession, conftest_path="/c.py", fixture_type=DBSession
    )
    reg = helpers.common.make_registry(dev, prod)

    result = reg.resolve(DBSession, qualifier="dev_db")
    assert result.name == "dev_db", "qualifier should select dev_db"


def test_registry_resolve_ambiguous_no_match() -> None:
    """Two fixtures of same type, unknown qualifier -- AmbiguousFixtureError."""
    dev = helpers.common.make_fixture_def(
        "dev_db", DBSession, conftest_path="/c.py", fixture_type=DBSession
    )
    prod = helpers.common.make_fixture_def(
        "prod_db", DBSession, conftest_path="/c.py", fixture_type=DBSession
    )
    reg = helpers.common.make_registry(dev, prod)

    with raises(AmbiguousFixtureError, match="ambiguous"):
        reg.resolve(DBSession, qualifier="unknown")


def test_registry_resolve_no_match() -> None:
    """No fixture for type -- FixtureNotFoundError."""
    reg = helpers.common.make_registry()

    with raises(FixtureNotFoundError):
        reg.resolve(AuthToken)


def test_registry_override_precedence() -> None:
    """Last registered fixture of same type wins (leaf conftest overrides root)."""
    root = helpers.common.make_fixture_def(
        "db", lambda: "root", conftest_path="/conftest.py", fixture_type=DBSession
    )
    leaf = helpers.common.make_fixture_def(
        "db", lambda: "leaf", conftest_path="/tests/conftest.py", fixture_type=DBSession
    )
    reg = helpers.common.make_registry(root, leaf)

    result = reg.resolve(DBSession)
    assert result.conftest_path == "/tests/conftest.py", (
        "leaf conftest should override root"
    )
