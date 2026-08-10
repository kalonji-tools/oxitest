"""Tests for NamespaceProxy, FixturesProxy, OxiNamespaceProxy, and their integration."""

from __future__ import annotations

import oxitest
from oxitest import Fixture, LogCapture, Patcher, StdCapture, TempDir
from oxitest._bridge._errors import BoundaryError, FixtureNotFoundError
from oxitest._bridge._fixture_registry import (
    ConftestSource,
    FixtureDef,
    FixtureRegistry,
    FixtureScope,
    ModuleSource,
)
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._lifetime import Lifetime
from oxitest._bridge._read_fixtures import _fixtures_registry_var, _FixturesProxy
from oxitest._bridge._test_meta import TestMeta
from oxitest._bridge.proxy import FrozenProxy
from oxitest._bridge.proxy_ns import FixturesProxy, NamespaceProxy, OxiNamespaceProxy
from oxitest._bridge.result import PassedResult
from tests import helpers

# ── NamespaceProxy ─────────────────────────────────────────────────────────


def _meta(module_path: str, fn_name: str = "test_fake") -> TestMeta:
    """A TestMeta for a proxy under test.

    ``FixturesProxy`` and ``OxiNamespaceProxy`` take the running test's whole
    ``TestMeta`` rather than a module path plus a name: a builtin resolved
    through the proxy reads the identity from it, and the two fields they used
    to take are the two that are *not* that identity (#1874).
    """
    return TestMeta(
        module_path=module_path,
        fn_name=fn_name,
        node_id=f"{module_path}::{fn_name}",
    )


def test_namespace_proxy_resolves_fixture() -> None:
    """Attribute access on NamespaceProxy resolves and returns the fixture value."""
    session = helpers.make_session(
        helpers.make_fixture_def("conn", lambda: "db-val", namespace="db")
    )
    proxy = NamespaceProxy("db", session, "/fake/test.py", [], test_is_async=True)
    assert proxy.conn == "db-val", (
        f"NamespaceProxy('db').conn should resolve to 'db-val', got {proxy.conn!r}"
    )


def test_namespace_proxy_is_lazy() -> None:
    """NamespaceProxy defers the fixture factory call until first attribute access."""
    called = []

    def make_conn() -> str:
        called.append(1)
        return "val"

    session = helpers.make_session(
        helpers.make_fixture_def("conn", make_conn, namespace="db")
    )
    proxy = NamespaceProxy("db", session, "/fake/test.py", [], test_is_async=True)
    assert called == [], "fixture factory must not be called before attribute access"
    _ = proxy.conn
    assert called == [1], (
        f"fixture factory must be called exactly once on first access, called "
        f"{len(called)} times"
    )


def test_namespace_proxy_isolates_namespaces() -> None:
    """Two NamespaceProxy instances for different namespaces resolve the same name."""
    session = helpers.make_session(
        helpers.make_fixture_def("conn", lambda: "db-conn", namespace="db"),
        helpers.make_fixture_def("conn", lambda: "http-conn", namespace="http"),
    )
    db_proxy = NamespaceProxy("db", session, "/fake/test.py", [], test_is_async=True)
    http_proxy = NamespaceProxy(
        "http", session, "/fake/test.py", [], test_is_async=True
    )
    assert db_proxy.conn == "db-conn", (
        f"db namespace proxy should resolve conn to 'db-conn', got {db_proxy.conn!r}"
    )
    assert http_proxy.conn == "http-conn", (
        f"http namespace proxy should resolve conn to 'http-conn', got "
        f"{http_proxy.conn!r}"
    )


# ── FixturesProxy ──────────────────────────────────────────────────────────


def test_fixtures_proxy_getattr_returns_namespace_proxy() -> None:
    """Accessing a user-defined namespace on FixturesProxy returns a NamespaceProxy."""
    session = helpers.make_session(
        helpers.make_fixture_def("conn", lambda: 1, namespace="db")
    )
    proxy = FixturesProxy(session, _meta("/fake/test.py"), [], test_is_async=True)
    ns = proxy.db
    assert isinstance(ns, NamespaceProxy), (
        f"FixturesProxy.db should return a NamespaceProxy, got {type(ns).__name__}"
    )


def test_fixtures_proxy_getattr_returns_oxi_proxy() -> None:
    """Accessing the 'oxi' attribute on FixturesProxy returns an OxiNamespaceProxy."""
    session = helpers.make_session()
    proxy = FixturesProxy(session, _meta("/fake/test.py"), [], test_is_async=True)
    oxi = proxy.oxi
    assert isinstance(oxi, OxiNamespaceProxy), (
        f"FixturesProxy.oxi should return an OxiNamespaceProxy, got "
        f"{type(oxi).__name__}"
    )


