"""Tests for oxitest._bridge._ast_utils — shared bare-assert AST helpers."""

from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass

import oxitest as oxi
from oxitest._bridge._ast_utils import find_bare_assert_lines, walk_bare_asserts

# --- walk_bare_asserts ---


@dataclass(frozen=True)
class WalkCase:
    id: str
    source: str
    expected: list[int]


@oxi.parametrize(
    no_asserts=WalkCase(
        id="no_asserts",
        source="def f():\n    pass\n",
        expected=[],
    ),
    single=WalkCase(
        id="single",
        source="def f():\n    assert x\n",
        expected=[2],
    ),
    skips_message=WalkCase(
        id="skips_message",
        source="def f():\n    assert x, 'msg'\n",
        expected=[],
    ),
    nested_in_if=WalkCase(
        id="nested_in_if",
        source=textwrap.dedent("""\
            def f():
                if True:
                    assert a
                else:
                    assert b
        """),
        expected=[3, 5],
    ),
    prunes_nested_function=WalkCase(
        id="prunes_nested_function",
        source=textwrap.dedent("""\
            def f():
                assert outer
                def inner():
                    assert inner_bare
                assert after
        """),
        expected=[2, 5],
    ),
    multiple_sorted=WalkCase(
        id="multiple_sorted",
        source=textwrap.dedent("""\
            def f():
                assert z
                x = 1
                assert y
                assert w
        """),
        expected=[2, 4, 5],
    ),
)
def test_walk_bare_asserts(case: WalkCase):
    """walk_bare_asserts prunes nested functions, returns sorted line numbers."""
    tree = ast.parse(case.source)
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef), "expected FunctionDef"

    result = walk_bare_asserts(func)

    assert result == case.expected, f"{case.id}: expected {case.expected}, got {result}"


# --- find_bare_assert_lines ---


@dataclass(frozen=True)
class FindCase:
    id: str
    source: str
    start_line: int
    expected: frozenset[int]


@oxi.parametrize(
    from_source=FindCase(
        id="from_source",
        source=textwrap.dedent("""\
            def f():
                assert x
                assert y, 'msg'
                assert z
        """),
        start_line=1,
        expected=frozenset({2, 4}),
    ),
    syntax_error=FindCase(
        id="syntax_error",
        source="def f(\n",
        start_line=1,
        expected=frozenset(),
    ),
    empty_source=FindCase(
        id="empty_source",
        source="",
        start_line=1,
        expected=frozenset(),
    ),
    with_offset=FindCase(
        id="with_offset",
        source="assert x\n",
        start_line=10,
        expected=frozenset({10}),
    ),
    walks_into_nested=FindCase(
        id="walks_into_nested",
        source=textwrap.dedent("""\
            def f():
                assert outer
                def inner():
                    assert inner_bare
        """),
        start_line=1,
        expected=frozenset({2, 4}),
    ),
)
def test_find_bare_assert_lines(case: FindCase):
    """find_bare_assert_lines walks entire tree (including nested functions)."""
    result = find_bare_assert_lines(case.source, start_line=case.start_line)

    assert result == case.expected, f"{case.id}: expected {case.expected}, got {result}"
