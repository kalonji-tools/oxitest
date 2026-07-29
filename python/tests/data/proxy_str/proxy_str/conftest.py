from __future__ import annotations

import oxitest

fixtures = oxitest.Fixtures(name="proxy_str_shared")


@fixtures.fixture(shared=True)
def dsn() -> str:
    return "pg://db"
