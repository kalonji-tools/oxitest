"""lifetime="package" — the exactly-once-per-run tier (#1710)."""

import oxitest as oxi
from oxitest._bridge._fixture_registry import LIFETIME_SCOPES, FixtureScope
from oxitest._bridge._lifetime import Lifetime


def test_package_lifetime_maps_to_its_own_scope() -> None:
    """The package tier needs a scope member of its own."""
    # Act
    scope = LIFETIME_SCOPES.get(Lifetime.PACKAGE)

    # Assert — SHARED is the legacy Fixtures(shared=True) scope (_fixtures.py:259,
    # fixture_lister.py:150, is_shared at _fixture_registry.py). Reusing it would
    # make every legacy shared fixture look package-scoped to the scheduler, and
    # collapse parallelism for suites that never asked for the package tier.
    assert scope is FixtureScope.PACKAGE, (
        f"package lifetime must map to a dedicated FixtureScope, got {scope}"
    )


def test_decorator_accepts_package_lifetime() -> None:
    """@oxi.fixture(lifetime="package") no longer raises UsageError."""

    # Act
    @oxi.fixture(lifetime="package")
    def db_engine() -> str:
        return "engine"

    # Assert — the decorator returns the function with a marker attached; a None
    # return would break every downstream registration path silently.
    assert db_engine is not None, (
        "the decorator must return the decorated function for registration to find it"
    )


def test_session_maps_to_a_different_scope_than_package() -> None:
    """The two wide tiers must not share a scope.

    Slice 4 (#1711) settled the semantics this file used to assert were
    undecided: ``session`` is once per **task group** (ADR-0009 Amendment 4),
    ``package`` exactly once per run. They sit at opposite ends of the same
    trade — package buys exactness and charges parallelism, session the
    reverse — so collapsing them onto one scope would silently give one tier
    the other's behaviour.
    """
    # Act
    package_scope = LIFETIME_SCOPES.get(Lifetime.PACKAGE)
    session_scope = LIFETIME_SCOPES.get(Lifetime.SESSION)

    # Assert
    assert session_scope is FixtureScope.PROCESS, (
        f"session lifetime must map to FixtureScope.PROCESS, not the builtins' "
        f"SESSION bucket, got {session_scope} — sharing that bucket is what "
        f"gave the tier a per-task-group boundary instead of a per-process one "
        f"(#1777)"
    )
    assert package_scope is not session_scope, (
        "package and session must not share a scope: they cache in different "
        "buckets (per anchor directory vs per process), and sharing one "
        "would make the declaring subtree's co-location apply to both"
    )
