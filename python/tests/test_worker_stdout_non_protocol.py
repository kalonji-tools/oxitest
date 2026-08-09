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

_RAW_BYTES = """\
import os


def test_writes_raw_bytes_to_fd1() -> None:
    os.write(1, b"\\xff\\xfe not utf-8\\n")
    assert True, "the write above is the point; this test must still be reported"


def test_after_the_bad_byte() -> None:
    assert True, "same worker as the bad byte — it must not be collateral damage"
"""

_UNCAPTURED_CHILD = """\
import subprocess
import sys

# 0xe9 is an accented letter in Latin-1 and cp1252, and is not valid UTF-8 on
# its own. Any console tool printing one under a non-UTF-8 codepage sends it.
_PRINTS_LATIN1 = "import sys; sys.stdout.buffer.write(b'\\\\xe9 raw\\\\n')"


def test_spawns_a_tool_without_capturing_it() -> None:
    subprocess.run([sys.executable, "-c", _PRINTS_LATIN1], check=True)
    assert True, "the spawned tool's uncaptured output is the point"
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
    assert "ignoring non-protocol line on worker stdout" in output, (
        f"this line is emitted only on the worker path, so it is also the proof "
        f"that the run was parallel. Serial mode has no pipe and cannot reach "
        f"the defect, so without this the test passes whatever the code does — "
        f"measured: the same project run serially reports 2 passed on an "
        f"unfixed tree.\noutput:\n{output}"
    )
    assert returncode == 0, (
        f"a passing suite must exit 0; this asserts the run reached a verdict "
        f"at all rather than erroring.\noutput:\n{output}"
    )


def test_raw_bytes_on_fd1_do_not_kill_the_worker(tmp: TempDir) -> None:
    """An undecodable byte costs one line, not the worker's whole stream."""
    # Arrange
    root = _project(tmp, "test_raw.py", _RAW_BYTES)

    # Act
    stdout, stderr, returncode = helpers.run_oxitest(root, "-n", "2")
    output = stdout + stderr

    # Assert
    assert "Worker subprocess exited unexpectedly" not in output, (
        f"the worker was alive and its tests passed. Reporting process death "
        f"sends the reader to look for a crash that never happened.\noutput:\n{output}"
    )
    assert "2 passed" in output, (
        f"both tests passed, and both belong to the worker that read the bad "
        f"byte. Asserting on the second is what proves the blast radius is one "
        f"line and not the rest of the stream.\noutput:\n{output}"
    )
    assert "invalid UTF-8 on worker stdout" in output, (
        f"an operator must be able to tell 'invalid UTF-8 from this worker' "
        f"from 'this worker exited'. Without this line the run is correct and "
        f"silent, and whoever reads it later has nothing to search for."
        f"\noutput:\n{output}"
    )
    assert "not utf-8" in output, (
        f"the decodable remainder of the mangled line must reach the operator. "
        f"Swallowing the whole line also lets both results through, so the two "
        f"assertions above cannot tell 'replaced' from 'dropped' — a mutant "
        f"that returns an empty string survives all of them.\noutput:\n{output}"
    )
    assert returncode == 0, (
        f"nothing failed, so the run must exit 0.\noutput:\n{output}"
    )


def test_an_uncaptured_child_process_does_not_kill_the_worker(tmp: TempDir) -> None:
    """A child inherits fd 1; its cp1252 output must not break the pipe."""
    # Arrange
    root = _project(tmp, "test_sub.py", _UNCAPTURED_CHILD)

    # Act
    stdout, stderr, returncode = helpers.run_oxitest(root, "-n", "2")
    output = stdout + stderr

    # Assert
    assert "Worker subprocess exited unexpectedly" not in output, (
        f"spawning a tool without capturing its output is ordinary practice, "
        f"and must not be reported as a crash.\noutput:\n{output}"
    )
    assert "BrokenPipeError" not in output, (
        f"the cascade is the second half of this defect: dropping the receiver "
        f"closed the pipe, so the worker's next write killed it for real — "
        f"after it had already been blamed for dying.\noutput:\n{output}"
    )
    assert "1 passed" in output, (
        f"the test passed and must be reported as passing.\noutput:\n{output}"
    )
    assert "invalid UTF-8 on worker stdout" in output, (
        f"this line is emitted only by the worker reader, so it is also the "
        f"proof that the run was parallel. Serial mode has no pipe and cannot "
        f"reach the defect, so without this the test passes whatever the code "
        f"does.\noutput:\n{output}"
    )
    assert returncode == 0, (
        f"nothing failed, so the run must exit 0.\noutput:\n{output}"
    )
