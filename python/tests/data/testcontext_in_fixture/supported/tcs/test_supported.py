"""Four supported uses of ``TestContext``, none of which #1874 may break."""

from __future__ import annotations

from pathlib import Path

from oxitest import Fixture, Fixtures, TestContext


def test_a_fixture_may_register_a_finalizer(schema: Fixture[str]) -> None:
    assert schema == "test_schema", (
        "the fixture that called ctx.addfinalizer must still produce its "
        "value; a guard that caught addfinalizer would fail here instead. "
        "That the finalizer actually RAN is asserted by the runner, which "
        "reads the log this fixture writes after teardown"
    )


def test_a_fixture_may_read_module_path(where_i_am: Fixture[str]) -> None:
    assert Path(where_i_am).name == "test_supported.py", (
        "module_path is not test identity and must keep answering inside a "
        "fixture; refusing it would break the scope-bucket value fixtures are "
        "entitled to see"
    )


def test_a_test_reads_its_own_identity(ctx: TestContext) -> None:
    assert ctx.name == "test_a_test_reads_its_own_identity", (
        "a test's own ctx must be completely unaffected — this is the case the "
        "identity accessors exist for, and the guard has to be invisible to it"
    )
    assert ctx.node_id.endswith("::test_a_test_reads_its_own_identity"), (
        "node_id is the field the proxy route used to lose; asserting it here "
        "is what makes the proxy comparison below a comparison of two real "
        "answers rather than of two empty strings"
    )


def test_the_proxy_route_reports_the_same_identity(
    ctx: TestContext, fx: Fixtures
) -> None:
    assert fx.oxi.ctx.node_id == ctx.node_id, (
        "fx.oxi.ctx used to rebuild a synthetic TestMeta from module_path and "
        "fn_name and drop node_id, so this returned '' from a real test whose "
        "node id was in scope the whole time"
    )
    assert fx.oxi.ctx.marks == ctx.marks, (
        "markers were dropped by the same rebuild; comparing against the "
        "test's own ctx is what makes the two routes provably one answer"
    )
