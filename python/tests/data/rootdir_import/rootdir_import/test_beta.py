from __future__ import annotations

from rootdir_import.helpers import make_user


def test_second_module_resolves_the_same_import() -> None:
    user = make_user()
    assert user["name"] == "test", (
        "reachability must hold from every test module, not just the first one imported"
    )
