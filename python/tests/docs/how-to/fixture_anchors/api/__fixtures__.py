"""Fixture declarations backing the `@oxi.fixture` examples in the user docs.

Anchored at ``api/``: everything declared here is visible from this directory
and every directory below it, and from nowhere else (ADR-0009 Rule 3). The
namespace ``api`` is the directory's own basename, which is why the tree lives
under ``fixture_anchors/`` rather than beside the legacy examples in
``how-to/fixtures/`` — ``fixtures`` is listed in ``norecursedirs``, so nothing
below that directory is collected by the doc-example run.

Pages sourcing anchors from this file:

- ``docs/user/how-to/use-fixtures.md``
- ``docs/user/how-to/use-async-tests.md``
- ``docs/user/reference/python-api/fixture-declaration.md``
- ``docs/user/reference/python-api/builtins.md``
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator

from oxitest import TestContext


class Pool:
    """Stub connection pool for doc examples."""

    def __init__(self, *, url: str) -> None:
        self.url = url
        self.closed = False

    def close(self) -> None:
        self.closed = True


class Client:
    """Stub async client for the async-fixture doc examples."""

    def __init__(self) -> None:
        self.closed = False

    @classmethod
    async def connect(cls) -> Client:
        await asyncio.sleep(0)
        return cls()

    async def aclose(self) -> None:
        await asyncio.sleep(0)
        self.closed = True


def create_schema(name: str) -> None:
    """Stub — the ``ctx-fixture`` example needs a real call, not a placeholder."""


def drop_schema(name: str) -> None:
    """Stub — registered as a finalizer by the ``ctx-fixture`` example."""


# fmt: off
# --8<-- [start:declare-fixture]
import oxitest as oxi


@oxi.fixture(lifetime="function")
def tenant() -> str:
    return "acme"
# --8<-- [end:declare-fixture]

# --8<-- [start:yield-teardown]
@oxi.fixture(lifetime="function")
def audit_log() -> Iterator[list[str]]:
    entries: list[str] = []
    yield entries
    entries.clear()
# --8<-- [end:yield-teardown]

# --8<-- [start:fixture-dependency]
@oxi.fixture(lifetime="function")
def request_headers(tenant: oxi.Fixture[str]) -> dict[str, str]:
    return {"X-Tenant": tenant}
# --8<-- [end:fixture-dependency]

# --8<-- [start:module-lifetime]
@oxi.fixture(lifetime="module")
def pool() -> Iterator[Pool]:
    resource = Pool(url="postgres://localhost/test")
    yield resource
    resource.close()
# --8<-- [end:module-lifetime]

# --8<-- [start:async-function-lifetime]
@oxi.fixture(lifetime="function")
async def request_id() -> str:
    await asyncio.sleep(0)
    return "req-42"
# --8<-- [end:async-function-lifetime]

# --8<-- [start:async-module-lifetime]
@oxi.fixture(lifetime="module")
async def client() -> AsyncIterator[Client]:
    conn = await Client.connect()
    yield conn
    await conn.aclose()
# --8<-- [end:async-module-lifetime]

# --8<-- [start:async-arrange-fixture]
@oxi.fixture(lifetime="function")
async def each_txn() -> AsyncIterator[None]:
    # Setup runs on the per-test loop.
    yield
    # Teardown runs on the same loop, after the test body.
# --8<-- [end:async-arrange-fixture]

# --8<-- [start:ctx-fixture]
@oxi.fixture(lifetime="function")
def db_schema(ctx: TestContext) -> str:
    schema = "test_schema"
    create_schema(schema)
    ctx.addfinalizer(lambda: drop_schema(schema))
    return schema
# --8<-- [end:ctx-fixture]
# fmt: on
