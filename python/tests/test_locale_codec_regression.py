"""End-to-end regressions for the locale-codec defect (#2004).

Both tests set PYTHONIOENCODING on the child only. On Windows this is the real
default; everywhere else it is how the defect is made reproducible, which is
the whole reason the fix carries no sys.platform branch.
"""

from __future__ import annotations

import os
import subprocess
import sysconfig
from pathlib import Path

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


_FDCAPTURE_TEST = '''\
from oxitest import FdCapture


def test_roundtrip(cap: FdCapture) -> None:
    """Non-ASCII output must survive the fd round-trip."""
    print("café")
    out = cap.readouterr().out
    # Strip the line break rather than pinning it. FdCapture returns the fd's
    # raw bytes, so Windows delivers "café\\r\\n" here where POSIX delivers
    # "café\\n" — a real difference from StdCapture, but not this test's claim.
    # What is under test is that the non-ASCII character survives at all.
    assert out.rstrip("\\r\\n") == "café", (
        f"FdCapture decodes the fd as UTF-8; a locale-codec sys.stdout wrote "
        f"cp1252 bytes into it, so this comes back replaced. got {out!r}"
    )
'''


def test_fdcapture_round_trips_non_ascii_output(tmp: TempDir) -> None:
    """A test's own non-ASCII output must not come back replaced."""
    # Arrange — serial, so the test runs in the top-level `python -m oxitest`
    # process. That is the entry point this task fixes; -n would move it into a
    # worker and test the other call site instead.
    project = tmp / "fdcap"
    project.mkdir()
    (project / "test_fd.py").write_text(_FDCAPTURE_TEST, encoding="utf-8")

    # Act
    stdout, stderr, returncode = helpers.run_oxitest(project, env=_CP1252_ENV)

    # Assert
    assert "1 passed" in stdout, (
        "FdCapture reads the fd and decodes it as UTF-8 unconditionally, so a "
        "cp1252 sys.stdout silently replaces every non-ASCII character a test "
        f"prints. stdout: {stdout!r} stderr: {stderr!r}"
    )
    assert returncode == 0, (
        f"expected a clean run, got {returncode} — stderr: {stderr!r}"
    )


def _console_script() -> Path:
    """The installed ``oxitest`` entry point, found without relying on PATH."""
    scripts = Path(sysconfig.get_path("scripts"))
    return scripts / ("oxitest.exe" if os.name == "nt" else "oxitest")


def test_the_console_script_declares_utf8_too(tmp: TempDir) -> None:
    """`oxitest` the command, not just `python -m oxitest`.

    pyproject.toml routes `[project.scripts] oxitest` at `oxitest:main`, which
    is the whole reason the declaration lives in `main()` rather than in
    `__main__.py`. This is the suite's only test that runs the installed
    binary — every other one invokes `python -m oxitest`, which reaches
    `main()` through `__main__.py` and so cannot tell the two placements
    apart (#2004).
    """
    # Arrange
    script = _console_script()
    assert script.exists(), (
        f"no console script at {script} — this test asserts the packaging "
        "route works, so a missing binary means the install is wrong, not "
        "that the test should be skipped"
    )
    project = tmp / "fdcap_console"
    project.mkdir()
    (project / "test_fd.py").write_text(_FDCAPTURE_TEST, encoding="utf-8")

    # Act — the same FdCapture probe, reached through the packaging entry point.
    result = subprocess.run(
        [str(script), str(project), "--color", "never"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_CP1252_ENV,
        timeout=60,
        check=False,
    )

    # Assert
    assert "1 passed" in result.stdout, (
        "the console script reaches oxitest.main() — __main__.py runs only "
        "under `python -m`, so a declaration placed there leaves this path "
        f"unfixed. stdout: {result.stdout!r} stderr: {result.stderr!r}"
    )
