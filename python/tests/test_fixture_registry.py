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
    FixtureDef,
    FixtureRegistry,
    FixtureScope,
    FrameworkSource,
    ModuleSource,
    PluginModuleSource,
    PluginSource,
    _shadow_order,
)
from oxitest._bridge._lifetime import Lifetime
from oxitest._bridge.query_bridge import fixture_entries
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
    defn = helpers.make_fixture_def("db", declaration_path="/c.py")
    reg = helpers.make_registry(defn)
    assert reg.get("db") is defn, (
        "FixtureRegistry.get('db') should return the exact FixtureDef that was "
        "registered"
    )


def test_registry_most_local_wins() -> None:
    """When the same fixture name is in two conftests, the leaf-most wins."""
    root = helpers.make_fixture_def(
        "db", lambda: 1, declaration_path="/root/conftest.py"
    )
    leaf = helpers.make_fixture_def(
        "db", lambda: 2, declaration_path="/root/tests/conftest.py"
    )
    reg = helpers.make_registry(root, leaf)
    assert reg.get("db") is leaf, (
        "FixtureRegistry should prefer the more-local (leaf) fixture over the root "
        "fixture"
    )


def test_registry_get_autouse_returns_only_autouse() -> None:
    """get_autouse() yields only fixtures registered with autouse=True."""
    auto = helpers.make_fixture_def("setup", autouse=True, declaration_path="/c.py")
    manual = helpers.make_fixture_def("db", declaration_path="/c.py")
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
        "db", lambda: None, declaration_path="/project/conftest.py"
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
        "val", typed_fixture, declaration_path="/project/conftest.py"
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
    parent_def = helpers.make_fixture_def("db", declaration_path="conftest.py")
    child_def = helpers.make_fixture_def("db", declaration_path="tests/conftest.py")

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
    defn = helpers.make_fixture_def("db", declaration_path="conftest.py")

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
    first = helpers.make_fixture_def("db", declaration_path="conftest.py")
    second = helpers.make_fixture_def("db", declaration_path="conftest.py")

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
        "conn", namespace="db", declaration_path="/path/conftest.py"
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
        helpers.make_fixture_def("db", declaration_path="conftest.py")
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
        helpers.make_fixture_def("a", declaration_path="conftest.py"),
        helpers.make_fixture_def("b", declaration_path="conftest.py"),
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
            "db", lambda: "root", declaration_path="root/conftest.py"
        ),
        helpers.make_fixture_def(
            "db", lambda: "leaf", declaration_path="root/sub/conftest.py"
        ),
    )
    defs = reg.all_defs("db")
    assert len(defs) == 2, (
        "all_defs should return all registered FixtureDefs for a name"
    )
    assert defs[0].declaration_path == "root/conftest.py", (
        "first entry should be the root conftest definition"
    )
    assert defs[1].declaration_path == "root/sub/conftest.py", (
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
        "db_session", DBSession, declaration_path="/c.py", fixture_type=DBSession
    )
    reg = helpers.make_registry(defn)

    result = reg.resolve(DBSession)
    assert result.name == "db_session", "should resolve the only match by type"

    result2 = reg.resolve(DBSession, qualifier="anything")
    assert result2.name == "db_session", "qualifier ignored when type is unique"


def test_registry_resolve_by_type_ambiguous_with_qualifier() -> None:
    """Two fixtures of same type -- qualifier disambiguates."""
    dev = helpers.make_fixture_def(
        "dev_db", DBSession, declaration_path="/c.py", fixture_type=DBSession
    )
    prod = helpers.make_fixture_def(
        "prod_db", DBSession, declaration_path="/c.py", fixture_type=DBSession
    )
    reg = helpers.make_registry(dev, prod)

    result = reg.resolve(DBSession, qualifier="dev_db")
    assert result.name == "dev_db", "qualifier should select dev_db"