def test_fixtures_proxy_unknown_namespace_raises() -> None:
    """Accessing an unregistered namespace on FixturesProxy raises FixtureNotFoundError.

    Was AttributeError before #1713 — a silent False from hasattr(fx, name) is
    how a boundary violation becomes a mystery, so this segment now raises the
    same taxonomy as the rest of fixture lookup.
    """
    session = helpers.make_session()
    proxy = FixturesProxy(session, _meta("/fake/test.py"), [], test_is_async=True)
    with oxitest.raises(FixtureNotFoundError, match="unknown_ns"):
        _ = proxy.unknown_ns


def _api_session() -> FixtureSession:
    """A session whose only fixture is anchored at /t/api.

    Replicated from test_fixture_session.py's helper of the same name and
    shape — python/tests has no cross-module imports between test files, so
    this mirrors the fixture rather than sharing it.
    """
    registry = FixtureRegistry()
    registry.register(
        FixtureDef(
            name="api_conn",
            fixture_type=object,
            scope=FixtureScope.EACH,
            source=ModuleSource(
                func=object,
                defining_module_path="/t/api/__fixtures__.py",
                anchor_package_path="/t/api",
                lifetime=Lifetime.FUNCTION,
            ),
            namespace="api",
        )
    )
    return FixtureSession(registry)


def test_unknown_segment_names_the_modern_declaration_route() -> None:
    """The stale hint pointed at conftest.py, which slice 1 displaced."""
    # Arrange
    session = _api_session()
    proxy = FixturesProxy(session, _meta("/t/api/test_a.py"), [], test_is_async=True)

    # Act
    with oxitest.raises(FixtureNotFoundError) as exc_info:
        _ = proxy.nope

    # Assert
    message = str(exc_info.value)
    assert "conftest.py" not in message, (
        "the old message told users to define a Fixtures() instance in "
        "conftest.py — not the primary declaration route since slice 1, so it "
        "sends them to the wrong file"
    )
    assert "nope" in message, (
        "the message must name the segment the user actually typed, or they "
        "cannot tell which part of fx.nope.x was wrong"
    )


def test_an_unreachable_segment_is_inert_until_a_leaf_is_touched() -> None:
    """Segment access is lazy; the boundary verdict belongs to the leaf.

    Refusing at the segment would mean never learning WHICH fixture was wanted,
    and BoundaryError has to name the fixture's anchor.
    """
    # Arrange
    session = _api_session()
    proxy = FixturesProxy(
        session, _meta("/t/admin/test_admin.py"), [], test_is_async=True
    )

    # Act — reaching the segment from outside its package must not raise
    namespace_proxy = proxy.api

    # Assert
    assert namespace_proxy is not None, (
        "a segment known anywhere must yield a proxy even from outside its "
        "boundary; raising here would strand the diagnostic without the leaf "
        "name it needs to report the fixture's anchor"
    )
    with oxitest.raises(BoundaryError):
        _ = namespace_proxy.api_conn


# ── OxiNamespaceProxy ──────────────────────────────────────────────────────


def test_oxi_proxy_tmp_injects_tempdir(
    tmp: TempDir, fixture_session: Fixture[FixtureSession]
) -> None:
    """oxi.tmp injects a TempDir builtin via the OxiNamespaceProxy."""
    proxy = OxiNamespaceProxy(fixture_session, _meta(str(tmp / "test.py")), [])
    result = proxy.tmp
    assert isinstance(result, TempDir), (
        f"oxi.tmp should inject a TempDir instance, got {type(result).__name__}"
    )


def test_oxi_proxy_cap_injects_stdcapture(
    tmp: TempDir, fixture_session: Fixture[FixtureSession]
) -> None:
    """oxi.cap injects a StdCapture builtin via the OxiNamespaceProxy."""
    teardowns: list = []
    proxy = OxiNamespaceProxy(fixture_session, _meta(str(tmp / "test.py")), teardowns)
    result = proxy.cap
    assert isinstance(result, StdCapture), (
        f"oxi.cap should inject a StdCapture instance, got {type(result).__name__}"
    )
    for td in reversed(teardowns):
        td()


def test_oxi_proxy_patch_injects_patcher(
    tmp: TempDir, fixture_session: Fixture[FixtureSession]
) -> None:
    """oxi.patch injects a Patcher builtin via the OxiNamespaceProxy."""
    proxy = OxiNamespaceProxy(fixture_session, _meta(str(tmp / "test.py")), [])
    result = proxy.patch
    assert isinstance(result, Patcher), (
        f"oxi.patch should inject a Patcher instance, got {type(result).__name__}"
    )


def test_oxi_proxy_log_injects_logcapture(
    tmp: TempDir, fixture_session: Fixture[FixtureSession]
) -> None:
    """oxi.log injects a LogCapture builtin via the OxiNamespaceProxy."""
    teardowns: list = []
    proxy = OxiNamespaceProxy(fixture_session, _meta(str(tmp / "test.py")), teardowns)
    result = proxy.log
    assert isinstance(result, LogCapture), (
        f"oxi.log should inject a LogCapture instance, got {type(result).__name__}"
    )
    for td in reversed(teardowns):
        td()


