from __future__ import annotations

import oxitest
from oxitest._bridge._read_fixtures import _fixtures_registry_var, _FixturesProxy


def test_proxy_resolves_namespace_and_accessor(_tmp: oxitest.TempDir) -> None:
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
    token = _fixtures_registry_var.set(reg)
    try:
        proxy = _FixturesProxy()
        accessor = proxy.db.conn
        assert hasattr(accessor, "_oxitest_fixture_name"), (
            "should return a FixtureAccessor with fixture name metadata"
        )
    finally:
        _fixtures_registry_var.reset(token)


def test_proxy_raises_outside_session() -> None:
    token = _fixtures_registry_var.set(None)
    try:
        proxy = _FixturesProxy()
        with oxitest.raises(
            AttributeError, match="only available during a test session"
        ):
            proxy.db
    finally:
        _fixtures_registry_var.reset(token)
