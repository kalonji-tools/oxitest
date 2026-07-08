"""Tests for NamespaceProxy, FixturesProxy, OxiNamespaceProxy, and their integration."""

from __future__ import annotations

from oxitest import Fixture, LogCapture, Patcher, StdCapture, TempDir, helpers
from oxitest._bridge._builtin_context import TestContext as OxiTestContext
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge.conftest_loader import load_fixtures_from_conftest
from oxitest._bridge.proxy import FrozenProxy
from oxitest._bridge.proxy_ns import FixturesProxy, NamespaceProxy, OxiNamespaceProxy

# ── NamespaceProxy ─────────────────────────────────────────────────────────


def test_namespace_proxy_resolves_fixture() -> None:
    """Attribute access on NamespaceProxy resolves and returns the fixture value."""
    session = helpers.common.make_session(
        helpers.common.make_fixture_def("conn", lambda: "db-val", namespace="db")
    )
    proxy = NamespaceProxy("db", session, "/fake/test.py", [])
    assert proxy.conn == "db-val", (
        f"NamespaceProxy('db').conn should resolve to 'db-val', got {proxy.conn!r}"
    )


def test_namespace_proxy_is_lazy() -> None:
    """NamespaceProxy defers the fixture factory call until first attribute access."""
    called = []

    def make_conn() -> str:
        called.append(1)
        return "val"

    session = helpers.common.make_session(
        helpers.common.make_fixture_def("conn", make_conn, namespace="db")
    )
    proxy = NamespaceProxy("db", session, "/fake/test.py", [])
    assert called == [], "fixture factory must not be called before attribute access"
    _ = proxy.conn
    assert called == [1], (
        f"fixture factory must be called exactly once on first access, called "
        f"{len(called)} times"
    )


def test_namespace_proxy_isolates_namespaces() -> None:
    """Two NamespaceProxy instances for different namespaces resolve the same name."""
    session = helpers.common.make_session(
        helpers.common.make_fixture_def("conn", lambda: "db-conn", namespace="db"),
        helpers.common.make_fixture_def("conn", lambda: "http-conn", namespace="http"),
    )
    db_proxy = NamespaceProxy("db", session, "/fake/test.py", [])
    http_proxy = NamespaceProxy("http", session, "/fake/test.py", [])
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
    session = helpers.common.make_session(
        helpers.common.make_fixture_def("conn", lambda: 1, namespace="db")
    )
    proxy = FixturesProxy(session, "/fake/test.py", [])
    ns = proxy.db
    assert isinstance(ns, NamespaceProxy), (
        f"FixturesProxy.db should return a NamespaceProxy, got {type(ns).__name__}"
    )


def test_fixtures_proxy_getattr_returns_oxi_proxy() -> None:
    """Accessing the 'oxi' attribute on FixturesProxy returns an OxiNamespaceProxy."""
    session = helpers.common.make_session()
    proxy = FixturesProxy(session, "/fake/test.py", [])
    oxi = proxy.oxi
    assert isinstance(oxi, OxiNamespaceProxy), (
        f"FixturesProxy.oxi should return an OxiNamespaceProxy, got "
        f"{type(oxi).__name__}"
    )


def test_fixtures_proxy_unknown_namespace_raises() -> None:
    """Accessing an unregistered namespace on FixturesProxy raises AttributeError."""
    session = helpers.common.make_session()
    proxy = FixturesProxy(session, "/fake/test.py", [])
    try:
        _ = proxy.unknown_ns
        msg = "expected AttributeError for unknown namespace 'unknown_ns'"
        raise AssertionError(msg)
    except AttributeError as exc:
        assert "unknown_ns" in str(exc), (
            f"AttributeError should mention 'unknown_ns', got: {exc}"
        )
        assert "conftest.py" in str(exc), (
            f"AttributeError should mention 'conftest.py' for guidance, got: {exc}"
        )


# ── OxiNamespaceProxy ──────────────────────────────────────────────────────


def test_oxi_proxy_tmp_injects_tempdir(
    tmp: TempDir, fixture_session: Fixture[FixtureSession]
) -> None:
    """oxi.tmp injects a TempDir builtin via the OxiNamespaceProxy."""
    proxy = OxiNamespaceProxy(fixture_session, str(tmp / "test.py"), [])
    result = proxy.tmp
    assert isinstance(result, TempDir), (
        f"oxi.tmp should inject a TempDir instance, got {type(result).__name__}"
    )


def test_oxi_proxy_cap_injects_stdcapture(
    tmp: TempDir, fixture_session: Fixture[FixtureSession]
) -> None:
    """oxi.cap injects a StdCapture builtin via the OxiNamespaceProxy."""
    teardowns: list = []
    proxy = OxiNamespaceProxy(fixture_session, str(tmp / "test.py"), teardowns)
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
    proxy = OxiNamespaceProxy(fixture_session, str(tmp / "test.py"), [])
    result = proxy.patch
    assert isinstance(result, Patcher), (
        f"oxi.patch should inject a Patcher instance, got {type(result).__name__}"
    )


