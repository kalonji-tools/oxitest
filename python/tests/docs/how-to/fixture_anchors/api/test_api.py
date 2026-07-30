"""Tests for the `__fixtures__.py` examples in the use-fixtures how-to guide."""

from oxitest import Fixture, Fixtures


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
# fmt: on


def test_module_lifetime_is_one_instance_per_module(fx: Fixtures) -> None:
    """Two accesses in the same module must hand back the same object."""
    first = fx.api.pool
    second = fx.api.pool
    assert first is second, (
        "a module-lifetime fixture rebuilt per access would still inject a "
        "usable Pool, so only object identity proves the tier is honoured"
    )
