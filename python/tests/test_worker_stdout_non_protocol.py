"""Anything on a worker's stdout that is not a protocol message (#2010).

A worker's stdout IS the wire. Two defects met there. The reader read lines
into a ``String``, so one undecodable byte returned ``ErrorKind::InvalidData``,
which it treated as end-of-stream — the whole worker's result stream ended and
the drain blamed process death for a worker that was alive and passing. And the
drain counted a line it could not parse as a received result, so the real result
arrived after the drain had finished and was discarded.

The second is the quiet one, and it needs no encoding at all: an ordinary
``print()`` reached it, and the run reported "no tests ran" while exiting 0.

These tests run the real binary because both defects live between two
processes, which is the seam no unit test reaches. Projects are written inline
rather than kept under ``data/`` because two of them write deliberate garbage
to fd 1, which is not a thing to leave in a directory the main suite walks.
"""

from __future__ import annotations

from pathlib import Path

from oxitest import TempDir
from tests import helpers

_PYPROJECT = """\
[tool.oxitest]
testpaths = ["."]
python_files = ["test_*.py"]
min_parallel_tests = 1
"""

_PLAIN_PRINT = """\
def test_prints_plain_ascii() -> None:
    print("hello from a test")
    assert True, "the print is the point; this test must still be reported"
"""


def _project(tmp: TempDir, name: str, body: str) -> Path:
    """Scaffold a one-module project and return its root."""
    root = Path(tmp) / "proj"
    root.mkdir(parents=True)
    (root / name).write_text(body, encoding="utf-8")
    (root / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    return root


def test_a_plain_print_does_not_delete_the_result(tmp: TempDir) -> None:
    """An ordinary print() must not consume the slot its own result needs."""
    # Arrange
    root = _project(tmp, "test_print.py", _PLAIN_PRINT)

    # Act
    stdout, stderr, returncode = helpers.run_oxitest(root, "-n", "2")
    output = stdout + stderr

    # Assert
    assert "no tests ran" not in output, (
        f"the test ran and passed, and the run reported that nothing ran. The "
        f"stray line consumed the result slot, so the real result arrived after "
        f"the drain had finished and was discarded.\noutput:\n{output}"
    )
    assert "1 passed" in output, (
        f"the passing test must be reported as passing. The exit code alone "
        f"cannot catch this — the broken behaviour also exits 0, which is what "
        f"made it silent.\noutput:\n{output}"
    )
    assert returncode == 0, (
        f"a passing suite must exit 0; this asserts the run reached a verdict "
        f"at all rather than erroring.\noutput:\n{output}"
    )
