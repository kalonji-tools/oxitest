"""Integration tests: --show-locals and --show-internals wire behavior."""

import os

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ

# Frames render with the platform separator. Spelling these "oxitest/" made the
# positive assertion fail on Windows and — worse — made the negative one below
# pass vacuously there, since "oxitest/_bridge" is a string Windows output can
# never contain (#1989).
_OXITEST_FRAME = f"oxitest{os.sep}"
_BRIDGE_FRAME = f"oxitest{os.sep}_bridge"


def test_show_locals_displays_variables(tmp: TempDir) -> None:
    """--show-locals shows local variable values in diagnostic."""
    (tmp / "test_locals.py").write_text(
        "def test_with_locals():\n"
        "    x = 42\n"
        "    y = 'hello'\n"
        "    assert x == 0, 'wrong value'\n",
        encoding="utf-8",
    )
    out, _, rc = helpers.run_oxitest(tmp, "--show-locals")
    integ.assert_failed(out, rc)
    integ.assert_contains(out, "x", "42")


def test_show_internals_shows_bridge_frames(tmp: TempDir) -> None:
    """--show-internals includes oxitest internal frames in trace."""
    (tmp / "test_internal.py").write_text(
        "def test_fail():\n    assert False, 'boom'\n", encoding="utf-8"
    )
    out, _, rc = helpers.run_oxitest(tmp, "--show-internals")
    integ.assert_failed(out, rc)
    integ.assert_contains(out, _OXITEST_FRAME)


def test_default_hides_internals(tmp: TempDir) -> None:
    """Without --show-internals, internal frames are filtered."""
    (tmp / "test_hidden.py").write_text(
        "def test_fail():\n    assert False, 'boom'\n", encoding="utf-8"
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_failed(out, rc)
    integ.assert_excludes(out, _BRIDGE_FRAME)


def test_default_no_locals(tmp: TempDir) -> None:
    """Without --show-locals, no local variables appear."""
    (tmp / "test_no_locals.py").write_text(
        "def test_with_locals():\n    secret = 'hidden'\n    assert False, 'fail'\n",
        encoding="utf-8",
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_failed(out, rc)
    integ.assert_excludes(out, "secret")
