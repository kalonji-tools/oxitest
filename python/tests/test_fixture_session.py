"""Tests for FixtureSession fixture timing instrumentation and unification."""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any

import oxitest
from oxitest import Fixture, TempDir, TempDirFactory
from oxitest._bridge._errors import (
    BoundaryError,
    FixtureNotFoundError,
    FixtureTypeNotFoundError,
)
from oxitest._bridge._fixture_registry import (
    BuiltinSource,
    ConftestSource,
    FixtureDef,
    FixtureRegistry,
    FixtureScope,
    ModuleSource,
    PluginSource,
)
from oxitest._bridge._fixture_session import FixtureSession, _SessionProtocol
from oxitest._bridge._lifetime import Lifetime
from oxitest._bridge.plugin_loader import (
    ActivatedPluginEntry,
    PluginRegistry,
    _PluginRegistryBuilder,
)
from oxitest.plugin import Plugin
from tests import helpers


class _MinimalType:
    """Marker type for minimal plugin provider tests."""


class _FakeFixtureProvider:
    """Test double for the FixtureProvider protocol."""

    __module__ = "fake_plugin"

    def __init__(
        self,
        name: str,
        fixture_type: type,
        *,
        scope: str = "each",
        autouse: bool = False,
    ) -> None:
        self.name = name
        self.fixture_type = fixture_type
        self.scope = scope
        self.autouse = autouse

    def create(self, **_: Any) -> object:
        return self.fixture_type()

    def teardown(self, **_: Any) -> None:
        pass


def test_setup_timing_recorded_for_function_scoped_fixture() -> None:
    """Fixture setup time is tracked on the session."""

    def slow_fixture() -> int:
        time.sleep(0.01)
        return 42

    session = helpers.make_session_with("slow_fixture", slow_fixture)
    teardowns: list[Callable[[], None]] = []
    session.get_fixture_by_name("slow_fixture", "test_mod.py", teardowns)

    timings = session.get_fixture_timings()
    assert len(timings) == 1, f"expected exactly 1 timing entry, got {len(timings)}"
    entry = timings[0]
    assert entry.name == "slow_fixture", (
        f"expected fixture name 'slow_fixture', got {entry.name!r}"
    )
    assert entry.setup_count == 1, f"expected setup_count 1, got {entry.setup_count}"
    assert entry.total_setup_ms >= 10.0, (
        f"expected at least 10ms setup time, got {entry.total_setup_ms}"
    )
    assert entry.total_teardown_ms == 0.0, (
        f"expected 0.0 teardown time, got {entry.total_teardown_ms}"
    )


def test_empty_session_returns_empty_timings() -> None:
    """FixtureSession([]) with no fixtures returns empty timings list."""
    session = FixtureSession([])

    timings = session.get_fixture_timings()

    assert timings == (), f"expected empty tuple from empty session, got {timings!r}"


def test_teardown_timing_recorded_for_yield_fixture() -> None:
    """Fixture teardown time is tracked on the session."""

    def yield_fixture() -> Generator[int]:
        yield 42
        time.sleep(0.01)

    session = helpers.make_session_with("yield_fx", yield_fixture)
    teardowns: list[Callable[[], None]] = []
    session.get_fixture_by_name("yield_fx", "test_mod.py", teardowns)

    # Run teardowns (simulates end-of-test cleanup)
    for td in reversed(teardowns):
        td()

    timings = session.get_fixture_timings()
    assert len(timings) == 1, f"expected 1 timing entry, got {len(timings)}"
    entry = timings[0]
    assert entry.teardown_count == 1, (
        f"expected teardown_count 1, got {entry.teardown_count}"
    )
    assert entry.total_teardown_ms >= 10.0, (
        f"expected at least 10ms teardown time, got {entry.total_teardown_ms}"
    )


def test_shared_fixture_setup_timed_once() -> None:
    """Shared fixture setup is only timed once; second resolve is cached."""

    def shared_fixture() -> int:
        time.sleep(0.01)
        return 99

    session = helpers.make_session(
        helpers.make_fixture_def(
            "shared_fx", shared_fixture, conftest_path="/conftest.py", shared=True
        )
    )
    teardowns: list[Callable[[], None]] = []

    session.get_fixture_by_name("shared_fx", "test_mod.py", teardowns)
    session.get_fixture_by_name("shared_fx", "test_mod.py", teardowns)

    timings = session.get_fixture_timings()
    assert len(timings) == 1, f"expected 1 timing entry, got {len(timings)}"
    entry = timings[0]
    assert entry.setup_count == 1, (
        f"expected setup_count 1 (cached on second call), got {entry.setup_count}"
    )


