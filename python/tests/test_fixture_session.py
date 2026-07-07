"""Tests for FixtureSession fixture timing instrumentation and unification."""

from __future__ import annotations

import time
from collections.abc import Callable, Generator

from oxitest import TempDir, helpers
from oxitest._bridge._fixture_registry import (
    BuiltinSource,
    ConftestSource,
    FixtureDef,
    FixtureScope,
    PluginSource,
)
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge.plugin_loader import PluginRegistry


class _MinimalType:
    """Marker type for minimal plugin provider tests."""


def test_setup_timing_recorded_for_function_scoped_fixture() -> None:
    """Fixture setup time is tracked on the session."""

    def slow_fixture() -> int:
        time.sleep(0.01)
        return 42

    session = helpers.common.make_session_with("slow_fixture", slow_fixture)
    teardowns: list[Callable[[], None]] = []
    session.get_fixture("slow_fixture", "test_mod.py", teardowns)

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

    assert timings == [], f"expected empty list from empty session, got {timings!r}"


def test_teardown_timing_recorded_for_yield_fixture() -> None:
    """Fixture teardown time is tracked on the session."""

    def yield_fixture() -> Generator[int]:
        yield 42
        time.sleep(0.01)

    session = helpers.common.make_session_with("yield_fx", yield_fixture)
    teardowns: list[Callable[[], None]] = []
    session.get_fixture("yield_fx", "test_mod.py", teardowns)

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

    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "shared_fx", shared_fixture, conftest_path="/conftest.py", shared=True
        )
    )
    teardowns: list[Callable[[], None]] = []

    session.get_fixture("shared_fx", "test_mod.py", teardowns)
    session.get_fixture("shared_fx", "test_mod.py", teardowns)

    timings = session.get_fixture_timings()
    assert len(timings) == 1, f"expected 1 timing entry, got {len(timings)}"
    entry = timings[0]
    assert entry.setup_count == 1, (
        f"expected setup_count 1 (cached on second call), got {entry.setup_count}"
    )


def test_multiple_fixtures_each_tracked_separately() -> None:
    """Each fixture gets its own timing entry."""
    session = helpers.common.make_session_with("fast_a", lambda: 1)
    teardowns: list[Callable[[], None]] = []

    session._registry.register(
        helpers.common.make_fixture_def(
            "fast_b", lambda: 2, conftest_path="/conftest.py"
        )
    )

    session.get_fixture("fast_a", "test_mod.py", teardowns)
    session.get_fixture("fast_b", "test_mod.py", teardowns)

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
    defn = session._registry.resolve(TempDir)
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
    defn = session._registry.resolve(TempDir, qualifier="TempDir")
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

        def create(self, ctx: object) -> int:
            return 42

        def teardown(self, value: object) -> None:
            pass

    class FakePluginRegistry(PluginRegistry):
        """PluginRegistry subclass that injects a single fixture provider."""

        @property
        def fixture_providers(self) -> tuple:  # type: ignore[override]
            return (MinimalProvider(),)

    session = FixtureSession([], FakePluginRegistry())

    defn = session._registry.resolve(_MinimalType)
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
