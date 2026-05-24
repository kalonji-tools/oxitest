"""Tests for import graph analysis."""

from oxitest._bridge.import_graph import (
    _extract_imported_modules,
    _file_to_modules,
    resolve_affected,
)


class TestFileToModules:
    """Test _file_to_modules converts file paths to module names."""

    def test_simple_module(self):
        result = _file_to_modules("myapp/utils.py", "/project")
        assert "myapp.utils" in result
        assert "myapp" in result

    def test_nested_module(self):
        result = _file_to_modules("myapp/sub/deep.py", "/project")
        assert "myapp.sub.deep" in result
        assert "myapp.sub" in result
        assert "myapp" in result

    def test_init_file(self):
        result = _file_to_modules("myapp/__init__.py", "/project")
        assert "myapp" in result
        assert "myapp.__init__" not in result

    def test_top_level_file(self):
        result = _file_to_modules("helpers.py", "/project")
        assert "helpers" in result

    def test_empty_on_error(self):
        result = _file_to_modules("../../outside.py", "/project")
        assert isinstance(result, list)


class TestExtractImportedModules:
    """Test _extract_imported_modules extracts imports via AST."""

    def test_import_statement(self, tmp_path):
        f = tmp_path / "test_mod.py"
        f.write_text("import myapp.utils\n")
        result = _extract_imported_modules(str(f))
        assert "myapp.utils" in result
        assert "myapp" in result

    def test_from_import(self, tmp_path):
        f = tmp_path / "test_mod.py"
        f.write_text("from myapp.utils import helper\n")
        result = _extract_imported_modules(str(f))
        assert "myapp.utils" in result
        assert "myapp" in result

    def test_multiple_imports(self, tmp_path):
        f = tmp_path / "test_mod.py"
        f.write_text("import os\nfrom myapp import models\n")
        result = _extract_imported_modules(str(f))
        assert "os" in result
        assert "myapp" in result

    def test_relative_import_skipped(self, tmp_path):
        f = tmp_path / "test_mod.py"
        f.write_text("from . import sibling\n")
        result = _extract_imported_modules(str(f))
        assert "sibling" not in result

    def test_syntax_error_returns_empty(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text("def broken(\n")
        result = _extract_imported_modules(str(f))
        assert result == set()

    def test_missing_file_returns_empty(self):
        result = _extract_imported_modules("/nonexistent/file.py")
        assert result == set()


class TestResolveAffected:
    """Test resolve_affected end-to-end."""

    def test_test_importing_changed_source(self, tmp_path):
        test_file = tmp_path / "test_foo.py"
        test_file.write_text("from myapp.utils import helper\n\ndef test_it(): pass\n")
        affected = resolve_affected(
            test_files=[str(test_file)],
            changed_sources=["myapp/utils.py"],
            root=str(tmp_path),
        )
        assert str(test_file) in affected

    def test_test_not_importing_changed_source(self, tmp_path):
        test_file = tmp_path / "test_foo.py"
        test_file.write_text("import os\n\ndef test_it(): pass\n")
        affected = resolve_affected(
            test_files=[str(test_file)],
            changed_sources=["myapp/utils.py"],
            root=str(tmp_path),
        )
        assert affected == []

    def test_no_changed_sources_returns_empty(self, tmp_path):
        test_file = tmp_path / "test_foo.py"
        test_file.write_text("import myapp\n")
        affected = resolve_affected(
            test_files=[str(test_file)],
            changed_sources=[],
            root=str(tmp_path),
        )
        assert affected == []

    def test_package_level_import_matches(self, tmp_path):
        test_file = tmp_path / "test_foo.py"
        test_file.write_text("import myapp\n\ndef test_it(): pass\n")
        affected = resolve_affected(
            test_files=[str(test_file)],
            changed_sources=["myapp/__init__.py"],
            root=str(tmp_path),
        )
        assert str(test_file) in affected

    def test_parent_package_match(self, tmp_path):
        test_file = tmp_path / "test_foo.py"
        test_file.write_text("from myapp.sub import something\n\ndef test_it(): pass\n")
        affected = resolve_affected(
            test_files=[str(test_file)],
            changed_sources=["myapp/sub/deep.py"],
            root=str(tmp_path),
        )
        # myapp.sub.deep changed → prefixes: myapp.sub.deep, myapp.sub, myapp.
        # Test imports myapp.sub → IS a match via shared prefix.
        assert str(test_file) in affected

    def test_exact_module_match(self, tmp_path):
        test_file = tmp_path / "test_foo.py"
        test_file.write_text("from myapp.sub import deep\n\ndef test_it(): pass\n")
        affected = resolve_affected(
            test_files=[str(test_file)],
            changed_sources=["myapp/sub/deep.py"],
            root=str(tmp_path),
        )
        # "from myapp.sub import deep" — imports myapp.sub, and changed file
        # generates prefixes: myapp.sub.deep, myapp.sub, myapp.
        # Test imports myapp.sub → IS a match via prefix.
        assert str(test_file) in affected