def test_registry_resolve_ambiguous_no_match() -> None:
    """Two fixtures of same type, unknown qualifier -- AmbiguousFixtureError."""
    dev = helpers.make_fixture_def(
        "dev_db", DBSession, declaration_path="/c.py", fixture_type=DBSession
    )
    prod = helpers.make_fixture_def(
        "prod_db", DBSession, declaration_path="/c.py", fixture_type=DBSession
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
        "db", lambda: "root", declaration_path="/conftest.py", fixture_type=DBSession
    )
    leaf = helpers.make_fixture_def(
        "db",
        lambda: "leaf",
        declaration_path="/tests/conftest.py",
        fixture_type=DBSession,
    )
    reg = helpers.make_registry(root, leaf)

    result = reg.resolve(DBSession)
    assert result.declaration_path == "/tests/conftest.py", (
        "leaf conftest should override root"
    )


# ── FixtureRegistry: anchor-aware filtered queries (B1, #1713) ────────────────


def _module_def(
    name: str,
    namespace: str,
    anchor: str,
    *,
    autouse: bool = False,
    scope: FixtureScope = FixtureScope.EACH,
) -> FixtureDef[Any]:
    """A package-anchored FixtureDef, the only source B1 constrains."""
    return FixtureDef(
        name=name,
        fixture_type=object,
        scope=scope,
        source=ModuleSource(
            func=lambda: None,
            defining_module_path=f"{anchor}/__fixtures__.py",
            anchor_package_path=anchor,
            lifetime=Lifetime.FUNCTION,
        ),
        autouse=autouse,
        namespace=namespace,
    )


# ── FixtureRegistry: shadow notice is visibility-aware (#1766) ────────────────


def _inline_def(name: str, module_path: str) -> FixtureDef[Any]:
    """An inline FixtureDef — the anchor is the test module itself (Rule 1).

    ``anchor_package_path == defining_module_path`` is exactly what marks a
    declaration inline, so the two arguments are deliberately the same value.
    """
    return FixtureDef(
        name=name,
        fixture_type=object,
        scope=FixtureScope.EACH,
        source=ModuleSource(
            func=lambda: None,
            defining_module_path=module_path,
            anchor_package_path=module_path,
            lifetime=Lifetime.FUNCTION,
        ),
        namespace=module_path.rsplit("/", 1)[-1].removesuffix(".py"),
    )


def _registration_notices(diags: list[Diagnostic]) -> list[str]:
    """Messages of the fixture-registration diagnostics, in emission order."""
    return [d.message for d in diags if d.context == "fixture registration"]


