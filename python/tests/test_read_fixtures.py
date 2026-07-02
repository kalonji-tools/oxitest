from __future__ import annotations

from collections.abc import Callable

import oxitest
from oxitest import Fixture
from oxitest._bridge._read_fixtures import _FixturesProxy


def test_proxy_resolves_namespace_and_accessor(
    tmp: oxitest.TempDir,
    fixtures_registry: Fixture[Callable],
) -> None:
    from oxitest._bridge._fixture_registry import (
        ConftestSource,
        FixtureDef,
        FixtureRegistry,
        FixtureScope,
    )

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
    fixtures_registry(reg)
    proxy = _FixturesProxy()
    accessor = proxy.db.conn
    assert hasattr(accessor, "_oxitest_fixture_name"), (
        "should return a FixtureAccessor with fixture name metadata"
    )


def test_proxy_raises_outside_session(fixtures_registry: Fixture[Callable]) -> None:
    fixtures_registry(None)
    proxy = _FixturesProxy()
    with oxitest.raises(AttributeError, match="only available during a test session"):
        proxy.db
