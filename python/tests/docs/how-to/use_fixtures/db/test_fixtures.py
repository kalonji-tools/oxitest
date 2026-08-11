"""Test functions for the use-fixtures how-to guide."""

import oxitest
from oxitest import Fixture, Fixtures


# fmt: off
# --8<-- [start:inject-fixture]
def test_sum(sample_data: Fixture[list[int]]) -> None:
    assert sum(sample_data) == 15, "fixture should inject sample_data"
# --8<-- [end:inject-fixture]

# --8<-- [start:namespace-test]
def test_api_writes_to_db(fx: Fixtures) -> None:
    response = fx.http.client.post("/users", json={"name": "Alice"})
    row = fx.db.conn.query("SELECT name FROM users WHERE id = ?", response.json()["id"])
    assert row["name"] == "Alice", "should read back written data"
# --8<-- [end:namespace-test]

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
