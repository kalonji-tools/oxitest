"""Fixtures for reference doc examples."""

import oxitest


@oxitest.fixture(lifetime="module")
def config() -> dict[str, str]:
    return {"env": "test"}