def test_multiple_fixtures_each_tracked_separately() -> None:
    """Each fixture gets its own timing entry."""
    session = helpers.make_session_with("fast_a", lambda: 1)
    teardowns: list[Callable[[], None]] = []

    session.registry.register(
        helpers.make_fixture_def("fast_b", lambda: 2, conftest_path="/conftest.py")
    )

    session.get_fixture_by_name("fast_a", "test_mod.py", teardowns)
    session.get_fixture_by_name("fast_b", "test_mod.py", teardowns)

    timings = session.get_fixture_timings()
    names = [t.name for t in timings]
    assert "fast_a" in names, f"expected 'fast_a' in timing names, got {names}"
    assert "fast_b" in names, f"expected 'fast_b' in timing names, got {names}"
    assert len(timings) == 2, f"expected 2 timing entries, got {len(timings)}"
    assert all(t.setup_count == 1 for t in timings), (
        f"expected all setup_count to be 1, got {[t.setup_count for t in timings]}"
    )


# ── FixtureSession unification ────────────────────────────────────────────────


def test_session_builtins_registered() -> None:
    """Builtins appear in the unified registry after session init."""
    session = FixtureSession([], PluginRegistry())
    defn = session.registry.resolve(TempDir)
    assert defn.name is not None, "TempDir builtin should be registered"
    assert isinstance(defn.source, BuiltinSource), (
        "source should be BuiltinSource for a builtin fixture"
    )


def test_session_conftest_overrides_builtin() -> None:
    """A conftest fixture with the same binding type overrides a builtin."""
    custom = FixtureDef(
        name="TempDir",
        fixture_type=TempDir,
        scope=FixtureScope.EACH,
        source=ConftestSource(func=lambda: "custom", conftest_path="/conftest.py"),
    )
    session = FixtureSession([custom], PluginRegistry())
    # Use qualifier "TempDir" to disambiguate (conftest registered with name "TempDir")
    defn = session.registry.resolve(TempDir, qualifier="TempDir")
    assert isinstance(defn.source, ConftestSource), (
        "conftest fixture should override builtin when they share the same type"
    )


def test_session_plugin_without_scope_autouse() -> None:
    """Plugin provider without scope/autouse attrs uses defaults (each, False)."""

    class MinimalProvider:
        """Provider with only the required protocol methods."""

        @property
        def name(self) -> str:
            return "minimal"

        @property
        def fixture_type(self) -> type[_MinimalType]:
            return _MinimalType

        def create(self, **_: Any) -> int:
            return 42

        def teardown(self, **_: Any) -> None:
            pass

    providers: Any = (MinimalProvider(),)
    plugin = Plugin(fixture_providers=providers)
    entry = ActivatedPluginEntry(module_name="test_minimal", plugin=plugin)
    builder = _PluginRegistryBuilder()
    builder.add_entry(entry)
    registry = builder.build()

    session = FixtureSession([], registry)

    defn = session.registry.resolve(_MinimalType)
    assert defn.name == "minimal", (
        "plugin fixture should be registered with provider name"
    )
    assert isinstance(defn.source, PluginSource), (
        "plugin fixture should have PluginSource, not a different source variant"
    )
    assert defn.scope == FixtureScope.EACH, (
        "plugin without scope attr should default to EACH scope"
    )
    assert defn.autouse is False, "plugin without autouse attr should default to False"


def _session_with(*providers: _FakeFixtureProvider) -> FixtureSession:
    """Build a FixtureSession from fake providers."""
    wrapped: Any = tuple(providers)
    plugin = Plugin(fixture_providers=wrapped)
    entry = ActivatedPluginEntry(module_name="fake_plugin", plugin=plugin)
    builder = _PluginRegistryBuilder()
    builder.add_entry(entry)
    return FixtureSession([], builder.build())


def test_register_plugin_fixtures_stamps_correct_fields() -> None:
    """Plugin fixtures are registered with correct name, type, scope, and source."""
    provider = _FakeFixtureProvider(
        name="plugin_db",
        fixture_type=_MinimalType,
        scope="session",
        autouse=False,
    )
    session = _session_with(provider)

    defn = session.registry.get("plugin_db")
    assert defn is not None, (
        "plugin fixture 'plugin_db' should be registered in the session registry"
    )
    assert defn.fixture_type is _MinimalType, (
        "registered fixture type should match the provider's fixture_type"
    )
    assert defn.scope == FixtureScope.SESSION, (
        "registered scope should match the provider's scope='session'; "
        "'shared' stopped being a legal provider scope with the tier (#1720)"
    )
    assert isinstance(defn.source, PluginSource), (
        "registered source should be PluginSource, not ConftestSource"
    )
    assert defn.source.plugin_module == "fake_plugin", (
        "PluginSource should record the provider's __module__"
    )


