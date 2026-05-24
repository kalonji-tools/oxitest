"""Tests for import graph analysis."""

from oxitest import Fixture, TempDir

from oxitest._bridge.import_graph import (
    _extract_imported_modules,
    _file_to_modules,
    resolve_affected,
)


# ── _file_to_modules ─────────────────────────────────────────────────


def test_file_to_modules_simple():
    result = _file_to_modules("myapp/utils.py", "/project")
    assert "myapp.utils" in result, ""
    assert "myapp" in result, ""


def test_file_to_modules_nested():
    result = _file_to_modules("myapp/sub/deep.py", "/project")
    assert "myapp.sub.deep" in result, ""
    assert "myapp.sub" in result, ""
    assert "myapp" in result, ""


def test_file_to_modules_init():
    result = _file_to_modules("myapp/__init__.py", "/project")
    assert "myapp" in result, ""
    assert "myapp.__init__" not in result, ""


def test_file_to_modules_top_level():
    result = _file_to_modules("helpers.py", "/project")
    assert "helpers" in result, ""


def test_file_to_modules_error():
    result = _file_to_modules("../../outside.py", "/project")
    assert isinstance(result, list), ""


# ── _extract_imported_modules ─────────────────────────────────────────


def test_extract_import_statement(tmp: Fixture[TempDir]):
    f = tmp / "test_mod.py"
    f.write_text("import myapp.utils\n")
    result = _extract_imported_modules(str(f))
    assert "myapp.utils" in result, ""
    assert "myapp" in result, ""


def test_extract_from_import(tmp: Fixture[TempDir]):
    f = tmp / "test_mod.py"
    f.write_text("from myapp.utils import helper\n")
    result = _extract_imported_modules(str(f))
    assert "myapp.utils" in result, ""
    assert "myapp" in result, ""


def test_extract_multiple_imports(tmp: Fixture[TempDir]):
    f = tmp / "test_mod.py"
    f.write_text("import os\nfrom myapp import models\n")
    result = _extract_imported_modules(str(f))
    assert "os" in result, ""
    assert "myapp" in result, ""


def test_extract_relative_import_skipped(tmp: Fixture[TempDir]):
    f = tmp / "test_mod.py"
    f.write_text("from . import sibling\n")
    result = _extract_imported_modules(str(f))
    assert "sibling" not in result, ""


def test_extract_syntax_error_returns_empty(tmp: Fixture[TempDir]):
    f = tmp / "bad.py"
    f.write_text("def broken(\n")
    result = _extract_imported_modules(str(f))
    assert result == set(), ""


def test_extract_missing_file_returns_empty():
    result = _extract_imported_modules("/nonexistent/file.py")
    assert result == set(), ""


# ── resolve_affected ──────────────────────────────────────────────────


def test_resolve_importing_changed_source(tmp: Fixture[TempDir]):
    test_file = tmp / "test_foo.py"
    test_file.write_text("from myapp.utils import helper\n\ndef test_it(): pass\n")
    affected = resolve_affected(
        test_files=[str(test_file)],
        changed_sources=["myapp/utils.py"],
        root=str(tmp),
    )
    assert str(test_file) in affected, ""


def test_resolve_not_importing_changed_source(tmp: Fixture[TempDir]):
    test_file = tmp / "test_foo.py"
    test_file.write_text("import os\n\ndef test_it(): pass\n")
    affected = resolve_affected(
        test_files=[str(test_file)],
        changed_sources=["myapp/utils.py"],
        root=str(tmp),
    )
    assert affected == [], ""


def test_resolve_no_changed_sources(tmp: Fixture[TempDir]):
    test_file = tmp / "test_foo.py"
    test_file.write_text("import myapp\n")
    affected = resolve_affected(
        test_files=[str(test_file)],
        changed_sources=[],
        root=str(tmp),
    )
    assert affected == [], ""


def test_resolve_package_level_import(tmp: Fixture[TempDir]):
    test_file = tmp / "test_foo.py"
    test_file.write_text("import myapp\n\ndef test_it(): pass\n")
    affected = resolve_affected(
        test_files=[str(test_file)],
        changed_sources=["myapp/__init__.py"],
        root=str(tmp),
    )
    assert str(test_file) in affected, ""


def test_resolve_parent_package_match(tmp: Fixture[TempDir]):
    test_file = tmp / "test_foo.py"
    test_file.write_text("from myapp.sub import something\n\ndef test_it(): pass\n")
    affected = resolve_affected(
        test_files=[str(test_file)],
        changed_sources=["myapp/sub/deep.py"],
        root=str(tmp),
    )
    # myapp.sub.deep changed -> prefixes: myapp.sub.deep, myapp.sub, myapp.
    # Test imports myapp.sub -> IS a match via prefix overlap.
    assert str(test_file) in affected, ""


def test_resolve_exact_module_match(tmp: Fixture[TempDir]):
    test_file = tmp / "test_foo.py"
    test_file.write_text("from myapp.sub import deep\n\ndef test_it(): pass\n")
    affected = resolve_affected(
        test_files=[str(test_file)],
        changed_sources=["myapp/sub/deep.py"],
        root=str(tmp),
    )
    # "from myapp.sub import deep" imports myapp.sub, and changed file
    # generates prefixes: myapp.sub.deep, myapp.sub, myapp.
    # Test imports myapp.sub -> IS a match via prefix.
    assert str(test_file) in affected, ""
