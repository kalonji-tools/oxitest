"""Tests for importorskip — skip-on-ImportError helper."""

from __future__ import annotations

import unittest

from oxitest import importorskip, raises


def test_importorskip_returns_module() -> None:
    """Successful import via importorskip should return the module object."""
    import os

    mod = importorskip("os")
    assert mod is os, "importorskip('os') should return the os module"


def test_importorskip_skips_missing_module() -> None:
    """Missing module via importorskip should raise SkipTest."""
    with raises(unittest.SkipTest):
        importorskip("_nonexistent_oxitest_test_module_xyz")


def test_importorskip_skip_reason_mentions_module_name() -> None:
    """SkipTest message should include the missing module name for diagnostics."""
    with raises(unittest.SkipTest, match="_nonexistent_oxitest_test_module_xyz"):
        importorskip("_nonexistent_oxitest_test_module_xyz")


def test_importorskip_custom_reason() -> None:
    """A custom reason= argument should override the default skip message."""
    with raises(unittest.SkipTest, match="needs loguru installed"):
        importorskip(
            "_nonexistent_oxitest_test_module_xyz",
            reason="needs loguru installed",
        )


def test_importorskip_exported_from_oxitest() -> None:
    """The importorskip function should be publicly exported in oxitest.__all__."""
    import oxitest

    assert hasattr(oxitest, "importorskip"), (
        "'importorskip' should be exported from the oxitest module"
    )
    assert "importorskip" in oxitest.__all__, (
        "'importorskip' should be listed in oxitest.__all__"
    )
