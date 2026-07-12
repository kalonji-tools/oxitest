"""Fixtures for reference doc examples."""

import oxitest

fx = oxitest.Fixtures()


@fx.fixture(shared=True)
def config() -> dict[str, str]:
    return {"env": "test"}
