"""lifetime="package" — the exactly-once-per-run tier (#1710)."""

import oxitest as oxi
from oxitest._bridge._errors import UsageError
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


def test_session_lifetime_is_still_rejected() -> None:
    """`session` stays out until #1711 decides its semantics."""
    # Act / Assert — shipping it early would mean guessing between per-run and
    # per-worker, which is exactly the decision #1711 exists to make.
    with oxi.raises(UsageError):

        @oxi.fixture(lifetime="session")
        def cluster() -> str:
            return "cluster"
