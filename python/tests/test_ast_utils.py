"""Tests for oxitest._bridge._ast_utils — runtime bare-assert fallback."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

import oxitest as oxi
from oxitest._bridge._ast_utils import find_bare_assert_lines


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