def test_oxi_proxy_log_injects_logcapture(
    tmp: TempDir, fixture_session: Fixture[FixtureSession]
) -> None:
    """oxi.log injects a LogCapture builtin via the OxiNamespaceProxy."""
    teardowns: list = []
    proxy = OxiNamespaceProxy(fixture_session, str(tmp / "test.py"), teardowns)
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
    proxy = OxiNamespaceProxy(fixture_session, str(tmp / "test.py"), [])
    try:
        _ = proxy.unknown
        msg = "expected AttributeError for unknown oxi builtin 'unknown'"
        raise AssertionError(msg)
    except AttributeError as exc:
        assert "unknown" in str(exc), (
            f"AttributeError should mention the unknown name 'unknown', got: {exc}"
        )
        assert "tmp" in str(exc), (  # available list shown
            f"AttributeError should show available builtins (including 'tmp'), got: "
            f"{exc}"
        )


# ── Shared fixtures ────────────────────────────────────────────────────────


def test_shared_fixture_accessed_via_namespace_is_frozen_proxy() -> None:
    """shared=True fixture accessed via fx.db.conn should be FrozenProxy-wrapped."""
    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "conn",
            lambda: {"host": "localhost", "port": 5432},
            shared=True,
            namespace="db",
        )
    )
    proxy = FixturesProxy(session, "/fake/test.py", [])
    result = proxy.db.conn
    assert isinstance(result, FrozenProxy), (
        f"shared fixture accessed via namespace proxy should be wrapped in "
        f"FrozenProxy, got {type(result).__name__}"
    )


# ── OxiNamespaceProxy ctx ──────────────────────────────────────────────────


def test_oxi_proxy_ctx_returns_test_context(
    fixture_session: Fixture[FixtureSession],
) -> None:
    """fx.oxi.ctx should return a TestContext instance."""
    proxy = OxiNamespaceProxy(fixture_session, "/fake/test.py", [])
    result = proxy.ctx
    assert isinstance(result, OxiTestContext), (
        f"oxi.ctx should return a TestContext instance, got {type(result).__name__}"
    )


def test_fixtures_proxy_caches_namespace_proxy_on_repeated_access(
    fixture_session: Fixture[FixtureSession],
) -> None:
    """FixturesProxy.oxi caches and returns the same OxiNamespaceProxy each access."""
    proxy = FixturesProxy(fixture_session, "/fake/test.py", [])
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
    proxy = OxiNamespaceProxy(fixture_session, "/fake/test.py", teardowns)
    tmp1 = proxy.tmp
    tmp2 = proxy.tmp
    assert tmp1 is tmp2, (
        "OxiNamespaceProxy.tmp should return the same cached TempDir on repeated access"
    )
    for td in reversed(teardowns):
        td()


# ── Integration: full pipeline ──────────────────────────────────────────────


def test_full_pipeline_fx_namespace_access(tmp: TempDir) -> None:
    """Full pipeline: namespaced fixtures in conftest, accessed via fx.db.conn."""
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "import oxitest\n"
        "db = oxitest.Fixtures()\n"
        "@db.fixture\n"
        "def conn() -> str:\n"
        "    return 'connected'\n"
    )

    test_file = tmp / "test_ns.py"
    test_file.write_text(
        "import oxitest\n"
        "def test_access(fx: oxitest.Fixtures) -> None:\n"
        "    assert fx.db.conn == 'connected'\n"
    )

    defs = load_fixtures_from_conftest(str(conftest))
    reg = FixtureRegistry()
    for d in defs:
        reg.register(d)
    session = FixtureSession(reg)

    result = helpers.common.run_test(str(test_file), "test_access", session)
    assert result.status == "passed", result.message


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
        "    assert p.read_text() == 'hi'\n"
    )

    result = helpers.common.run_test(str(test_file), "test_oxi_tmp", fixture_session)
    assert result.status == "passed", result.message


def test_full_pipeline_two_namespaces_same_fixture_name(tmp: TempDir) -> None:
    """Two namespaces with the same fixture name are independent."""
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "import oxitest\n"
        "db = oxitest.Fixtures()\n"
        "@db.fixture\n"
        "def url() -> str:\n"
        "    return 'postgres://localhost'\n"
        "\n"
        "cache = oxitest.Fixtures()\n"
        "@cache.fixture\n"
        "def url() -> str:\n"
        "    return 'redis://localhost'\n"
    )

    test_file = tmp / "test_two_ns.py"
    test_file.write_text(
        "import oxitest\n"
        "def test_two_namespaces(fx: oxitest.Fixtures) -> None:\n"
        "    assert fx.db.url == 'postgres://localhost'\n"
        "    assert fx.cache.url == 'redis://localhost'\n"
    )

    defs = load_fixtures_from_conftest(str(conftest))
    reg = FixtureRegistry()
    for d in defs:
        reg.register(d)
    session = FixtureSession(reg)

    result = helpers.common.run_test(str(test_file), "test_two_namespaces", session)
    assert result.status == "passed", result.message
