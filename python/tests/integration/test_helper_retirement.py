"""The helper surface is gone; plain import is what replaced it (#1788)."""

from __future__ import annotations

from pathlib import Path

import oxitest
from oxitest import TempDir
from tests import helpers

#: The #1780 acceptance suite. Its utility module is named ``helpers.py`` and is
#: reached by plain ``import`` — the replacement for what this PR deleted,
#: already demonstrated.
_ROOTDIR_SUITE = Path(__file__).parent.parent / "data" / "rootdir_import"


def test_stateless_utility_is_reachable_by_plain_import() -> None:
    """AC 4: a utility module in the test tree resolves via plain import.

    This is the property that made the helper registry unnecessary — if it
    regresses, deleting the registry left users with no replacement at all.

    Coverage of the full matrix (serial, parallel, inspect, doctest) lives in
    ``test_rootdir_import.py``. This asserts only the one property #1788
    depends on, so the retirement has a test that fails if its premise does.
    """
    # Act — no cwd= kwarg. ``python -m`` prepends CWD to sys.path, so passing
    # one would resolve the import by accident of the invocation instead.
    stdout, stderr, rc = helpers.run_oxitest(_ROOTDIR_SUITE)

    # Assert
    assert rc == 0, (
        f"the suite imports `rootdir_import.helpers` by plain import; a "
        f"non-zero exit means the retirement removed the registry without a "
        f"working replacement.\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )


def test_helpers_is_no_longer_an_accepted_query_resource(tmp: TempDir) -> None:
    """AC 1: clap no longer accepts `helpers` as a query resource."""
    # Arrange
    (tmp.path / "test_a.py").write_text(
        'def test_a() -> None:\n    assert True, "trivial"\n', encoding="utf-8"
    )

    # Act
    _out, err, rc = helpers.run_oxitest_subcmd(
        tmp, "query", "helpers", "--detail", "anything"
    )

    # Assert — the message is the real contract; the exit code is incidental.
    assert "invalid value 'helpers'" in err, (
        f"a retired resource kind must be rejected at parse time, or users "
        f"get an empty table instead of an error\nrc={rc}\n{err}"
    )


def test_helper_symbols_are_gone_from_the_public_api() -> None:
    """AC 2: nothing named helper survives on the oxitest namespace."""
    # Arrange / Act
    leaked = [name for name in dir(oxitest) if "helper" in name.lower()]

    # Assert
    assert leaked == [], (
        f"the helper concept is retired — a surviving export would let users "
        f"write against an API with no implementation behind it: {leaked}"
    )
