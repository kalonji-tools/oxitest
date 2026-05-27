"""Shared AST utilities for bare-assert detection.

Two functions with deliberately different walking behavior:

- ``walk_bare_asserts`` prunes nested function defs (collection-time use).
- ``find_bare_assert_lines`` walks the entire tree including nested functions
  (runtime fallback use).
"""

from __future__ import annotations

__all__ = ["find_bare_assert_lines", "walk_bare_asserts"]

import ast


def walk_bare_asserts(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[int]:
    """Return sorted line numbers of bare ``assert`` statements in *func_node*.

    Does NOT descend into nested function definitions, so inner helpers
    whose bare asserts should not be attributed to the enclosing test are
    correctly excluded.
    """
    lines: list[int] = []
    queue = list(ast.iter_child_nodes(func_node))
    while queue:
        node = queue.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue  # prune: do not recurse into nested functions
        if isinstance(node, ast.Assert) and node.msg is None:
            lines.append(node.lineno)
        queue.extend(ast.iter_child_nodes(node))
    return sorted(lines)


def find_bare_assert_lines(source: str, start_line: int = 1) -> frozenset[int]:
    """Parse *source* and return line numbers of bare ``assert`` statements.

    Unlike :func:`walk_bare_asserts`, this walks the **entire** tree including
    nested function definitions — matching the original middleware fallback
    behavior.

    *start_line* is added as an offset (minus 1) so that line numbers align
    with the original file when the source was extracted via
    :func:`inspect.getsourcelines`.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return frozenset()

    return frozenset(
        n.lineno + start_line - 1
        for n in ast.walk(tree)
        if isinstance(n, ast.Assert) and n.msg is None
    )