def test_register_plugin_fixtures_respects_autouse() -> None:
    """Plugin fixtures with autouse=True are registered as autouse."""
    provider = _FakeFixtureProvider(
        name="auto_setup",
        fixture_type=_MinimalType,
        autouse=True,
    )
    session = _session_with(provider)

    defn = session.registry.get("auto_setup")
    assert defn is not None, "autouse plugin fixture should be registered"
    assert defn.autouse is True, (
        "autouse flag should be True on the registered FixtureDef"
    )


def test_register_plugin_fixtures_empty_registry_is_noop() -> None:
    """Empty PluginRegistry adds no fixtures to the session."""
    session = FixtureSession([], PluginRegistry())

    all_names = list(session.registry)
    assert all(not n.startswith("plugin") for n in all_names), (
        f"empty registry should add no plugin fixtures, got {all_names}"
    )


def test_session_protocol_declares_get_fixture_by_type() -> None:
    """_SessionProtocol must declare get_fixture_by_type with the expected signature.

    Parameter names are load-bearing — arrange (#1268) calls this method with
    kwargs, so renaming t/module_path/fn_teardowns silently breaks arrange.
    """
    sig = inspect.signature(_SessionProtocol.get_fixture_by_type)
    params = list(sig.parameters)
    assert params == ["self", "t", "module_path", "fn_teardowns"], (
        f"arrange (#1268) calls this with kwargs — renaming "
        f"t/module_path/fn_teardowns silently breaks arrange; got {params}"
    )


def test_get_fixture_by_type_resolves_builtin(
    fixture_session: Fixture[FixtureSession],
) -> None:
    """Passing TempDir to get_fixture_by_type must resolve via the BuiltinSource path.

    Returns a TempDir instance via the BuiltinSource dispatch path.
    """
    teardowns: list[Callable[[], None]] = []
    result = fixture_session.get_fixture_by_type(TempDir, "test_mod.py", teardowns)

    assert isinstance(result, TempDir), (
        "arrange (#1268)'s @oxi.arrange(TempDir) relies on this dispatch "
        "path for side effects"
    )
    assert len(teardowns) > 0, (
        "without teardown registration, function-scoped resources leak "
        "across tests — reverse-order cleanup requires fn_teardowns entries"
    )


def test_get_fixture_by_type_resolves_session_scoped_builtin(
    fixture_session: Fixture[FixtureSession],
) -> None:
    """A session-scoped builtin (TempDirFactory) resolves via get_fixture_by_type.

    Teardown lands on the session scope, NOT on the caller's fn_teardowns list —
    session-scoped fixtures live beyond a single test. Arrange (#1268) callers
    with @oxi.arrange(TempDirFactory) rely on this routing to avoid double-teardown.
    """
    fn_teardowns: list[Callable[[], None]] = []
    result = fixture_session.get_fixture_by_type(
        TempDirFactory, "test_mod.py", fn_teardowns
    )

    assert isinstance(result, TempDirFactory), (
        "arrange (#1268)'s @oxi.arrange(TempDirFactory) relies on this "
        "dispatch path for session-scoped resource setup"
    )
    assert len(fn_teardowns) == 0, (
        f"routing to fn_teardowns would cause teardown-per-test instead of "
        f"teardown-at-session-end — session-scoped resources need to survive "
        f"across tests; got {len(fn_teardowns)} entries"
    )
    session_teardowns = fixture_session._session_scope.teardowns  # noqa: SLF001 — no public API exposes session-scope teardown count; this is the only way to verify routing
    assert len(session_teardowns) > 0, (
        "without session-scope teardown registration, session-scoped "
        "resources leak past the test run"
    )


@dataclass
class _MyResult:
    """Synthetic return type for conftest fixture resolution tests."""

    value: int = 42


@dataclass
class _PluginValue:
    """Synthetic return type for plugin fixture resolution tests."""

    marker: str = "from_plugin"


class _UnregisteredType:
    """Type with no fixture registration — used to exercise the error path."""


