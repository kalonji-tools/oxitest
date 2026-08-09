"""Three supported uses of ``TestContext``, none of which #1874 may break."""

from __future__ import annotations

from pathlib import Path

from oxitest import Fixture, TestContext


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
