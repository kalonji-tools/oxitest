from __future__ import annotations

import oxitest as oxi

from rootdir_import.helpers import make_user


def test_declaration_file_import_resolved(
    seed_user: oxi.Fixture[dict[str, str]],
) -> None:
    assert seed_user["name"] == "seeded", (
        "the fixture's own module imported from the test tree at session "
        "setup; if the append happened after declaration loading this file "
        "would never have loaded at all"
    )


def test_absolute_import_resolves() -> None:
    user = make_user("alice")
    assert user == {"name": "alice", "kind": "user"}, (
        "a plain absolute import from the test tree must resolve — this is the "
        "capability that replaces the retired helper registry (#1700)"
    )
