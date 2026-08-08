"""Unit tests for the WarnCapture builtin fixture."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import warnings

import oxitest
from oxitest import TempDir, WarnCapture


def test_warncapture_captures_warnings_emitted_during_test(warn: WarnCapture) -> None:
    """WarnCapture.warnings should contain warnings emitted during the test body."""
    warnings.warn("hello", UserWarning, stacklevel=2)
    assert len(warn.warnings) == 1, f"expected 1 warning, got {warn.warnings!r}"
    assert issubclass(warn.warnings[0].category, UserWarning), (
        f"expected UserWarning category, got {warn.warnings[0].category!r}"
    )
    assert "hello" in str(warn.warnings[0].message), (
        f"expected 'hello' in warning message, got {warn.warnings[0].message!r}"
    )


def test_warncapture_captures_multiple_warning_categories(warn: WarnCapture) -> None:
    """WarnCapture should collect warnings of different categories in a single list."""
    warnings.warn("first", UserWarning, stacklevel=2)
    warnings.warn("second", DeprecationWarning, stacklevel=2)
    assert len(warn.warnings) == 2, f"expected 2 warnings, got {warn.warnings!r}"
    categories = [w.category for w in warn.warnings]
    assert UserWarning in categories, (
        f"expected UserWarning in captured categories, got {categories!r}"
    )
    assert DeprecationWarning in categories, (
        f"expected DeprecationWarning in captured categories, got {categories!r}"
    )


def test_warncapture_clear_resets_list(warn: WarnCapture) -> None:
    """WarnCapture.clear() should empty the list so new warnings are captured fresh."""
    warnings.warn("before clear", UserWarning, stacklevel=2)
    assert len(warn.warnings) == 1, (
        f"expected 1 warning before clear, got {warn.warnings!r}"
    )
    warn.clear()
    assert warn.warnings == (), (
        f"expected empty tuple after clear, got {warn.warnings!r}"
    )
    warnings.warn("after clear", UserWarning, stacklevel=2)
    assert len(warn.warnings) == 1, (
        f"expected 1 warning after clear, got {warn.warnings!r}"
    )


def test_warncapture_captured_ids_survives_clear(warn: WarnCapture) -> None:
    """captured_ids is not affected by clear() — tracks lifetime captures."""
    warnings.warn("first", UserWarning, stacklevel=2)
    assert len(warn.captured_ids) == 1, (
        f"expected 1 tracked id, got {len(warn.captured_ids)}"
    )
    warn.clear()
    assert len(warn.captured_ids) == 1, (
        f"clear() must not reset captured_ids, got {len(warn.captured_ids)}"
    )
    warnings.warn("second", UserWarning, stacklevel=2)
    assert len(warn.captured_ids) == 2, (
        f"expected 2 tracked ids, got {len(warn.captured_ids)}"
    )


def test_warncapture_and_oxitest_warns_can_coexist(warn: WarnCapture) -> None:
    """WarnCapture stays active while oxitest.warns() is used in the same test."""
    warnings.warn("before", UserWarning, stacklevel=2)
    with oxitest.warns(UserWarning, match="asserted"):
        warnings.warn("asserted", UserWarning, stacklevel=2)
    assert any("before" in str(w.message) for w in warn.warnings), (
        f"WarnCapture must capture warnings outside oxitest.warns() context, "
        f"got {warn.warnings!r}"
    )
    assert any("asserted" in str(w.message) for w in warn.warnings), (
        f"WarnCapture must also capture warnings inside oxitest.warns() context "
        f"(survives nested catch_warnings), got {warn.warnings!r}"
    )


def test_captured_warnings_not_in_report(tmp: TempDir) -> None:
    """Warnings captured by WarnCapture should not appear in --warnings output."""
    test_file = tmp / "test_cap.py"
    test_file.write_text(
        textwrap.dedent("""\
        import warnings
        from oxitest import WarnCapture

        def test_captured(warn: WarnCapture) -> None:
            warnings.warn("captured_by_fixture", UserWarning, stacklevel=1)
            assert len(warn.warnings) == 1, ""

        def test_uncaptured():
            warnings.warn("not_captured", UserWarning, stacklevel=1)
            assert True, ""
    """),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "oxitest", str(tmp), "--serial", "--warnings"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert "not_captured" in result.stdout, (
        f"uncaptured warning should appear:\n{result.stdout}"
    )
    assert "captured_by_fixture" not in result.stdout, (
        f"captured warning should be suppressed:\n{result.stdout}"
    )
