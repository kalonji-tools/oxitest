from __future__ import annotations

import oxitest as oxi
from oxitest._bridge._namespace_validation import validate_namespace_name


def test_rejects_python_keyword() -> None:
    with oxi.raises(ValueError, match="Python keyword"):
        validate_namespace_name("class", "/tmp/conftest.py")


def test_rejects_soft_keyword() -> None:
    with oxi.raises(ValueError, match="Python keyword"):
        validate_namespace_name("match", "/tmp/conftest.py")


def test_rejects_builtin() -> None:
    with oxi.raises(ValueError, match="Python builtin"):
        validate_namespace_name("int", "/tmp/conftest.py")


def test_rejects_builtin_print() -> None:
    with oxi.raises(ValueError, match="Python builtin"):
        validate_namespace_name("print", "/tmp/conftest.py")


def test_accepts_valid_name() -> None:
    validate_namespace_name("unit", "/tmp/conftest.py")


def test_accepts_valid_name_db() -> None:
    validate_namespace_name("db", "/tmp/conftest.py")


def test_error_message_includes_source_path() -> None:
    with oxi.raises(ValueError, match="/my/conftest.py"):
        validate_namespace_name("for", "/my/conftest.py")


def test_error_message_suggests_renaming() -> None:
    with oxi.raises(ValueError, match="Rename the variable or directory"):
        validate_namespace_name("list", "/tmp/conftest.py")
