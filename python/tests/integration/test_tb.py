"""Integration tests: --tb modes and --show-locals/--show-internals flags."""

from oxitest import TempDir, helpers


def test_tb_detail_shows_source_line(tmp: TempDir) -> None:
    """Default --tb=detail shows the assertion source line in bold."""
    (tmp / "test_fail.py").write_text("def test_fail():\n    assert 1 == 2\n")
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_failed(out, rc)
    helpers.integ.assert_contains(out, "assert 1 == 2")


def test_tb_detail_shows_box_chrome(tmp: TempDir) -> None:
    """Default --tb=detail shows the new box chrome."""
    (tmp / "test_fail.py").write_text("def test_fail():\n    assert 1 == 2\n")
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_failed(out, rc)
    helpers.integ.assert_contains(out, "┌", "└")
    # Old chrome should NOT appear
    helpers.integ.assert_excludes(out, "┌─", "└─")


def test_tb_line_shows_one_liner(tmp: TempDir) -> None:
    """--tb=line produces a single FAILED line."""
    (tmp / "test_fail.py").write_text("def test_fail():\n    assert 1 == 2\n")
    out, _, rc = helpers.common.run_oxitest(tmp, "--tb=line")
    helpers.integ.assert_failed(out, rc)
    helpers.integ.assert_contains(out, "FAILED")
    helpers.integ.assert_excludes(out, "┌")


def test_tb_no_suppresses_diagnostic(tmp: TempDir) -> None:
    """--tb=no shows no diagnostic block."""
    (tmp / "test_fail.py").write_text("def test_fail():\n    assert 1 == 2\n")
    out, _, rc = helpers.common.run_oxitest(tmp, "--tb=no")
    helpers.integ.assert_failed(out, rc)
    helpers.integ.assert_excludes(out, "assert 1 == 2", "┌")


def test_show_locals_conflict_with_tb_no(tmp: TempDir) -> None:
    """--show-locals with --tb=no is rejected."""
    (tmp / "test_pass.py").write_text("def test_pass():\n    pass\n")
    out, err, rc = helpers.common.run_oxitest(tmp, "--show-locals", "--tb=no")
    helpers.integ.assert_failed(out, rc)
    combined = out + err
    assert "requires" in combined.lower() or "detail" in combined.lower(), (
        f"error should mention detail requirement: {combined!r}"
    )


def test_show_internals_conflict_with_tb_line(tmp: TempDir) -> None:
    """--show-internals with --tb=line is rejected."""
    (tmp / "test_pass.py").write_text("def test_pass():\n    pass\n")
    out, err, rc = helpers.common.run_oxitest(tmp, "--show-internals", "--tb=line")
    helpers.integ.assert_failed(out, rc)
    combined = out + err
    assert "requires" in combined.lower() or "detail" in combined.lower(), (
        f"error should mention detail requirement: {combined!r}"
    )


def test_tb_detail_shows_diff(tmp: TempDir) -> None:
    """--tb=detail shows left/right diff for comparison failures."""
    (tmp / "test_diff.py").write_text("def test_diff():\n    assert 1 == 2\n")
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_failed(out, rc)
    assert "left:" in out or "- left:" in out, f"diff left missing: {out!r}"


def test_tb_detail_shows_collection_diff(tmp: TempDir) -> None:
    """--tb=detail shows element-level diff for list comparisons."""
    (tmp / "test_list.py").write_text(
        "def test_list():\n    assert [1, 2, 3] == [1, 4, 3]\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_failed(out, rc)
    # Element-level diff should show individual elements on separate lines
    helpers.integ.assert_contains(out, "-   2,", "+   4,")
