"""Helpers for writing test files and installing modules into sys.modules.

Split out of conftest.py by #1787. These are plain functions — there is no
helper registry (#1700). Import them via ``from tests import helpers``.
"""

from __future__ import annotations

import sys
import textwrap
import types
from collections.abc import Callable

from oxitest import TempDir, TestContext


def write_test_file(
    tmp_path: TempDir,
    code: str,
    name: str = "test_auto.py",
) -> str:
    """Write a test file into *tmp_path* and return its path as str."""
    f = tmp_path / name
    f.write_text(code)
    return str(f)


def write_test_module(tmp: TempDir, code: str, *, name: str = "test_auto.py") -> str:
    """Write dedented code to a named file in tmp and return its path as str."""
    f = tmp / name
    f.write_text(textwrap.dedent(code))
    return str(f)


def make_plugin_module(
    name: str,
    entry: Callable[..., object] | None = None,
    **attrs: object,
) -> types.ModuleType:
    """Create a fake plugin module with the given entry point and attributes.

    Sets ``oxitest_plugin`` to *entry* when provided.  Any additional keyword
    arguments are set as module attributes (e.g. ``oxitest_cli_extension``).
    """
    mod = types.ModuleType(name)
    if entry is not None:
        setattr(mod, "oxitest_plugin", entry)  # noqa: B010 — dynamic module attr
    for attr_name, value in attrs.items():
        setattr(mod, attr_name, value)
    return mod


def install_module(
    ctx: TestContext,
    name: str,
    module: types.ModuleType,
) -> None:
    """Install ``module`` under ``name`` in ``sys.modules``; auto-remove on teardown."""

    def cleanup() -> None:
        sys.modules.pop(name, None)

    sys.modules[name] = module
    ctx.on_teardown(cleanup)
