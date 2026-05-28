from __future__ import annotations

__all__ = ["HelperNamespace", "build_helpers"]

import types
from pathlib import Path
from typing import Any

from oxitest._bridge._namespace_validation import validate_namespace_name
from oxitest._bridge.fixtures import Fixtures


class HelperNamespace:
    """Dot-accessible bag of attributes assembled from conftest helpers."""

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"HelperNamespace has no attribute {name!r}")

    def __repr__(self) -> str:
        names = sorted(n for n in vars(self) if not n.startswith("_"))
        return f"HelperNamespace({', '.join(names)})"


def build_helpers(
    conftest_chain: list[tuple[types.ModuleType, Path]],
) -> HelperNamespace:
    """Build a root HelperNamespace from a root-first chain of conftest modules.

    Each module's public callables (functions and classes) are collected into
    a per-scope HelperNamespace, keyed by directory name or
    ``__helpers_namespace__`` override.
    """
    root = HelperNamespace()
    seen: dict[str, str] = {}  # namespace_name -> source_path

    for module, dir_path in conftest_chain:
        name = getattr(module, "__helpers_namespace__", None) or dir_path.name
        source = str(dir_path / "conftest.py")

        validate_namespace_name(name, source)

        if name in seen:
            raise ValueError(
                f'Two conftest files use the same helpers namespace "{name}":\n'
                f"  - {seen[name]}\n"
                f"  - {source}\n"
                f"\n"
                f'Add __helpers_namespace__ = "..." to one of them '
                f"to pick a different name."
            )
        seen[name] = source

        scope = HelperNamespace()
        for attr_name, obj in vars(module).items():
            if attr_name.startswith("_"):
                continue
            if isinstance(obj, Fixtures):
                continue
            if not callable(obj):
                continue
            setattr(scope, attr_name, obj)

        setattr(root, name, scope)

    return root
