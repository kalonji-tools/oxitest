"""Tests for namespace name validation (rejects Python keywords and builtins)."""

from __future__ import annotations

import oxitest as oxi
from oxitest._bridge._namespace_validation import validate_namespace_name


def test_rejects_python_keyword() -> None:
    """validate_namespace_name should reject hard Python keywords like 'class'."""
    with oxi.raises(ValueError, match="Python keyword"):
        validate_namespace_name("class", "/fake/conftest.py")


def test_rejects_soft_keyword() -> None:
    """validate_namespace_name should reject soft keywords like 'match'."""
    with oxi.raises(ValueError, match="Python keyword"):
        validate_namespace_name("match", "/fake/conftest.py")


def test_rejects_builtin() -> None:
    """validate_namespace_name should reject built-in type names like 'int'."""
    with oxi.raises(ValueError, match="Python builtin"):
        validate_namespace_name("int", "/fake/conftest.py")


def test_rejects_builtin_print() -> None:
    """validate_namespace_name should reject built-in function names like 'print'."""
    with oxi.raises(ValueError, match="Python builtin"):
        validate_namespace_name("print", "/fake/conftest.py")


def test_accepts_valid_name() -> None:
    """validate_namespace_name should accept valid identifiers that are not reserved."""
    validate_namespace_name("unit", "/fake/conftest.py")


def test_accepts_valid_name_db() -> None:
    """validate_namespace_name should accept short domain-style names like 'db'."""
    validate_namespace_name("db", "/fake/conftest.py")


def test_error_message_includes_source_path() -> None:
    """Error message should include the conftest source path for easier debugging."""
    with oxi.raises(ValueError, match="/my/conftest.py"):
        validate_namespace_name("for", "/my/conftest.py")


def test_error_message_suggests_renaming() -> None:
    """Error message should suggest renaming the conflicting variable or directory."""
    with oxi.raises(ValueError, match="Rename the variable or directory"):
        validate_namespace_name("list", "/fake/conftest.py")
