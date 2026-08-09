"""End-to-end regressions for the locale-codec defect (#2004).

Both tests set PYTHONIOENCODING on the child only. On Windows this is the real
default; everywhere else it is how the defect is made reproducible, which is
the whole reason the fix carries no sys.platform branch.
"""

from __future__ import annotations

import os

from oxitest import TempDir
from tests import helpers

_CP1252_ENV = {**os.environ, "PYTHONIOENCODING": "cp1252"}

_PASSING_TEST = """\
def test_ok() -> None:
    assert True, "the run must report this test, not discard it"
"""


def test_a_non_ascii_project_path_still_reports_its_results(tmp: TempDir) -> None:
    """A non-ASCII path must not silently zero out the whole run."""
    # Arrange — the directory name is generated, never committed: a non-ASCII
    # path in the tree would drag in macOS core.precomposeunicode and Windows
    # checkout behaviour, neither of which this is about.
    project = tmp / "prüf"
    project.mkdir()
    (project / "test_ok.py").write_text(_PASSING_TEST, encoding="utf-8")

    # Act — -n 2 forces the worker subprocess path, which is where stdin is read.
    stdout, stderr, returncode = helpers.run_oxitest(
        project, "-n", "2", env=_CP1252_ENV
    )

    # Assert
    assert "1 passed" in stdout, (
        "a worker that decodes its task with the locale codec sees a mangled "
        "node id, so drain discards every result and the run reports "
        f"'no tests ran' — with exit 0. stdout: {stdout!r} stderr: {stderr!r}"
    )
    assert returncode == 0, (
        f"expected a clean run, got {returncode} — stderr: {stderr!r}"
    )
