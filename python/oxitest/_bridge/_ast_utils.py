"""AST utility for runtime bare-assert detection.

``find_bare_assert_lines`` walks the entire tree including nested functions
(runtime fallback use in ``_middleware.py``).

Collection-time bare-assert detection is now handled in Rust
(``src/bare_asserts.rs``).
"""

from __future__ import annotations

__all__ = ["find_bare_assert_lines"]

import ast


def find_bare_assert_lines(source: str, start_line: int = 1) -> frozenset[int]:
    """Parse *source* and return line numbers of bare ``assert`` statements.

    Walks the **entire** tree including nested function definitions — matching
    the original middleware fallback behavior.

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
