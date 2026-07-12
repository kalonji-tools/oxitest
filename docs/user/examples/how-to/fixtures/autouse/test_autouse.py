"""Test that exercises the autouse fixture example.

The reset_database autouse fixture injects db and calls
db.execute("DELETE FROM users") in teardown. We verify the
fixture chain resolves by checking db was injected.
"""

from oxitest import Fixture


def test_autouse_injects_db(db: Fixture[object]) -> None:
    assert hasattr(db, "execute"), (
        "autouse fixture should inject db with execute method"
    )
    assert db.calls == [], "no calls yet — teardown runs after this test"
