"""Integration tests: --show-locals and --show-internals wire behavior."""

from conftest import helpers
from oxitest import TempDir


def test_show_locals_displays_variables(tmp: TempDir):
    """--show-locals shows local variable values in diagnostic."""
    (tmp / "test_locals.py").write_text(
        "def test_with_locals():\n"
        "    x = 42\n"
        "    y = 'hello'\n"
        "    assert x == 0, 'wrong value'\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--show-locals")
    assert rc == 1, f"should fail, got {rc}"
    assert "x" in out, f"local 'x' should appear: {out!r}"
    assert "42" in out, f"local value '42' should appear: {out!r}"


def test_show_internals_shows_bridge_frames(tmp: TempDir):
    """--show-internals includes oxitest internal frames in trace."""
    (tmp / "test_internal.py").write_text(
        "def test_fail():\n    assert False, 'boom'\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--show-internals")
    assert rc == 1, f"should fail, got {rc}"
    assert "oxitest/" in out, f"internal frame should appear: {out!r}"


def test_default_hides_internals(tmp: TempDir):
    """Without --show-internals, internal frames are filtered."""
    (tmp / "test_hidden.py").write_text("def test_fail():\n    assert False, 'boom'\n")
    out, _, rc = helpers.common.run_oxitest(tmp)
    assert rc == 1, f"should fail, got {rc}"
    assert "oxitest/_bridge" not in out, f"internal frame should be hidden: {out!r}"


def test_default_no_locals(tmp: TempDir):
    """Without --show-locals, no local variables appear."""
    (tmp / "test_no_locals.py").write_text(
        "def test_with_locals():\n    secret = 'hidden'\n    assert False, 'fail'\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    assert rc == 1, f"should fail, got {rc}"
    assert "secret" not in out, f"locals should not appear by default: {out!r}"