def test_disjoint_package_anchors_emit_no_shadow_notice(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """`tests/api/v1` and `tests/admin/v1` are mutually invisible (#1766)."""
    # Arrange
    registry = FixtureRegistry()

    # Act
    registry.register(_module_def("thing", "v1", "/t/api/v1"))
    registry.register(_module_def("thing", "v1", "/t/admin/v1"))

    # Assert
    notices = _registration_notices(diag_collector)
    assert notices == [], (
        "no test can resolve both declarations, so neither overrides the other "
        "— a notice here tells the user to rename a fixture that is not in "
        f"conflict with anything; got {notices}"
    )


def test_disjoint_inline_anchors_emit_no_shadow_notice(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """Two test modules declaring the same inline fixture name (#1766)."""
    # Arrange
    registry = FixtureRegistry()

    # Act
    registry.register(_inline_def("client", "/t/test_alpha.py"))
    registry.register(_inline_def("client", "/t/test_beta.py"))

    # Assert
    notices = _registration_notices(diag_collector)
    assert notices == [], (
        "an inline declaration reaches only its own module, so two modules "
        "picking the same fixture name never collide. This is the volume case: "
        "n modules sharing a name would otherwise emit n-1 false notices; "
        f"got {notices}"
    )


def test_the_nearer_package_is_named_as_the_shadower(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """Direction follows resolution, not registration order (#1766).

    Registration walks deepest-first, so the rootdir declaration arrives last —
    but ``_deepest_visible`` hands tests in ``/t/api`` the *api* declaration.
    A message keyed on arrival order names the loser as the winner.
    """
    # Arrange
    registry = FixtureRegistry()

    # Act
    registry.register(_module_def("thing", "api", "/t/api"))
    registry.register(_module_def("thing", "t", "/t"))

    # Assert
    notices = _registration_notices(diag_collector)
    assert len(notices) == 1, (
        f"the two anchors overlap, so exactly one real clash exists; got {notices}"
    )
    assert notices[0].index("/t/api/__fixtures__.py") < notices[0].index(
        "/t/__fixtures__.py"
    ), (
        "the shadower is named first in the message, and the shadower is the "
        "def resolution actually returns — reporting the rootdir declaration "
        "as the shadower sends the user to edit the file that already lost: "
        f"{notices[0]!r}"
    )


def test_an_anchored_shadower_reports_the_subtree_it_shadows_within(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """A package declaration overrides a conftest one only inside its own tree."""
    # Arrange
    registry = FixtureRegistry()
    conftest = helpers.make_fixture_def(
        "thing", namespace="db", declaration_path="/t/conftest.py"
    )

    # Act
    registry.register(conftest)
    registry.register(_module_def("thing", "api", "/t/api"))

    # Assert
    notices = _registration_notices(diag_collector)
    assert len(notices) == 1, (
        f"a conftest def is ambient, so the package def does clash; got {notices}"
    )
    assert notices[0].endswith("within /t/api"), (
        "without the subtree the notice reads as a run-wide override, and the "
        "user goes looking for a break that does not exist — tests outside "
        f"/t/api still resolve the conftest definition: {notices[0]!r}"
    )


def test_an_unanchored_shadower_reports_no_subtree(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """Conftest-over-conftest is run-wide, so there is no subtree to name."""
    # Arrange
    registry = FixtureRegistry()

    # Act
    registry.register(helpers.make_fixture_def("db", declaration_path="/t/conftest.py"))
    registry.register(
        helpers.make_fixture_def("db", declaration_path="/t/unit/conftest.py")
    )

    # Assert
    notices = _registration_notices(diag_collector)
    assert len(notices) == 1, f"one override, one notice; got {notices}"
    assert "within" not in notices[0], (
        "a conftest fixture is ambient, so appending a subtree would claim a "
        "boundary the legacy API does not have — and this message is the one "
        f"docs and users already know verbatim: {notices[0]!r}"
    )


def test_a_spanning_declaration_clashes_with_every_disjoint_prior(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """Two mutually invisible priors are both real clashes for a def above them.

    Comparing only the last-registered prior was harmless while every pair
    emitted — the chain covered everything. Once disjoint pairs are suppressed
    it is not: `/t/admin/v1` is never compared and its override goes unreported.
    """
    # Arrange
    registry = FixtureRegistry()

    # Act
    registry.register(_module_def("thing", "v1", "/t/admin/v1"))
    registry.register(_module_def("thing", "v1", "/t/api/v1"))
    registry.register(_module_def("thing", "t", "/t"))

    # Assert
    notices = _registration_notices(diag_collector)
    assert len(notices) == 2, (
        "the rootdir declaration is overridden in both subtrees and neither "
        "subtree shadows the other, so both are maximal and both must be "
        f"reported; got {notices}"
    )
    assert all("/t/__fixtures__.py" in notice for notice in notices), (
        f"every notice here is about the rootdir def being shadowed; got {notices}"
    )


def test_a_conftest_chain_reports_consecutive_pairs_only(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """Three conftests declaring one name emit two notices, not three.

    Every unanchored pair overlaps, so an all-pairs rule would add a
    root-versus-leaf notice that says nothing the chain has not already said —
    noise added by a fix whose purpose is removing noise.
    """
    # Arrange
    registry = FixtureRegistry()

    # Act
    for path in ("/t/conftest.py", "/t/unit/conftest.py", "/t/unit/api/conftest.py"):
        registry.register(helpers.make_fixture_def("db", declaration_path=path))

    # Assert
    notices = _registration_notices(diag_collector)
    assert len(notices) == 2, (
        "a three-link chain has two overrides; reporting the transitive pair "
        f"as well would change long-standing conftest output; got {notices}"
    )


def test_many_disjoint_inline_declarations_stay_silent(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """The suppression path at the scale a real suite reaches (#1853).

    Every other test here registers at most three defs, which exercises the
    predicate but not the scan around it. `register` compares each incoming def
    against every prior sharing the name, so this is the shape whose cost grows
    — and one module per inline fixture name is the ordinary case, not a
    pathological one.
    """
    # Arrange
    registry = FixtureRegistry()
    declarations = [_inline_def("client", f"/t/pkg/test_mod{i}.py") for i in range(200)]

    # Act
    for declaration in declarations:
        registry.register(declaration)

    # Assert
    notices = _registration_notices(diag_collector)
    assert notices == [], (
        "200 test modules each declaring their own 'client' collide with "
        "nothing — every anchor is a distinct file. One notice here means the "
        f"scan reintroduced per-pair reporting; got {len(notices)}"
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
        source=FrameworkSource(func=lambda: None, origin="/t/conftest.py"),
    )
    second = FixtureDef(
        name="conn",
        fixture_type=object,
        scope=FixtureScope.EACH,
        source=FrameworkSource(func=lambda: None, origin="/t/api/conftest.py"),
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
        "legacy_setup", autouse=True, declaration_path="/t/alpha/conftest.py"
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
    registry.register(
        helpers.make_fixture_def("setup", declaration_path="/t/conftest.py")
    )
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
        helpers.make_fixture_def("legacy_setup", autouse=True, declaration_path="/c.py")
    )

    # Act
    names = {defn.name for defn in registry.get_autouse(None)}

    # Assert
    assert names == {"api_setup", "legacy_setup"}, (
        "find_unused_fixtures seeds used-ness from every autouse fixture in "
        "the run — filtering the catalog query would let a fixture used only "
        "inside its own boundary be flagged as unused"
    )


def test_autouse_suppression_by_an_anchored_def_is_boundary_local() -> None:
    """An anchored def suppresses an ambient autouse only inside its boundary."""
    # Arrange — ambient autouse FIRST, anchored non-autouse LAST, so a defs[-1]
    # implementation sees a non-autouse def and suppresses the name run-wide.
    registry = FixtureRegistry()
    ambient = helpers.make_fixture_def(
        "setup", autouse=True, declaration_path="/t/conftest.py"
    )
    registry.register(ambient)
    registry.register(_module_def("setup", "api", "/t/api"))

    # Act
    inside = list(registry.get_autouse("/t/api/test_a.py"))
    outside = list(registry.get_autouse("/t/test_b.py"))

    # Assert
    assert inside == [], (
        "the anchored non-autouse def is the deepest visible def inside /t/api, "
        "so it overrides the ambient autouse there — losing this half lets a "
        "package that deliberately opts out of an ambient autouse silently get "
        "it back"
    )
    assert outside == [ambient], (
        "outside /t/api the anchored def is invisible, so the ambient autouse "
        "is the deepest visible candidate and still fires — losing this half is "
        "the defs[-1] regression (#1774): one anchored def anywhere in the tree "
        "disables an ambient autouse for tests that cannot even see it"
    )


def test_autouse_fires_widest_lifetime_first() -> None:
    """Firing order follows the tier, not the order fixtures registered (#1716).

    Registration order here is deliberately narrow-then-wide: that is the
    configuration a dict-iteration implementation gets wrong.
    """
    # Arrange
    registry = FixtureRegistry()
    registry.register(
        _module_def("narrow", "pkg", "/t/pkg", autouse=True, scope=FixtureScope.EACH)
    )
    registry.register(
        _module_def("wide", "pkg", "/t/pkg", autouse=True, scope=FixtureScope.PACKAGE)
    )

    # Act
    names = [defn.name for defn in registry.get_autouse("/t/pkg/test_a.py")]

    # Assert
    assert names == ["wide", "narrow"], (
        "a package-lifetime autouse fixture must fire before a function-lifetime "
        "one, so setup nests the same way teardown already does on the scope "
        f"stacks; got {names}"
    )


def test_autouse_order_is_stable_within_a_tier() -> None:
    """Same-tier autouse fixtures keep registration order (#1716).

    The sort key is the tier alone, and Python's sort is stable — which is what
    lets FixtureDef stay frozen with no registration-index field.
    """
    # Arrange
    registry = FixtureRegistry()
    registry.register(
        _module_def("first", "pkg", "/t/pkg", autouse=True, scope=FixtureScope.EACH)
    )
    registry.register(
        _module_def("second", "pkg", "/t/pkg", autouse=True, scope=FixtureScope.EACH)
    )

    # Act
    names = [defn.name for defn in registry.get_autouse("/t/pkg/test_a.py")]

    # Assert
    assert names == ["first", "second"], (
        "an unstable sort would make autouse order depend on the sort "
        f"implementation rather than on the user's declarations; got {names}"
    )


def test_autouse_ranks_the_session_scope_between_process_and_package() -> None:
    """The ``session`` rung is reachable and ranked, not dead weight (#1716).

    No ``Lifetime`` maps to ``FixtureScope.SESSION`` — it holds the builtins,
    which are never autouse — so nothing in this repo's own declarations can
    reach that rank. A **plugin** can: ``_register_plugin_fixtures`` builds its
    ``FixtureDef`` with ``scope=FixtureScope(provider_scope)`` from an arbitrary
    provider string *and* ``autouse=provider_autouse``, in the same call.

    Without this test the entry is an untested branch in a rank map, which is
    exactly the shape a later cleanup reads as speculative and deletes — and
    ``_SCOPE_RANK`` must stay total, because a missing member is a ``KeyError``
    on the autouse path rather than a merely wrong order.
    """
    # Arrange — mirrors _register_plugin_fixtures: a plugin-sourced def is
    # unanchored, so it is ambient and visible everywhere.
    registry = FixtureRegistry()

    def _plugin_def(name: str, scope: FixtureScope) -> FixtureDef[Any]:
        return FixtureDef(
            name=name,
            fixture_type=object,
            scope=scope,
            source=PluginSource(provider=object(), plugin_module="acme.plugin"),
            autouse=True,
        )

    registry.register(_plugin_def("pkg_wide", FixtureScope.PACKAGE))
    registry.register(_plugin_def("task_wide", FixtureScope.SESSION))
    registry.register(_plugin_def("proc_wide", FixtureScope.PROCESS))

    # Act
    names = [defn.name for defn in registry.get_autouse("/t/test_a.py")]

    # Assert
    assert names == ["proc_wide", "task_wide", "pkg_wide"], (
        "session ranks between process and package because that is where its "
        "boundary sits — it drains at end_task, inside end_process and outside "
        f"a package's subtree; got {names}"
    )


def test_autouse_index_ignores_names_with_no_autouse_def() -> None:
    """The candidate index holds only autouse-capable names (#1716).

    get_autouse runs once per test, so iterating every registered fixture makes
    the loop scale with the suite rather than with the feature.
    """
    # Arrange
    registry = FixtureRegistry()
    registry.register(_module_def("plain", "pkg", "/t/pkg"))
    registry.register(_module_def("auto", "pkg", "/t/pkg", autouse=True))

    # Act
    indexed = list(registry._autouse_names)  # noqa: SLF001 — the index is a perf invariant with no public reader

    # Assert
    assert indexed == ["auto"], (
        "a name with no autouse def must never enter the index — it would be "
        f"resolved once per test for a result that is always discarded; got {indexed}"
    )


def test_notice_says_autouse_was_suppressed(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """Shadowing an autouse fixture stops it firing — say so (#1716).

    Inert before slice 9, because no anchored def could be autouse. The moment
    one can, a name collision between two unrelated fixtures silently disables
    an ancestor's autouse for a whole subtree.
    """
    # Arrange
    registry = FixtureRegistry()

    # Act
    registry.register(_module_def("setup", "t", "/t", autouse=True))
    registry.register(_module_def("setup", "api", "/t/api"))

    # Assert
    notices = _registration_notices(diag_collector)
    assert len(notices) == 1, (
        f"the two anchors overlap, so exactly one real clash exists; got {notices}"
    )
    assert "no longer fires" in notices[0], (
        "the notice must name the consequence, not just the fact of shadowing "
        "— 'shadows definition in X' reads as a naming nit when what actually "
        f"happened is that a fixture stopped running: {notices[0]!r}"
    )


def test_notice_stays_quiet_when_no_autouse_is_lost(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """Autouse-shadows-autouse loses no firing, so no suppression clause (#1716)."""
    # Arrange
    registry = FixtureRegistry()

    # Act
    registry.register(_module_def("setup", "t", "/t", autouse=True))
    registry.register(_module_def("setup", "api", "/t/api", autouse=True))

    # Assert
    notices = _registration_notices(diag_collector)
    assert len(notices) == 1, (
        f"the two anchors overlap, so exactly one real clash exists; got {notices}"
    )
    assert "no longer fires" not in notices[0], (
        "the deeper declaration is autouse too, so firing continues — claiming "
        "suppression here sends the user hunting for a fixture that still runs: "
        f"{notices[0]!r}"
    )


# ── module_source_declarations: the ADR-0009 enforcement source (#1859) ───────


def _declared_at_module_level() -> str:
    """A real def, so `co_firstlineno` has a stable line to report."""
    return "v"


def _module_source_def(
    name: str, anchor: str, lifetime: Lifetime, namespace: str
) -> FixtureDef[Any]:
    """A ModuleSource def anchored at *anchor*, declaring *lifetime*."""
    return FixtureDef(
        name=name,
        fixture_type=str,
        scope=FixtureScope.PACKAGE,
        source=ModuleSource(
            func=_declared_at_module_level,
            defining_module_path=f"{anchor}/__fixtures__.py",
            anchor_package_path=anchor,
            lifetime=lifetime,
        ),
        namespace=namespace,
    )


def test_module_source_declarations_reports_name_lifetime_and_lineno() -> None:
    """The scheduler and ADR-0009 Rule 4 read this instead of the prescan AST."""
    # Arrange
    registry = helpers.make_registry()
    registry.register(_module_source_def("conn", "/proj/pkg", Lifetime.PACKAGE, "pkg"))

    # Act
    declarations = registry.module_source_declarations("/proj/pkg/__fixtures__.py")

    # Assert
    expected_line = _declared_at_module_level.__code__.co_firstlineno
    assert declarations == (("conn", "package", expected_line),), (
        f"this tuple is the only thing standing between an aliased declaration "
        f"and the scheduler: the lifetime string drives package co-location and "
        f"the Rule 4 rootdir check, and the lineno reaches the user in the "
        f"co-location warning; got {declarations!r}"
    )


def test_module_source_declarations_excludes_other_files() -> None:
    """Each declaration home asks only about its own file."""
    # Arrange
    registry = helpers.make_registry()
    registry.register(_module_source_def("mine", "/proj/pkg", Lifetime.PACKAGE, "pkg"))
    registry.register(
        _module_source_def("theirs", "/proj/other", Lifetime.PACKAGE, "other")
    )

    # Act
    declarations = registry.module_source_declarations("/proj/pkg/__fixtures__.py")

    # Assert
    assert [name for name, _, _ in declarations] == ["mine"], (
        f"a home that claimed another anchor's declarations would co-locate a "
        f"subtree that declared nothing, disabling parallelism for directories "
        f"the user never annotated; got {declarations!r}"
    )


def test_module_source_declarations_excludes_non_module_sources() -> None:
    """Conftest and plugin fixtures are not ADR-0009 declarations."""
    # Arrange
    registry = helpers.make_registry(
        helpers.make_fixture_def("db", declaration_path="/c.py")
    )

    # Act
    declarations = registry.module_source_declarations("/c.py")

    # Assert
    assert declarations == (), (
        f"only ModuleSource carries an anchor and a declared lifetime; counting "
        f"a FrameworkSource here would apply a home-kind rule to a source that "
        f"has no home; got {declarations!r}"
    )


def test_module_source_declarations_sees_a_shadowed_declaration() -> None:
    """An inventory question, not a resolution one — every def counts."""
    # Arrange — the same name declared at a package and at a nested package.
    # `_by_name["conn"]` is [outer, inner], so `defs[-1]` is the inner one.
    registry = helpers.make_registry()
    registry.register(_module_source_def("conn", "/proj/pkg", Lifetime.PACKAGE, "pkg"))
    registry.register(
        _module_source_def("conn", "/proj/pkg/sub", Lifetime.PACKAGE, "sub")
    )

    # Act
    declarations = registry.module_source_declarations("/proj/pkg/__fixtures__.py")

    # Assert
    assert [name for name, _, _ in declarations] == ["conn"], (
        f"the outer declaration still exists even though a deeper package "
        f"shadows the name for resolution. Reading only the most-local def "
        f"would drop it, and the outer package would stop co-locating its "
        f"subtree — the exactly-once guarantee failing silently for any suite "
        f"that reuses a fixture name in a nested package; got {declarations!r}"
    )


# ── PluginModuleSource (#1717) ────────────────────────────────────────────────


def _plugin_conn() -> int:
    """Stand-in plugin fixture factory."""
    return 1


def _make_plugin_def(namespace: str = "oxi_pg") -> FixtureDef[int]:
    """A FixtureDef as the plugin registrar will build it."""
    return FixtureDef(
        name="conn",
        fixture_type=int,
        scope=FixtureScope.MODULE,
        source=PluginModuleSource(
            func=_plugin_conn,
            defining_module_path="/site-packages/oxi_pg/__fixtures__.py",
            plugin_module="oxi_pg",
            lifetime=Lifetime.MODULE,
        ),
        namespace=namespace,
    )


def test_plugin_module_source_is_b1_exempt() -> None:
    """A plugin fixture carries no anchor, so B1 never filters it."""
    defn = _make_plugin_def()

    assert defn.anchor is None, (
        "an anchor would bind the fixture to its site-packages directory, which "
        "no user test can be under — every plugin fixture would be invisible"
    )


def test_plugin_module_source_is_visible_from_any_module() -> None:
    """Ambient means reachable from every test in the run, at any depth."""
    defn = _make_plugin_def()

    assert defn.is_visible_from("/proj/tests/deep/nested/test_a.py"), (
        "plugin fixtures are ambient in every fixture session (ADR-0009 Rule 6); "
        "False here means the ancestor half of 'ambient ancestor' regressed"
    )


def test_plugin_module_source_scores_zero_in_the_shadow_order() -> None:
    """A plugin fixture's shadow rank is exactly 0, not merely small.

    Asserting the value rather than a comparison against some particular user
    anchor: a `< deep_anchor` test passes for any rank below that anchor's
    depth, so it cannot tell "unanchored scores 0" from "scores 3". Mutating
    the 0 to a 1 left such a test green.
    """
    plugin = _make_plugin_def(namespace="api")

    rank, _index = _shadow_order(plugin, 0)

    assert rank == 0, (
        "unanchored sources score 0 so they lose to *any* anchored declaration "
        "that can see them; a non-zero rank makes a plugin outrank a user's own "
        f"shallow declaration, silently changing their suite — got {rank}"
    )


def test_plugin_module_source_loses_to_the_shallowest_user_declaration() -> None:
    """A user's anchored declaration outranks a plugin fixture at any depth."""
    plugin = _make_plugin_def(namespace="api")
    # The shallowest anchor that exists — depth 1. Any deeper anchor would let a
    # mutated rank of 1, 2 or 3 still "lose" and keep this test green.
    shallowest_user = FixtureDef(
        name="conn",
        fixture_type=int,
        scope=FixtureScope.MODULE,
        source=ModuleSource(
            func=_plugin_conn,
            defining_module_path="/__fixtures__.py",
            anchor_package_path="/",
            lifetime=Lifetime.MODULE,
        ),
        namespace="api",
    )

    assert _shadow_order(plugin, 0) < _shadow_order(shallowest_user, 0), (
        "installing a plugin must never take precedence over a user's own "
        "declaration — the user's suite would silently change behaviour"
    )


def test_plugin_module_source_declaration_path_names_the_plugin() -> None:
    """declaration_path renders as <plugin:module>, not the site-packages path."""
    defn = _make_plugin_def()

    assert defn.declaration_path == "<plugin:oxi_pg>", (
        "this string is what the shadow NOTICE prints; a 90-character "
        "site-packages path makes 'shadows definition in ...' unreadable"
    )


def test_plugin_module_source_exposes_its_callable() -> None:
    """The func property returns the factory, as for any user-declared fixture."""
    defn = _make_plugin_def()

    assert defn.func is _plugin_conn, (
        "the instantiator routes plugin-module fixtures through "
        "resolve_user_fixture, which needs the callable; without it every "
        "resolution raises AttributeError instead of building the fixture"
    )


def test_plugin_module_source_renders_in_query_output() -> None:
    """query_bridge names the owning plugin and carries the factory's docstring."""
    reg = helpers.make_registry(_make_plugin_def())

    entries = [e for e in fixture_entries(reg) if e["name"] == "conn"]

    assert entries, "the plugin fixture must appear in `oxitest query fixtures`"
    assert entries[0]["source"] == "<plugin:oxi_pg>", (
        "an unhandled variant falls to '<unknown>', which tells a user nothing "
        f"about which installed package owns the fixture — got {entries[0]['source']!r}"
    )
    assert entries[0]["description"] == "Stand-in plugin fixture factory.", (
        "the factory's docstring is the only description a user gets for a "
        f"fixture they cannot open in their own tree — got "
        f"{entries[0]['description']!r}"
    )


# ── #1848: arrangement inputs are declared, not derived from a tier ──────────


def test_module_lifetime_names_lists_only_the_module_tier() -> None:
    """The warning's reader must not read FixtureDef.arranges, which #1848 deletes."""
    registry = helpers.make_registry(
        helpers.make_fixture_def("per_test", scope=FixtureScope.EACH),
        helpers.make_fixture_def("per_module", scope=FixtureScope.MODULE),
        helpers.make_fixture_def("per_process", scope=FixtureScope.PROCESS),
    )

    names = registry.module_lifetime_names()

    assert names == ("per_module",), (
        "the wide-lifetime warning names module-tier fixtures only; a process-tier "
        "fixture is built once per worker and is not what the warning is about"
    )


def test_arranged_fixture_groups_ignores_the_lifetime_tier() -> None:
    """#1848: a component is what @oxi.arrange names, at any tier."""
    registry = helpers.make_registry(
        helpers.make_fixture_def("per_module", scope=FixtureScope.MODULE),
        helpers.make_fixture_def("per_process", scope=FixtureScope.PROCESS),
    )

    arranged_only = registry.arranged_fixture_groups(frozenset({"per_process"}))
    nothing_arranged = registry.arranged_fixture_groups(frozenset())

    assert arranged_only == (("per_process",),), (
        "a process-tier fixture named by @oxi.arrange forms a component; before "
        "#1848 the tier filter discarded it and the decorator was a silent no-op"
    )
    assert nothing_arranged == (), (
        "with nothing arranged there is no component, however wide the tiers — "
        "this is the retired inference, and its absence is the point of #1848"
    )
