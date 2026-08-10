"""End-to-end deadline ownership (#2001).

These drive a real oxitest run rather than calling the wrapper directly. The
defect's whole shape is that the in-process view looks fine while the reported
outcome is wrong, so the outcome is what has to be asserted.
"""

from __future__ import annotations

import signal

import oxitest as oxi
from oxitest import TempDir
from tests import helpers

_PYPROJECT = """\
[project]
name = "deadline_probe"
version = "0.0.0"

[tool.oxitest]
timeout = 3
"""

#: The defect is Unix-only: Windows delivers a deadline with a per-test
#: `threading.Timer` and has no process-global slot for test code to write, and
#: no `signal.alarm` to write it with. A probe that clears the timer therefore
#: cannot be expressed on Windows at all -- the run dies with AttributeError.
_NO_SHARED_TIMER = not hasattr(signal, "alarm")
_NO_SHARED_TIMER_REASON = (
    "the deadline is only takeable where one process-global timer delivers it;"
    " Windows uses a per-test timer and has no signal.alarm"
)

#: A body that reaches Python bytecode boundaries, so BOTH arms can interrupt
#: it. `time.sleep` is a blocking C call, and the ctypes arm fires only at a
#: bytecode boundary -- a sleeping body is not bounded there
#: (`bounds_blocking_calls = False`), so it measures the platform difference
#: instead of the deadline.
_SPIN_FOR_30S = (
    "    deadline = time.monotonic() + 30\n"
    "    while time.monotonic() < deadline:\n"
    "        pass\n"
)


@oxi.mark.skip(when=_NO_SHARED_TIMER, reason=_NO_SHARED_TIMER_REASON)
def test_a_test_that_clears_the_timer_is_reported_as_warned(tmp: TempDir) -> None:
    """Two lines of ordinary user code void the deadline, and it must be reported.

    Before #2001 this test slept past its 3s deadline and reported passed, with
    nothing in the output to say the deadline had not been enforced.
    """
    (tmp / "pyproject.toml").write_text(_PYPROJECT)
    (tmp / "test_probe.py").write_text(
        "import signal\n"
        "import time\n"
        "\n"
        "def test_clears_its_own_deadline():\n"
        "    signal.alarm(1)\n"
        "    signal.alarm(0)\n"
        "    time.sleep(5)\n"
    )
    out, _, rc = helpers.run_oxitest(tmp, "-n", "1")
    assert "warning" in out, (
        "a test that cleared its deadline must be reported, not counted as a clean"
        f" pass -- the pass is unearned because the deadline never applied. Got {out!r}"
    )
    assert rc == 0, (
        "a cleared deadline is a report, not a failure: oxitest did not observe a"
        f" failure, only that it could not observe. Got exit {rc}"
    )


def test_a_nested_run_leaves_the_enclosing_test_its_deadline(tmp: TempDir) -> None:
    """The instrument claim: the enclosing test times out, and it is NAMED.

    Before #2001 the nested run's __exit__ destroyed the enclosing deadline, so
    the blocking call that follows ran unbounded until the coordinator watchdog
    killed the worker and errored every test in flight -- naming none of them.
    """
    (tmp / "pyproject.toml").write_text(_PYPROJECT)
    (tmp / "test_probe.py").write_text(
        "import time\n"
        "\n"
        "from oxitest._bridge._timeout import make_timeout_wrapper\n"
        "from oxitest._bridge.result import PassedResult\n"
        "\n"
        "def test_nests_then_blocks():\n"
        "    wrapper = make_timeout_wrapper(1)\n"
        "    wrapper(PassedResult)\n" + _SPIN_FOR_30S
    )
    out, _, rc = helpers.run_oxitest(tmp, "-n", "1")
    assert "test_nests_then_blocks" in out, (
        "the enclosing test must be named. An anonymous worker kill is exactly the"
        f" evidence three occurrences of this bug failed to produce. Got {out!r}"
    )
    assert "Timed out" in out, (
        "the enclosing 3s deadline must survive the nested run and fire; without it"
        f" the 30s sleep is unbounded and the watchdog kills the worker. Got {out!r}"
    )
    assert rc != 0, (
        "a test cut by its deadline is a failing run; exit 0 here would mean the"
        f" timeout was reported in the output but not in the exit code. Got {rc}"
    )


def test_a_deadline_does_not_leak_into_the_next_test(tmp: TempDir) -> None:
    """A timer armed by one test must not reach the next test in the same worker.

    Save-and-restore deliberately arms a timer that outlives an inner scope, so
    leaking across the test boundary is a defect this fix could introduce. This
    is the dimension the stage-4 claims audit named as unreached.
    """
    (tmp / "pyproject.toml").write_text(_PYPROJECT)
    (tmp / "test_probe.py").write_text(
        "import time\n"
        "\n"
        "from oxitest._bridge._timeout import make_timeout_wrapper\n"
        "from oxitest._bridge.result import PassedResult\n"
        "\n"
        "def test_a_nests():\n"
        "    wrapper = make_timeout_wrapper(1)\n"
        "    wrapper(PassedResult)\n"
        "\n"
        "def test_b_runs_normally():\n"
        "    time.sleep(1.5)\n"
    )
    out, _, rc = helpers.run_oxitest(tmp, "-n", "1")
    assert rc == 0, (
        "the second test must get its own full 3s deadline; a timer left armed by"
        f" the first would cut it spuriously. Got exit {rc}, output {out!r}"
    )
    assert "2 passed" in out, (
        "both tests must pass -- a spurious interrupt in the second test is how a"
        f" leaked timer presents. Got {out!r}"
    )