def test_get_fixture_by_type_resolves_conftest_fixture() -> None:
    """A conftest ConftestSource fixture must resolve via get_fixture_by_type.

    The unified registry indexes FixtureDefs by return type; passing _MyResult
    to get_fixture_by_type must find and run the conftest factory.

    Uses make_fixture_def with fixture_type= to set the binding type explicitly
    (bypasses the from __future__ import annotations string-annotation issue).
    Uses a generator factory so teardown registration is exercised.
    """

    def _my_result_factory() -> Generator[_MyResult]:
        yield _MyResult()

    # Arrange: build a session with a conftest-sourced fixture bound to _MyResult
    session = helpers.make_session(
        helpers.make_fixture_def(
            "my_result",
            _my_result_factory,
            conftest_path="/fake/conftest.py",
            fixture_type=_MyResult,
        )
    )

    teardowns: list[Callable[[], None]] = []
    result = session.get_fixture_by_type(_MyResult, "test_x.py", teardowns)

    assert isinstance(result, _MyResult), (
        "arrange (#1268)'s @oxi.arrange(MyType) requires conftest fixtures "
        "indexed by return type to resolve via this dispatch"
    )
    assert result.value == 42, (
        "conftest fixture body must actually execute — a resolved-but-not-run "
        "fixture would be a null implementation"
    )
    assert len(teardowns) > 0, (
        "without teardown registration, generator fixtures leak their "
        "post-yield cleanup — reverse-order cleanup requires fn_teardowns entries"
    )


def test_get_fixture_by_type_resolves_plugin_fixture() -> None:
    """Plugin FixtureProvider must resolve via get_fixture_by_type.

    Arrange (#1268) with a plugin-provided type must go through this path.
    Uses the existing _FakeFixtureProvider test double and _session_with helper
    (both defined above) so registration ceremony stays minimal.
    """
    # Arrange: build a session with a plugin-sourced fixture bound to _PluginValue
    provider = _FakeFixtureProvider(name="plugin_value", fixture_type=_PluginValue)
    session = _session_with(provider)

    teardowns: list[Callable[[], None]] = []
    result = session.get_fixture_by_type(_PluginValue, "test_mod.py", teardowns)

    assert isinstance(result, _PluginValue), (
        "arrange (#1268)'s @oxi.arrange(PluginType) relies on the "
        "PluginSource dispatch path"
    )
    assert result.marker == "from_plugin", (
        "provider.create() must actually run and return _PluginValue() — "
        "a default-constructed instance carries marker='from_plugin'; "
        "any other value means the wrong path executed"
    )
    assert len(teardowns) == 1, (
        "without teardown registration, plugin provider.teardown never "
        "runs — resolve_by_source appends a lambda per PluginSource resolution"
    )


def test_get_fixture_by_type_raises_on_unknown_type() -> None:
    """An unregistered class must raise FixtureTypeNotFoundError.

    FixtureTypeNotFoundError is a FixtureNotFoundError subclass, so existing
    catch sites remain unbroken.  The message must mention the three valid
    registration routes (BuiltinFixture, plugin FixtureProvider, conftest
    return annotation) and must NOT mention 'Fixture[<type>]' — that hint
    is for the by-name path and is misleading for @oxi.arrange(MyType) users.
    Silent failure would let @oxi.arrange(UnknownType) silently skip,
    hiding user mistakes.
    """
    session = helpers.make_session()  # empty registry — no fixtures registered
    teardowns: list[Callable[[], None]] = []

    with oxitest.raises(
        FixtureTypeNotFoundError, match=r"_UnregisteredType"
    ) as exc_info:
        session.get_fixture_by_type(_UnregisteredType, "test_mod.py", teardowns)

    msg = str(exc_info.value)
    assert isinstance(exc_info.value, FixtureNotFoundError), (
        "FixtureTypeNotFoundError must be a subclass of FixtureNotFoundError — "
        "existing 'except FixtureNotFoundError' catch sites must keep working"
    )
    assert "BuiltinFixture" in msg, (
        "@oxi.arrange(MyType) users need to know BuiltinFixture is one of "
        "three valid registration routes — the by-name Fixture[T] hint is "
        "misleading here"
    )
    assert "FixtureProvider" in msg or "plugin" in msg, (
        "@oxi.arrange(MyType) users need to know plugin-provided types are "
        "one of three valid registration routes"
    )
    assert "conftest" in msg, (
        "@oxi.arrange(MyType) users need to know conftest fixtures with "
        "matching return annotations are one of three valid registration routes"
    )
    assert "Fixture[" not in msg, (
        "error message must NOT mention 'Fixture[<type>]' — that hint applies to "
        "the by-name path, not the by-type path used by @oxi.arrange(MyType)"
    )


@dataclass(frozen=True)
class LookupCase:
    """Parametrize case for the boundary-versus-not-found taxonomy."""

    namespace: str
    leaf: str
    module_path: str
    expected_type: type[Exception]


def _api_session() -> FixtureSession:
    """A session whose only fixture is anchored at /t/api."""
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


