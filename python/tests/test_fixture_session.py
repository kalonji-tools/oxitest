"""Tests for FixtureSession fixture timing instrumentation."""

from __future__ import annotations

import time

from conftest import helpers
from oxitest._bridge._fixture_registry import FixtureDef
from oxitest._bridge._fixture_session import _NullFixtureSession


def test_setup_timing_recorded_for_function_scoped_fixture():
    """Fixture setup time is tracked on the session."""

    def slow_fixture() -> int:
        time.sleep(0.01)
        return 42

    session = helpers.common.make_session_with("slow_fixture", slow_fixture)
    session.begin_module("test_mod.py")
    teardowns: list = []
    session.get_fixture("slow_fixture", "test_mod.py", teardowns)

    timings = session.get_fixture_timings()
    assert len(timings) == 1, f"expected exactly 1 timing entry, got {len(timings)}"
    entry = timings[0]
    assert entry["name"] == "slow_fixture", (
        f"expected fixture name 'slow_fixture', got {entry['name']!r}"
    )
    assert entry["setup_count"] == 1, (
        f"expected setup_count 1, got {entry['setup_count']}"
    )
    assert entry["total_setup_ms"] >= 10.0, (
        f"expected at least 10ms setup time, got {entry['total_setup_ms']}"
    )
    assert entry["total_teardown_ms"] == 0.0, (
        f"expected 0.0 teardown time, got {entry['total_teardown_ms']}"
    )


def test_null_session_returns_empty_timings():
    """_NullFixtureSession.get_fixture_timings() returns an empty list."""
    session = _NullFixtureSession()

    timings = session.get_fixture_timings()

    assert timings == [], f"expected empty list from null session, got {timings!r}"


def test_teardown_timing_recorded_for_yield_fixture():
    """Fixture teardown time is tracked on the session."""

    def yield_fixture():
        yield 42
        time.sleep(0.01)

    session = helpers.common.make_session_with("yield_fx", yield_fixture)
    session.begin_module("test_mod.py")
    teardowns: list = []
    session.get_fixture("yield_fx", "test_mod.py", teardowns)

    # Run teardowns (simulates end-of-test cleanup)
    for td in reversed(teardowns):
        td()

    timings = session.get_fixture_timings()
    assert len(timings) == 1, f"expected 1 timing entry, got {len(timings)}"
    entry = timings[0]
    assert entry["teardown_count"] == 1, (
        f"expected teardown_count 1, got {entry['teardown_count']}"
    )
    assert entry["total_teardown_ms"] >= 10.0, (
        f"expected at least 10ms teardown time, got {entry['total_teardown_ms']}"
    )


def test_shared_fixture_setup_timed_once():
    """Shared fixture setup is only timed once; second resolve is cached."""

    def shared_fixture() -> int:
        time.sleep(0.01)
        return 99

    session = helpers.common.make_session(
        FixtureDef(
            name="shared_fx",
            func=shared_fixture,
            autouse=False,
            params=None,
            conftest_path="/conftest.py",
            shared=True,
            namespace="",
            is_async=False,
        )
    )
    session.begin_module("test_mod.py")
    teardowns: list = []

    session.get_fixture("shared_fx", "test_mod.py", teardowns)
    session.get_fixture("shared_fx", "test_mod.py", teardowns)

    timings = session.get_fixture_timings()
    assert len(timings) == 1, f"expected 1 timing entry, got {len(timings)}"
    entry = timings[0]
    assert entry["setup_count"] == 1, (
        f"expected setup_count 1 (cached on second call), got {entry['setup_count']}"
    )


def test_multiple_fixtures_each_tracked_separately():
    """Each fixture gets its own timing entry."""
    session = helpers.common.make_session_with("fast_a", lambda: 1)
    session.begin_module("test_mod.py")
    teardowns: list = []

    session._registry.register(
        FixtureDef(
            name="fast_b",
            func=lambda: 2,
            autouse=False,
            params=None,
            conftest_path="/conftest.py",
            shared=False,
            namespace="",
            is_async=False,
        )
    )

    session.get_fixture("fast_a", "test_mod.py", teardowns)
    session.get_fixture("fast_b", "test_mod.py", teardowns)

    timings = session.get_fixture_timings()
    names = [t["name"] for t in timings]
    assert "fast_a" in names, f"expected 'fast_a' in timing names, got {names}"
    assert "fast_b" in names, f"expected 'fast_b' in timing names, got {names}"
    assert len(timings) == 2, f"expected 2 timing entries, got {len(timings)}"
    assert all(t["setup_count"] == 1 for t in timings), (
        f"expected all setup_count to be 1, got {[t['setup_count'] for t in timings]}"
    )
