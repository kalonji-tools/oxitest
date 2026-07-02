"""Unit tests for FixtureValidator — extracted validation/query logic."""

from __future__ import annotations

from oxitest import helpers
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixture_validator import FixtureValidator
from oxitest._bridge._loader import ModuleCache
from oxitest._bridge.plugin_loader import PluginRegistry


def _make_validator(*defs) -> FixtureValidator:
    """Create a FixtureValidator from FixtureDefs."""
    reg = FixtureRegistry()
    for d in defs:
        reg.register(d)
    return FixtureValidator(reg, PluginRegistry(), ModuleCache())


def _factory_no_dep() -> None:
    """A fixture factory with no dependencies."""


def test_validate_valid_names_returns_empty():
    v = _make_validator(
        helpers.common.make_fixture_def("store", lambda: 42, conftest_path="/c.py")
    )

    errors = v.validate_fixture_names(
        [{"node_id": "test.py::test_a", "fixture_names": ["store"], "fixref_names": []}]
    )

    assert errors == [], f"expected no errors, got {errors}"


def test_validate_invalid_name_returns_error():
    v = _make_validator(
        helpers.common.make_fixture_def("store", lambda: 42, conftest_path="/c.py")
    )

    errors = v.validate_fixture_names(
        [{"node_id": "test.py::test_a", "fixture_names": ["nope"], "fixref_names": []}]
    )

    assert errors == [("test.py::test_a", "nope")], f"unexpected: {errors}"


def test_find_unused_detects_unreferenced():
    v = _make_validator(
        helpers.common.make_fixture_def(
            "unused_db", conftest_path="/project/conftest.py", factory=_factory_no_dep
        )
    )

    result = v.find_unused_fixtures([{"fixture_names": []}])

    assert len(result) == 1, f"expected 1 unused, got {len(result)}"
    assert result[0] == ("/project/conftest.py", "unused_db"), (
        f"unexpected: {result[0]}"
    )


def test_find_unused_excludes_autouse():
    v = _make_validator(
        helpers.common.make_fixture_def(
            "auto_setup",
            autouse=True,
            conftest_path="/project/conftest.py",
            factory=_factory_no_dep,
        )
    )

    result = v.find_unused_fixtures([{"fixture_names": []}])

    assert result == [], f"autouse should not be unused: {result}"


def test_find_unused_excludes_transitive_deps():
    def _parent(dep: object = None) -> None:
        pass

    v = _make_validator(
        helpers.common.make_fixture_def(
            "dep", conftest_path="/project/conftest.py", factory=_factory_no_dep
        ),
        helpers.common.make_fixture_def(
            "parent",
            conftest_path="/project/conftest.py",
            factory=_parent,
            depends_on=(("dep", object),),
        ),
    )

    result = v.find_unused_fixtures([{"fixture_names": ["parent"]}])

    assert result == [], f"transitive dep should not be unused: {result}"


def test_validate_builtin_qualifier_not_flagged():
    """fixture_deps with builtin type_name are not flagged missing."""
    v = _make_validator()

    errors = v.validate_fixture_names(
        [
            {
                "node_id": "test.py::test_a",
                "fixture_names": ["tmp"],
                "fixref_names": [],
                "fixture_deps": [("tmp", "TempDir")],
            },
        ]
    )

    assert errors == [], (
        "builtin fixture qualifier should be excluded from missing fixture errors"
    )
