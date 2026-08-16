"""A Test Item that writes JSON to fd 1 must not remove a Test Item from the report.

The worker writes protocol lines to file descriptor 1 and a Test Item can write
to the same descriptor. Before #2143 the coordinator counted any valid JSON line
as a test result and discarded one real result behind it, and the run exited 0.

#2010 fixed the same defect for a line that is not JSON. These tests pin the
branch for a line that is valid JSON.
"""

from dataclasses import dataclass

import oxitest
from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ

_OTHER_MODULE = "def test_c1(): assert True, 'c1'\ndef test_c2(): assert True, 'c2'\n"


def _suite(statement: str) -> str:
    """A three-item module whose middle Test Item writes to fd 1."""
    return (
        "import json\n"
        "import sys\n\n"
        "def test_b1(): assert True, 'b1'\n\n"
        "def test_b2():\n"
        f"    {statement}\n"
        "    assert True, 'b2'\n\n"
        "def test_b3(): assert True, 'b3'\n"
    )


@dataclass(frozen=True)
class StrayLineCase:
    """One shape a Test Item can write to file descriptor 1."""

    statement: str


# Both shapes are needed. A bare scalar is not a JSON object, so `WireEnvelope`
# deserialization fails and the fallback used to decide. An object IS a JSON
# object, so the field default used to decide. A fix to one half passes the
# other half's case.
@oxitest.parametrize(
    bare_scalar=StrayLineCase("print(42)"),
    json_object=StrayLineCase('print(json.dumps({"user": 1}))'),
    lowercase_true=StrayLineCase('sys.stdout.write("true\\n")'),
)
def test_a_json_line_keeps_every_test_in_the_report(
    tmp: TempDir,
    statement: str,
) -> None:
    """A stray JSON line is dropped, and all five Test Items are reported."""
    # Arrange: five Test Items in two modules, one of them writing to fd 1.
    (tmp / "test_b.py").write_text(_suite(statement), encoding="utf-8")
    (tmp / "test_c.py").write_text(_OTHER_MODULE, encoding="utf-8")

    # Act
    out, _, rc = helpers.run_oxitest(tmp, "-n", "2")

    # Assert
    integ.assert_passed(out, rc, count=5)


def test_the_serial_path_reports_the_same_count(tmp: TempDir) -> None:
    """The serial path has no drain and no result slot, so it is the control."""
    # Arrange
    (tmp / "test_b.py").write_text(
        _suite('print(json.dumps({"user": 1}))'), encoding="utf-8"
    )
    (tmp / "test_c.py").write_text(_OTHER_MODULE, encoding="utf-8")

    # Act
    out, _, rc = helpers.run_oxitest(tmp, "--serial")

    # Assert
    integ.assert_passed(out, rc, count=5)
