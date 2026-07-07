"""Integration tests: --show-locals and --show-internals wire behavior."""

from oxitest import TempDir, helpers


def test_show_locals_displays_variables(tmp: TempDir) -> None:
    """--show-locals shows local variable values in diagnostic."""
    (tmp / "test_locals.py").write_text(
        "def test_with_locals():\n"
        "    x = 42\n"
        "    y = 'hello'\n"
        "    assert x == 0, 'wrong value'\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--show-locals")
    helpers.integ.assert_failed(out, rc)
    helpers.integ.assert_contains(out, "x", "42")


def test_show_internals_shows_bridge_frames(tmp: TempDir) -> None:
    """--show-internals includes oxitest internal frames in trace."""
    (tmp / "test_internal.py").write_text(
        "def test_fail():\n    assert False, 'boom'\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--show-internals")
    helpers.integ.assert_failed(out, rc)
    helpers.integ.assert_contains(out, "oxitest/")


def test_default_hides_internals(tmp: TempDir) -> None:
    """Without --show-internals, internal frames are filtered."""
    (tmp / "test_hidden.py").write_text("def test_fail():\n    assert False, 'boom'\n")
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_failed(out, rc)
    helpers.integ.assert_excludes(out, "oxitest/_bridge")


def test_default_no_locals(tmp: TempDir) -> None:
    """Without --show-locals, no local variables appear."""
    (tmp / "test_no_locals.py").write_text(
        "def test_with_locals():\n    secret = 'hidden'\n    assert False, 'fail'\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_failed(out, rc)
    helpers.integ.assert_excludes(out, "secret")