def test_oxi_proxy_unknown_raises_with_available_list(
    tmp: TempDir, fixture_session: Fixture[FixtureSession]
) -> None:
    """Unknown name on OxiNamespaceProxy raises AttributeError listing builtins."""
    proxy = OxiNamespaceProxy(fixture_session, _meta(str(tmp / "test.py")), [])
    with oxitest.raises(AttributeError, match="unknown") as exc:
        _ = proxy.unknown
    assert "tmp" in str(exc.value), (  # available list shown
        "AttributeError should show available builtins (including 'tmp'), "
        f"got: {exc.value}"
    )


# ── Shared fixtures ────────────────────────────────────────────────────────


def test_shared_fixture_accessed_via_namespace_is_frozen_proxy() -> None:
    """shared=True fixture accessed via fx.db.conn should be FrozenProxy-wrapped."""
    session = helpers.make_session(
        helpers.make_fixture_def(
            "conn",
            lambda: {"host": "localhost", "port": 5432},
            shared=True,
            namespace="db",
        )
    )
    proxy = FixturesProxy(session, _meta("/fake/test.py"), [], test_is_async=True)
    result = proxy.db.conn
    assert isinstance(result, FrozenProxy), (
        f"shared fixture accessed via namespace proxy should be wrapped in "
        f"FrozenProxy, got {type(result).__name__}"
    )


# ── OxiNamespaceProxy: ctx is removed ──────────────────────────────────────


def test_oxi_proxy_no_longer_resolves_ctx(
    fixture_session: Fixture[FixtureSession],
) -> None:
    """``fx.oxi.ctx`` is removed and refuses like any other unknown name.

    The replacements are ``oxi.current_test()`` from a test and
    ``ctx: TestContext`` from a fixture. Removed without a deprecation
    period per ADR-0015.
    """
    # Arrange
    proxy = OxiNamespaceProxy(fixture_session, _meta("/fake/test.py"), [])

    # Act
    with oxitest.raises(AttributeError) as exc_info:
        _ = proxy.ctx

    # Assert
    message = str(exc_info.value)
    assert "no builtin 'ctx'" in message, (
        "a removed name must fail through the same unknown-name path as any "
        f"other, so a user who tries it is told what happened; got: {message}"
    )
    advertised = message.split("Available: ")[1]
    assert "ctx" not in advertised, (
        "the Available list is what a user reads after the refusal, so it "
        f"must stop offering a name that cannot resolve; got: {advertised}"
    )


def test_fixtures_proxy_caches_namespace_proxy_on_repeated_access(
    fixture_session: Fixture[FixtureSession],
) -> None:
    """FixturesProxy.oxi caches and returns the same OxiNamespaceProxy each access."""
    proxy = FixturesProxy(
        fixture_session, _meta("/fake/test.py"), [], test_is_async=True
    )
    oxi1 = proxy.oxi
    oxi2 = proxy.oxi
    assert oxi1 is oxi2, (
        "FixturesProxy.oxi should return the same cached OxiNamespaceProxy on repeated "
        "access"
    )


def test_oxi_proxy_caches_builtin_on_repeated_access(
    fixture_session: Fixture[FixtureSession],
) -> None:
    """OxiNamespaceProxy caches builtins so repeated access returns the same object."""
    teardowns: list = []
    proxy = OxiNamespaceProxy(fixture_session, _meta("/fake/test.py"), teardowns)
    tmp1 = proxy.tmp
    tmp2 = proxy.tmp
    assert tmp1 is tmp2, (
        "OxiNamespaceProxy.tmp should return the same cached TempDir on repeated access"
    )
    for td in reversed(teardowns):
        td()


# ── Integration: full pipeline ──────────────────────────────────────────────


def test_full_pipeline_fx_namespace_access(tmp: TempDir) -> None:
    """Full pipeline: a namespaced declaration is reachable as ``fx.db.conn``.

    The namespace was the ``Fixtures()`` variable name until #1720. It is the
    anchor directory's basename now (ADR-0009 Rule 5), so the declaration lives
    in ``db/__fixtures__.py`` and the test sits inside that anchor to see it.
    """
    pkg = tmp / "db"
    pkg.mkdir()
    declarations = pkg / "__fixtures__.py"
    declarations.write_text(
        "from oxitest import fixture\n"
        "@fixture(lifetime='function')\n"
        "def conn() -> str:\n"
        "    return 'connected'\n",
        encoding="utf-8",
    )

    test_file = pkg / "test_ns.py"
    test_file.write_text(
        "import oxitest\n"
        "def test_access(fx: oxitest.Fixtures) -> None:\n"
        "    assert fx.db.conn == 'connected', 'namespaced access must resolve'\n",
        encoding="utf-8",
    )

    session = helpers.session_from_declarations(
        declarations, anchor_package_path=str(pkg)
    )

    result = helpers.run_test(str(test_file), "test_access", session)
    helpers.assert_result(
        result,
        PassedResult,
        why="fx.<namespace>.<name> must reach a declaration anchored at that"
        " directory -- the namespace is derived from the anchor, not from a"
        " registrar variable",
    )


