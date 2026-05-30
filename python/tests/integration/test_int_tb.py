"""Integration tests: --tb modes and --show-locals/--show-internals flags."""

from conftest import helpers
from oxitest import TempDir


def test_tb_detail_shows_source_line(tmp: TempDir):
    """Default --tb=detail shows the assertion source line in bold."""
    (tmp / "test_fail.py").write_text("def test_fail():\n    assert 1 == 2\n")
    out, _, rc = helpers.common.run_oxitest(tmp)
    assert rc == 1, f"expected exit 1, got {rc}"
    assert "assert 1 == 2" in out, f"source line missing: {out!r}"


def test_tb_detail_shows_box_chrome(tmp: TempDir):
    """Default --tb=detail shows the new box chrome."""
    (tmp / "test_fail.py").write_text("def test_fail():\n    assert 1 == 2\n")
    out, _, rc = helpers.common.run_oxitest(tmp)
    assert rc == 1, f"expected exit 1, got {rc}"
    assert "┌" in out, f"box open missing: {out!r}"
    assert "└" in out, f"box close missing: {out!r}"
    # Old chrome should NOT appear
    assert "┌─" not in out, f"old box chrome found: {out!r}"
    assert "└─" not in out, f"old box chrome found: {out!r}"


def test_tb_line_shows_one_liner(tmp: TempDir):
    """--tb=line produces a single FAILED line."""
    (tmp / "test_fail.py").write_text("def test_fail():\n    assert 1 == 2\n")
    out, _, rc = helpers.common.run_oxitest(tmp, "--tb=line")
    assert rc == 1, f"expected exit 1, got {rc}"
    assert "FAILED" in out, f"FAILED missing: {out!r}"
    assert "┌" not in out, f"box chrome should not appear: {out!r}"


def test_tb_no_suppresses_diagnostic(tmp: TempDir):
    """--tb=no shows no diagnostic block."""
    (tmp / "test_fail.py").write_text("def test_fail():\n    assert 1 == 2\n")
    out, _, rc = helpers.common.run_oxitest(tmp, "--tb=no")
    assert rc == 1, f"expected exit 1, got {rc}"
    assert "assert 1 == 2" not in out, f"source line should not appear: {out!r}"
    assert "┌" not in out, f"box chrome should not appear: {out!r}"


def test_show_locals_conflict_with_tb_no(tmp: TempDir):
    """--show-locals with --tb=no is rejected."""
    (tmp / "test_pass.py").write_text("def test_pass():\n    pass\n")
    out, err, rc = helpers.common.run_oxitest(tmp, "--show-locals", "--tb=no")
    assert rc != 0, "should fail with conflict"
    combined = out + err
    assert "requires" in combined.lower() or "detail" in combined.lower(), (
        f"error should mention detail requirement: {combined!r}"
    )


def test_show_internals_conflict_with_tb_line(tmp: TempDir):
    """--show-internals with --tb=line is rejected."""
    (tmp / "test_pass.py").write_text("def test_pass():\n    pass\n")
    out, err, rc = helpers.common.run_oxitest(tmp, "--show-internals", "--tb=line")
    assert rc != 0, "should fail with conflict"
    combined = out + err
    assert "requires" in combined.lower() or "detail" in combined.lower(), (
        f"error should mention detail requirement: {combined!r}"
    )


def test_tb_detail_shows_diff(tmp: TempDir):
    """--tb=detail shows left/right diff for comparison failures."""
    (tmp / "test_diff.py").write_text("def test_diff():\n    assert 1 == 2\n")
    out, _, rc = helpers.common.run_oxitest(tmp)
    assert rc == 1, f"expected exit 1, got {rc}"
    assert "left:" in out or "- left:" in out, f"diff left missing: {out!r}"


def test_tb_detail_shows_collection_diff(tmp: TempDir):
    """--tb=detail shows element-level diff for list comparisons."""
    (tmp / "test_list.py").write_text(
        "def test_list():\n    assert [1, 2, 3] == [1, 4, 3]\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    assert rc == 1, f"expected exit 1, got {rc}"
    # Element-level diff should show individual elements on separate lines
    assert "-   2," in out, f"removed element missing: {out!r}"
    assert "+   4," in out, f"added element missing: {out!r}"
