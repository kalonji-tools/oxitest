"""Tests for the oxitest public API surface — discoverability and docstrings."""

from __future__ import annotations

import oxitest


def test_module_has_docstring() -> None:
    assert oxitest.__doc__ is not None, "oxitest module should have a docstring"
    assert len(oxitest.__doc__.strip()) > 0, (
        "oxitest module docstring should not be blank"
    )


def test_module_docstring_mentions_fixtures_registry() -> None:
    doc = oxitest.__doc__
    assert doc is not None, "oxitest module should have a docstring"
    assert "Fixtures" in doc, "oxitest module docstring should mention 'Fixtures'"


def test_module_docstring_mentions_fixture_injection() -> None:
    doc = oxitest.__doc__
    assert doc is not None, "oxitest module should have a docstring"
    assert "Fixture" in doc, "oxitest module docstring should mention 'Fixture'"


def test_fixture_type_has_docstring() -> None:
    from oxitest import Fixture

    assert Fixture.__doc__ is not None, "Fixture type should have a docstring"


def test_fixture_type_docstring_explains_injection() -> None:
    from oxitest import Fixture

    doc = Fixture.__doc__
    assert doc is not None, "Fixture type should have a docstring"
    assert "inject" in doc.lower(), "Fixture docstring should explain injection"
    assert "Fixture[T]" in doc or "Fixture[" in doc, (
        "Fixture docstring should show Fixture[T] usage"
    )


def test_fixture_type_docstring_warns_unannotated_not_injected() -> None:
    from oxitest import Fixture

    doc = Fixture.__doc__
    assert doc is not None, "Fixture type should have a docstring"
    # must make clear that unannotated params are NOT injected
    assert "not" in doc.lower() or "required" in doc.lower(), (
        "Fixture docstring should clarify that unannotated params are NOT injected"
    )


def test_parametrize_decorator_has_docstring() -> None:
    assert oxitest.parametrize.__doc__ is not None, (
        "oxitest.parametrize should have a docstring"
    )


def test_parametrize_docstring_explains_compact_mode() -> None:
    doc = oxitest.parametrize.__doc__
    assert doc is not None, "oxitest.parametrize should have a docstring"
    assert "compact" in doc.lower(), (
        "oxitest.parametrize docstring should explain compact mode"
    )


def test_parametrize_docstring_explains_expanded_mode() -> None:
    doc = oxitest.parametrize.__doc__
    assert doc is not None, "oxitest.parametrize should have a docstring"
    assert "expanded" in doc.lower(), (
        "oxitest.parametrize docstring should explain expanded mode"
    )


def test_parametrize_docstring_explains_mode_detection() -> None:
    doc = oxitest.parametrize.__doc__
    assert doc is not None, "oxitest.parametrize should have a docstring"
    # must mention that mode is inferred from the function signature
    assert "signature" in doc.lower() or "annotate" in doc.lower(), (
        "oxitest.parametrize docstring should explain mode detection from function"
        " signature"
    )


def test_fixtures_fixture_method_has_teardown_docstring() -> None:
    import oxitest

    doc = oxitest.Fixtures.fixture.__doc__
    assert doc is not None, "Fixtures.fixture method should have a docstring"
    assert "yield" in doc.lower(), (
        "Fixtures.fixture docstring should mention 'yield' for teardown pattern"
    )
    assert "teardown" in doc.lower(), (
        "Fixtures.fixture docstring should mention 'teardown'"
    )


def test_fixtures_fixture_method_docstring_recommends_typed_returns() -> None:
    import oxitest

    doc = oxitest.Fixtures.fixture.__doc__
    assert doc is not None, "Fixtures.fixture method should have a docstring"
    # must advise against plain dict
    assert "dict" in doc.lower(), (
        "Fixtures.fixture docstring should mention 'dict' (to advise against it)"
    )
    # must recommend a structured alternative
    assert "dataclass" in doc.lower() or "typeddict" in doc.lower(), (
        "Fixtures.fixture docstring should recommend dataclass or TypedDict"
    )
    # must include a return-types section header
    assert "return" in doc.lower(), (
        "Fixtures.fixture docstring should include a return-types section"
    )


