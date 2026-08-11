"""Test functions for the use-fixtures how-to guide.

These sit in the `db` anchor package so that `fx.db.conn` is visible to them.
"""

import oxitest
from oxitest import Fixtures


# fmt: off
# --8<-- [start:fx-oxi-test]
def test_export(fx: Fixtures) -> None:
    result = fx.db.conn.export()
    (fx.oxi.tmp.path / "export.json").write_text(result, encoding="utf-8")
    assert fx.oxi.tmp.path.joinpath("export.json").exists(), "export file should exist"
# --8<-- [end:fx-oxi-test]

# --8<-- [start:arrange]
@oxitest.arrange("reset_database")
def test_insert_user() -> None:
    assert True, "reset_database fixture should run for side effects"
# --8<-- [end:arrange]

# --8<-- [start:arrange-multiple]
@oxitest.arrange("reset_database", "app_config")
def test_cold_start() -> None:
    assert True, "fixtures should run for side effects"
# --8<-- [end:arrange-multiple]
# fmt: on
