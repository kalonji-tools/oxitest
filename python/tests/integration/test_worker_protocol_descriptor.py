"""A capture fixture must not take the worker's protocol pipe with it.

`FdCapture` calls ``os.dup2`` on file descriptor 1 and `StdCapture` replaces
``sys.stdout``. In a worker both of those used to be how protocol lines left the
process, so a diagnostic emitted while such a fixture was active went into the
capture file, and the captured output came back holding protocol lines (#2147).

Every test here runs parallel. The serial path calls into Python over PyO3 and
has no pipe, so it cannot reach the defect — a serial run reported both
diagnostics on an unfixed tree.
"""

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ

_TEARDOWN_SUITE = """\
from oxitest import {fixture}, TestContext


def test_under_capture(cap: {fixture}, ctx: TestContext) -> None:
    def boom() -> None:
        raise RuntimeError("MARKER_UNDER_CAPTURE")

    ctx.addfinalizer(boom)
    assert True, "the diagnostic is emitted by the finalizer, after the body"


def test_without_capture(ctx: TestContext) -> None:
    def boom2() -> None:
        raise RuntimeError("MARKER_CONTROL")

    ctx.addfinalizer(boom2)
    assert True, "the control needs no capture fixture"
"""

_LEAK_SUITE = """\
from oxitest import {fixture}
from oxitest._bridge._diagnostic_collector import emit_diagnostic


def test_capture_sees_no_protocol_line(cap: {fixture}) -> None:
    emit_diagnostic("warning", "probe", "MARKER_IN_BODY")
    captured = cap.readouterr()
    assert "MARKER_IN_BODY" not in captured.out, (
        f"a protocol line reached the captured output; got {{captured.out!r}}"
    )
"""


def test_fd_capture_does_not_swallow_a_worker_diagnostic(tmp: TempDir) -> None:
    """The diagnostic emitted under FdCapture must reach the reporter."""
    # Arrange
    (tmp / "test_teardown.py").write_text(
        _TEARDOWN_SUITE.format(fixture="FdCapture"), encoding="utf-8"
    )

    # Act
    stdout, stderr, _ = helpers.run_oxitest(tmp, "-n", "2", "--warnings")
    output = stdout + stderr

    # Assert
    assert "MARKER_UNDER_CAPTURE" in output, (
        "FdCapture redirects fd 1, which is the worker's protocol pipe, so this "
        "diagnostic went into the capture file and the user never saw it "
        f"(#2147).\noutput:\n{output}"
    )
    assert "MARKER_CONTROL" in output, (
        "the control diagnostic proves the run reported diagnostics at all; "
        f"without it the assertion above passes on a silent run.\noutput:\n{output}"
    )


def test_std_capture_does_not_swallow_a_worker_diagnostic(tmp: TempDir) -> None:
    """StdCapture replaces sys.stdout, which was the same channel."""
    # Arrange
    (tmp / "test_teardown.py").write_text(
        _TEARDOWN_SUITE.format(fixture="StdCapture"), encoding="utf-8"
    )

    # Act
    stdout, stderr, _ = helpers.run_oxitest(tmp, "-n", "2", "--warnings")
    output = stdout + stderr

    # Assert
    assert "MARKER_UNDER_CAPTURE" in output, (
        f"the sys.stdout surface must close with the descriptor one.\noutput:\n{output}"
    )
    assert "MARKER_CONTROL" in output, (
        f"the control proves the run reported diagnostics at all.\noutput:\n{output}"
    )


def test_fd_capture_returns_no_protocol_line(tmp: TempDir) -> None:
    """A Test Item asserting on its own output must not read the wire protocol."""
    # Arrange
    (tmp / "test_leak.py").write_text(
        _LEAK_SUITE.format(fixture="FdCapture"), encoding="utf-8"
    )

    # Act
    stdout, stderr, returncode = helpers.run_oxitest(tmp, "-n", "2")

    # Assert
    integ.assert_passed(stdout + stderr, returncode, count=1)


def test_std_capture_returns_no_protocol_line(tmp: TempDir) -> None:
    """The same at the sys.stdout level."""
    # Arrange
    (tmp / "test_leak.py").write_text(
        _LEAK_SUITE.format(fixture="StdCapture"), encoding="utf-8"
    )

    # Act
    stdout, stderr, returncode = helpers.run_oxitest(tmp, "-n", "2")

    # Assert
    integ.assert_passed(stdout + stderr, returncode, count=1)