@oxitest.parametrize(
    unreachable_segment_real_leaf=LookupCase(
        namespace="api",
        leaf="api_conn",
        module_path="/t/admin/test_a.py",
        expected_type=BoundaryError,
    ),
    unreachable_segment_bad_leaf=LookupCase(
        namespace="api",
        leaf="typo",
        module_path="/t/admin/test_a.py",
        expected_type=BoundaryError,
    ),
    unknown_segment=LookupCase(
        namespace="nope",
        leaf="x",
        module_path="/t/admin/test_a.py",
        expected_type=FixtureNotFoundError,
    ),
    reachable_segment_bad_leaf=LookupCase(
        namespace="api",
        leaf="typo",
        module_path="/t/api/test_a.py",
        expected_type=FixtureNotFoundError,
    ),
)
def test_error_type_is_a_function_of_the_segment(case: LookupCase) -> None:
    """Decision 9's taxonomy: the segment decides, never the leaf."""
    # Arrange
    session = _api_session()

    # Act
    error = session.fixture_lookup_error(case.leaf, case.namespace, case.module_path)

    # Assert
    assert type(error) is case.expected_type, (
        f"reaching '{case.namespace}.{case.leaf}' from {case.module_path}: making "
        f"the error type depend on the leaf too turns one documentable line into "
        f"a 2x2, and a leaf-first message for a cross-boundary typo tells the "
        f"user that fixing the spelling will work — it will not"
    )


def test_a_conftest_only_namespace_never_produces_a_boundary_error() -> None:
    """The legacy API is exempt from B1 until #1720 retires it."""
    # Arrange
    registry = FixtureRegistry()
    registry.register(
        FixtureDef(
            name="legacy_conn",
            fixture_type=object,
            scope=FixtureScope.EACH,
            source=ConftestSource(func=object, conftest_path="/t/api/conftest.py"),
            namespace="legacy",
        )
    )
    session = FixtureSession(registry)

    # Act
    error = session.fixture_lookup_error("typo", "legacy", "/t/elsewhere/test_a.py")

    # Assert
    assert type(error) is FixtureNotFoundError, (
        "conftest fixtures resolve run-wide, so there is no boundary to report "
        "crossing; raising BoundaryError here would tell the user to restructure "
        "packages to fix what is only a misspelling"
    )


def _mixed_api_session() -> FixtureSession:
    """A namespace ``api`` fed by both a conftest instance and ``/t/api``.

    The registrar permits this: its duplicate check fires on a repeated
    ``(namespace, name)`` pair, and ``api.legacy_conn`` and ``api.api_conn``
    are different pairs.
    """
    registry = FixtureRegistry()
    registry.register(
        FixtureDef(
            name="legacy_conn",
            fixture_type=object,
            scope=FixtureScope.EACH,
            source=ConftestSource(func=object, conftest_path="/t/conftest.py"),
            namespace="api",
        )
    )
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


def test_a_mixed_namespace_still_reports_a_crossed_boundary() -> None:
    """An unanchored def in the namespace must not mask the anchored one."""
    # Arrange
    session = _mixed_api_session()

    # Act
    error = session.fixture_lookup_error("api_conn", "api", "/t/admin/test_a.py")

    # Assert
    assert type(error) is BoundaryError, (
        "conftest fixtures are exempt from B1 and so read as visible from "
        "everywhere; letting one stand as proof that the segment is reachable "
        "makes the BoundaryError branch unreachable for every namespace that "
        "also holds a directory anchor, and sends the user hunting for a typo "
        "in a name they spelled correctly"
    )


def test_a_mixed_namespace_reports_not_found_inside_the_anchor() -> None:
    """Narrowing the reachability question must not break the legal access."""
    # Arrange
    session = _mixed_api_session()

    # Act
    error = session.fixture_lookup_error("typo", "api", "/t/api/v1/test_a.py")

    # Assert
    assert type(error) is FixtureNotFoundError, (
        "the test sits under /t/api, so B1 lets it through and the only thing "
        "wrong is the leaf name — a BoundaryError here would tell the user to "
        "restructure packages to fix a misspelling"
    )


def test_cross_boundary_resolution_raises_rather_than_reporting_not_found() -> None:
    """The end of the wiring: the proxy path actually raises the new error."""
    # Arrange
    session = _api_session()
    teardowns: list[Callable[[], None]] = []

    # Act / Assert
    with oxitest.raises(BoundaryError):
        session.get_fixture_in_namespace(
            "api_conn", "api", "/t/admin/test_a.py", teardowns, test_is_async=True
        )
