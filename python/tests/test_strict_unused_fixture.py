"""The unused-fixture check reaches every ADR-0009 Rule 5 declaration home.

`src/inspect/signals.rs` carries the same check for `oxitest inspect`, and #1722
repaired it there. The strict-mode check never received that repair: it filtered
on a declaration path ending in `conftest.py`, which #1720 made unsatisfiable
(#2200).
"""

from __future__ import annotations

from pathlib import Path

from tests import helpers

_HOMES = Path(__file__).parent / "data" / "strict_unused_homes"


def test_unused_fixture_is_reported_in_all_three_declaration_homes() -> None:
    """Each of `__fixtures__.py`, `__init__.py` and a test module is a home."""
    # Act
    out, err, rc = helpers.run_oxitest(None, "--warnings", "--serial", cwd=str(_HOMES))

    # Assert
    combined = out + err
    for name in (
        "never_used_in_fixtures_file",
        "never_used_in_init",
        "never_used_inline",
    ):
        assert name in combined, (
            f"'{name}' is declared with @oxi.fixture and no test references it, "
            f"so the unused-fixture check must name it. A filter keyed on a "
            f"file name cannot see any declaration home after #1720 retired "
            f"conftest.py, and a filter keyed on __fixtures__.py would skip "
            f"the other two.\nrc={rc}\n{combined}"
        )

    assert rc == 3, (
        f'the project sets strict = "abort", so a violation must stop the run '
        f"before execution and exit 3; a run that reaches the test and passes "
        f"means the check produced nothing.\nrc={rc}\n{combined}"
    )
