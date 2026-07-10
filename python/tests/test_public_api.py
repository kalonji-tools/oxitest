"""Tests for the oxitest public API surface — export presence and discoverability."""

from __future__ import annotations

import oxitest
from oxitest import (
    Both,
    Cli,
    CliExtension,
    Conf,
    FixtureTeardownWarning,
    WarnCapture,
)


def test_tempdir_exported_from_oxitest() -> None:
    """TempDir is available as a top-level name in the oxitest package."""
    assert hasattr(oxitest, "TempDir"), "'TempDir' should be exported from oxitest"


def test_tempdir_factory_exported_from_oxitest() -> None:
    """TempDirFactory is available as a top-level name in the oxitest package."""
    assert hasattr(oxitest, "TempDirFactory"), (
        "'TempDirFactory' should be exported from oxitest"
    )


def test_stdcapture_exported_from_oxitest() -> None:
    """StdCapture is available as a top-level name in the oxitest package."""
    assert hasattr(oxitest, "StdCapture"), (
        "'StdCapture' should be exported from oxitest"
    )


def test_fdcapture_exported_from_oxitest() -> None:
    """FdCapture is available as a top-level name in the oxitest package."""
    assert hasattr(oxitest, "FdCapture"), "'FdCapture' should be exported from oxitest"


def test_patcher_exported_from_oxitest() -> None:
    """Patcher is available as a top-level name in the oxitest package."""
    assert hasattr(oxitest, "Patcher"), "'Patcher' should be exported from oxitest"


def test_capture_result_exported_from_oxitest() -> None:
    """CaptureResult is available as a top-level name in the oxitest package."""
    assert hasattr(oxitest, "CaptureResult"), (
        "'CaptureResult' should be exported from oxitest"
    )


def test_logcapture_exported_from_oxitest() -> None:
    """LogCapture is available as a top-level name in the oxitest package."""
    assert hasattr(oxitest, "LogCapture"), (
        "'LogCapture' should be exported from oxitest"
    )


def test_shared_fixture_mutation_error_importable_from_oxitest() -> None:
    """SharedFixtureMutationError is exported and is a RuntimeError subclass."""
    assert hasattr(oxitest, "SharedFixtureMutationError"), (
        "'SharedFixtureMutationError' should be exported from oxitest"
    )
    assert issubclass(oxitest.SharedFixtureMutationError, RuntimeError), (
        "oxitest.SharedFixtureMutationError should be a subclass of RuntimeError"
    )


def test_yields_exported_from_oxitest() -> None:
    """Yields is exported from oxitest and listed in __all__."""
    assert hasattr(oxitest, "Yields"), "'Yields' should be exported from oxitest"
    assert "Yields" in oxitest.__all__, "'Yields' should be listed in oxitest.__all__"


def test_warncapture_exported_from_oxitest() -> None:
    """WarnCapture is importable directly from the oxitest package."""
    assert WarnCapture is not None, "WarnCapture should be exported from oxitest"


def test_fixture_teardown_warning_exported_from_oxitest() -> None:
    """FixtureTeardownWarning is importable from oxitest as a UserWarning subclass."""
    assert issubclass(FixtureTeardownWarning, UserWarning), (
        "FixtureTeardownWarning should be a subclass of UserWarning"
    )


def test_internal_types_not_in_all() -> None:
    """Internal implementation types are absent from __all__ to avoid leaking them."""
    removed = [
        "ApproxBase",
    ]
    for name in removed:
        assert name not in oxitest.__all__, (
            f"{name} should not be in __all__ — it is an internal type"
        )


def test_exception_types_in_all() -> None:
    """Public exception and warning types are listed in __all__ for discoverability."""
    promoted = [
        "SharedFixtureMutationError",
        "FixtureShadowWarning",
        "FixtureTeardownWarning",
    ]
    for name in promoted:
        assert name in oxitest.__all__, (
            f"{name} should be in __all__ — it is a public exception/warning type"
        )


def test_plugin_config_types_in_all() -> None:
    """Stable plugin config types (Cli, Conf, Both, CliExtension) are in __all__."""
    expected = ["Cli", "Conf", "Both", "CliExtension"]
    for name in expected:
        assert name in oxitest.__all__, (
            f"{name} should be in __all__ — it is a stable plugin config type"
        )


def test_plugin_config_types_importable() -> None:
    """Plugin config types can be constructed from the public API without errors."""
    assert Cli(help="test").help == "test", "Cli should be constructable"
    assert Conf(help="test").help == "test", "Conf should be constructable"
    assert Both(help="test").help == "test", "Both should be constructable"
    assert CliExtension(prefix="p", config_type=int).prefix == "p", (
        "CliExtension should be constructable"
    )


def test_injectable_exported_from_oxitest() -> None:
    """The injectable decorator is exported from oxitest and listed in __all__."""
    assert hasattr(oxitest, "injectable"), (
        "'injectable' should be exported from the oxitest module"
    )
    assert "injectable" in oxitest.__all__, (
        "'injectable' should be listed in oxitest.__all__"
    )
