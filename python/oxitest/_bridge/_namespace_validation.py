from __future__ import annotations

__all__ = ["validate_namespace_name"]

import builtins
import keyword


def validate_namespace_name(name: str, source_path: str) -> None:
    """Raise ValueError if *name* is a Python keyword or builtin."""
    if keyword.iskeyword(name) or keyword.issoftkeyword(name):
        raise ValueError(
            f'"{name}" is a Python keyword and cannot be used as a namespace name.\n'
            f"\n"
            f"Source: {source_path}\n"
            f"\n"
            f'Rename the directory or add __helpers_namespace__ = "..." '
            f"to pick a different name."
        )
    if name in vars(builtins):
        raise ValueError(
            f'"{name}" is a Python builtin and cannot be used as a namespace name.\n'
            f"\n"
            f"Source: {source_path}\n"
            f"\n"
            f'Rename the directory or add __helpers_namespace__ = "..." '
            f"to pick a different name."
        )
