from __future__ import annotations

import unittest

from oxitest import importorskip, raises


def test_importorskip_returns_module():
    import os

    mod = importorskip("os")
    assert mod is os, "importorskip('os') should return the os module"


def test_importorskip_skips_missing_module():
    with raises(unittest.SkipTest):
        importorskip("_nonexistent_oxitest_test_module_xyz")


def test_importorskip_skip_reason_mentions_module_name():
    with raises(unittest.SkipTest, match="_nonexistent_oxitest_test_module_xyz"):
        importorskip("_nonexistent_oxitest_test_module_xyz")


def test_importorskip_custom_reason():
    with raises(unittest.SkipTest, match="needs loguru installed"):
        importorskip(
            "_nonexistent_oxitest_test_module_xyz",
            reason="needs loguru installed",
        )


def test_importorskip_exported_from_oxitest():
    import oxitest

    assert hasattr(oxitest, "importorskip"), (
        "'importorskip' should be exported from the oxitest module"
    )
    assert "importorskip" in oxitest.__all__, (
        "'importorskip' should be listed in oxitest.__all__"
    )
