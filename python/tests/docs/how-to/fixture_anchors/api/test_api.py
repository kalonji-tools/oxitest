"""Tests for the `__fixtures__.py` examples in the user docs.

Anchors here back `use-fixtures.md`, `use-async-tests.md` and
`reference/python-api/builtins.md`.
"""

import oxitest as oxi
from oxitest import Fixture, Fixtures, TestContext


# fmt: off
# --8<-- [start:proxy-access]
def test_tenant_is_resolved(fx: Fixtures) -> None:
    assert fx.api.tenant == "acme", "fx.<namespace>.<name> reaches the declaration"
# --8<-- [end:proxy-access]

# --8<-- [start:injection-access]
def test_tenant_is_injected(tenant: Fixture[str]) -> None:
    assert tenant == "acme", "Fixture[T] injects the same fixture"
# --8<-- [end:injection-access]

# --8<-- [start:dependency-test]
def test_headers_carry_the_tenant(fx: Fixtures) -> None:
    assert fx.api.request_headers["X-Tenant"] == "acme", (
        "request_headers resolved tenant itself — the test never asked for it"
    )
# --8<-- [end:dependency-test]

# --8<-- [start:teardown-test]
def test_audit_log_records_the_test(fx: Fixtures) -> None:
    fx.api.audit_log.append("created user")
    assert fx.api.audit_log == ["created user"], "teardown clears it after the test"
# --8<-- [end:teardown-test]

# --8<-- [start:module-lifetime-test]
def test_pool_is_open(fx: Fixtures) -> None:
    assert not fx.api.pool.closed, "the pool is torn down after this module, not now"
# --8<-- [end:module-lifetime-test]

# --8<-- [start:async-proxy-access]
async def test_query(fx: Fixtures) -> None:
    request_id = await fx.api.request_id
    client = await fx.api.client
    assert request_id == "req-42", "await reaches a function-lifetime async fixture"
    assert not client.closed, "the module-lifetime client outlives this test"
# --8<-- [end:async-proxy-access]

# --8<-- [start:async-injection]
async def test_request_id_is_injected(request_id: Fixture[str]) -> None:
    assert request_id == "req-42", "Fixture[T] needs no await"
# --8<-- [end:async-injection]

# --8<-- [start:async-arrange]
@oxi.arrange("each_txn")
async def test_async_write(fx: Fixtures) -> None:
    assert await fx.api.request_id == "req-42", "the arranged fixture ran around this"
# --8<-- [end:async-arrange]

# --8<-- [start:ctx-test]
def test_create_user(db_schema: Fixture[str], ctx: TestContext) -> None:
    # ctx.name     → "test_create_user"
    # ctx.node_id  → ".../test_api.py::test_create_user"
    # ctx.marks    → frozenset()
    # ctx.param_id → None (not parametrized)
    assert db_schema == "test_schema", "the fixture's value reaches the test"
    assert ctx.name == "test_create_user", "ctx names the test it is injected into"
# --8<-- [end:ctx-test]
# fmt: on


def test_module_lifetime_is_one_instance_per_module(fx: Fixtures) -> None:
    """Two accesses in the same module must hand back the same object."""
    first = fx.api.pool
    second = fx.api.pool
    assert first is second, (
        "a module-lifetime fixture rebuilt per access would still inject a "
        "usable Pool, so only object identity proves the tier is honoured"
    )