def test_full_pipeline_fx_oxi_tmp(
    tmp: TempDir, fixture_session: Fixture[FixtureSession]
) -> None:
    """End-to-end: test accesses fx.oxi.tmp and writes to it."""
    test_file = tmp / "test_oxi.py"
    test_file.write_text(
        "import oxitest\n"
        "from pathlib import Path\n"
        "def test_oxi_tmp(fx: oxitest.Fixtures) -> None:\n"
        "    p = Path(str(fx.oxi.tmp)) / 'hello.txt'\n"
        "    p.write_text('hi')\n"
        "    assert p.read_text() == 'hi'\n",
        encoding="utf-8",
    )

    result = helpers.run_test(str(test_file), "test_oxi_tmp", fixture_session)
    helpers.assert_result(
        result,
        PassedResult,
        why="the built-in 'oxi' namespace must expose a usable tmp directory through"
        " fx -- the test writes a file and reads it back",
    )


def test_full_pipeline_two_namespaces_same_fixture_name(tmp: TempDir) -> None:
    """Two namespaces may hold the same fixture name and stay independent.

    The anchors are nested rather than siblings, and that is forced: a
    namespace is an anchor directory's basename, and the B1 boundary makes a
    fixture visible only inside its own anchor. Two siblings could never both
    be visible to one test, so the pair this asserts on can only exist as
    ``db/`` and ``db/cache/`` with the test in the inner one.
    """
    outer = tmp / "db"
    outer.mkdir()
    inner = outer / "cache"
    inner.mkdir()
    outer_decls = outer / "__fixtures__.py"
    outer_decls.write_text(
        "from oxitest import fixture\n"
        "@fixture(lifetime='function')\n"
        "def url() -> str:\n"
        "    return 'postgres://localhost'\n",
        encoding="utf-8",
    )
    inner_decls = inner / "__fixtures__.py"
    inner_decls.write_text(
        "from oxitest import fixture\n"
        "@fixture(lifetime='function')\n"
        "def url() -> str:\n"
        "    return 'redis://localhost'\n",
        encoding="utf-8",
    )

    test_file = inner / "test_two_ns.py"
    test_file.write_text(
        "import oxitest\n"
        "def test_two_namespaces(fx: oxitest.Fixtures) -> None:\n"
        "    assert fx.db.url == 'postgres://localhost', 'outer anchor'\n"
        "    assert fx.cache.url == 'redis://localhost', 'inner anchor'\n",
        encoding="utf-8",
    )

    reg = FixtureRegistry()
    for decls, anchor in ((outer_decls, outer), (inner_decls, inner)):
        for defn in helpers.session_from_declarations(
            decls, anchor_package_path=str(anchor)
        ).registry.all():
            reg.register(defn)
    session = FixtureSession(reg)

    result = helpers.run_test(str(test_file), "test_two_namespaces", session)
    helpers.assert_result(
        result,
        PassedResult,
        why="same fixture name under two namespaces must stay independent --"
        " one shadowing the other would make the qualified path meaningless",
    )


def test_fixtures_proxy_resolves_namespace_and_accessor(
    _tmp: oxitest.TempDir,
) -> None:
    """_FixturesProxy chains namespace access to a FixtureAccessor with metadata."""

    def _db() -> str:
        return "pg"

    reg = FixtureRegistry()
    reg.register(
        FixtureDef(
            name="conn",
            fixture_type=str,
            scope=FixtureScope.EACH,
            source=ConftestSource(func=_db, conftest_path="/conftest.py"),
            namespace="db",
        )
    )
    token = _fixtures_registry_var.set(reg)
    try:
        proxy = _FixturesProxy()
        accessor = proxy.db.conn
        assert hasattr(accessor, "_oxitest_fixture_name"), (
            "should return a FixtureAccessor with fixture name metadata"
        )
    finally:
        _fixtures_registry_var.reset(token)


def test_fixtures_proxy_raises_outside_session() -> None:
    """Accessing a _FixturesProxy namespace outside a session raises AttributeError."""
    token = _fixtures_registry_var.set(None)
    try:
        proxy = _FixturesProxy()
        with oxitest.raises(
            AttributeError, match="only available during a test session"
        ):
            _ = proxy.db
    finally:
        _fixtures_registry_var.reset(token)
