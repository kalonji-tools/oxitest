from __future__ import annotations

from oxitest import Fixtures


def test_gets_conn(fx: Fixtures) -> None:
    conn = fx.slice1_same_package.conn
    assert conn is not None, "fixture resolved through the new ModuleSource path"


def test_fresh_per_test(fx: Fixtures) -> None:
    conn = fx.slice1_same_package.conn
    assert conn is not None, "function-lifetime fixtures are fresh per test"