def test_tempdir_exported_from_oxitest() -> None:
    import oxitest

    assert hasattr(oxitest, "TempDir"), "'TempDir' should be exported from oxitest"


def test_tempdir_factory_exported_from_oxitest() -> None:
    import oxitest

    assert hasattr(oxitest, "TempDirFactory"), (
        "'TempDirFactory' should be exported from oxitest"
    )


def test_stdcapture_exported_from_oxitest() -> None:
    import oxitest

    assert hasattr(oxitest, "StdCapture"), (
        "'StdCapture' should be exported from oxitest"
    )


def test_fdcapture_exported_from_oxitest() -> None:
    import oxitest

    assert hasattr(oxitest, "FdCapture"), "'FdCapture' should be exported from oxitest"


def test_patcher_exported_from_oxitest() -> None:
    import oxitest

    assert hasattr(oxitest, "Patcher"), "'Patcher' should be exported from oxitest"


def test_capture_result_exported_from_oxitest() -> None:
    import oxitest

    assert hasattr(oxitest, "CaptureResult"), (
        "'CaptureResult' should be exported from oxitest"
    )


def test_logcapture_exported_from_oxitest() -> None:
    import oxitest

    assert hasattr(oxitest, "LogCapture"), (
        "'LogCapture' should be exported from oxitest"
    )


def test_shared_fixture_mutation_error_importable_from_oxitest() -> None:
    import oxitest

    assert hasattr(oxitest, "SharedFixtureMutationError"), (
        "'SharedFixtureMutationError' should be exported from oxitest"
    )
    assert issubclass(oxitest.SharedFixtureMutationError, RuntimeError), (
        "oxitest.SharedFixtureMutationError should be a subclass of RuntimeError"
    )


def test_yields_exported_from_oxitest() -> None:
    import oxitest

    assert hasattr(oxitest, "Yields"), "'Yields' should be exported from oxitest"
    assert "Yields" in oxitest.__all__, "'Yields' should be listed in oxitest.__all__"


def test_warncapture_exported_from_oxitest() -> None:
    from oxitest import WarnCapture

    assert WarnCapture is not None, "WarnCapture should be exported from oxitest"


def test_fixture_teardown_warning_exported_from_oxitest() -> None:
    from oxitest import FixtureTeardownWarning

    assert issubclass(FixtureTeardownWarning, UserWarning), (
        "FixtureTeardownWarning should be a subclass of UserWarning"
    )


def test_internal_types_not_in_all() -> None:
    removed = [
        "ApproxBase",
    ]
    for name in removed:
        assert name not in oxitest.__all__, (
            f"{name} should not be in __all__ — it is an internal type"
        )


def test_exception_types_in_all() -> None:
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
    expected = ["Cli", "Conf", "Both", "CliExtension"]
    for name in expected:
        assert name in oxitest.__all__, (
            f"{name} should be in __all__ — it is a stable plugin config type"
        )


def test_plugin_config_types_importable() -> None:
    from oxitest import Both, Cli, CliExtension, Conf

    assert Cli(help="test").help == "test", "Cli should be constructable"
    assert Conf(help="test").help == "test", "Conf should be constructable"
    assert Both(help="test").help == "test", "Both should be constructable"
    assert CliExtension(prefix="p", config_type=int).prefix == "p", (
        "CliExtension should be constructable"
    )


def test_injectable_exported_from_oxitest() -> None:
    import oxitest

    assert hasattr(oxitest, "injectable"), (
        "'injectable' should be exported from the oxitest module"
    )
    assert "injectable" in oxitest.__all__, (
        "'injectable' should be listed in oxitest.__all__"
    )
