from __future__ import annotations

from rootdir_import_nested.helpers import label


def test_import_resolves_from_a_subpackage() -> None:
    assert label() == "nested", (
        "the import spelling must not depend on which subset of the suite was "
        "selected — deriving the sys.path entry from the collected files "
        "instead of the project root would break this on a narrowed run"
    )
