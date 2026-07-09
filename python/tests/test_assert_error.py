"""Tests for assert rewriting: _OxitestAssertionError carries structured operands."""

from __future__ import annotations

from typing import Any

import oxitest
from oxitest._bridge._assert_error import (
    _OXITEST_NO_RHS,
    _OxitestAssertionError,
)
from oxitest._oxitest import rewrite_asserts


def _exec_rewritten(src: str, ns: dict[str, Any]) -> None:
    """Parse, rewrite via Rust, compile, and exec src with the given namespace."""
    tree, _bare = rewrite_asserts(src, "<test>")
    code = compile(tree, "<test>", "exec")
    exec(code, ns)


def test_compare_equal_failure_carries_left_right_op() -> None:
    """Rewritten assert x == y should populate left, right, and op on failure."""
    ns: dict[str, Any] = {"_OxitestAssertionError": _OxitestAssertionError, "x": 41}
    _exec_rewritten("def test_f():\n    assert x == 42\n", ns)
    with oxitest.raises(_OxitestAssertionError) as exc:
        ns["test_f"]()
    assert exc.value.left == 41, f"expected left == 41, got {exc.value.left!r}"
    assert exc.value.right == 42, f"expected right == 42, got {exc.value.right!r}"
    assert exc.value.op == "==", f"expected op == '==', got {exc.value.op!r}"


def test_compare_in_failure_carries_operands() -> None:
    """Rewritten 'assert x in collection' should populate left, right, and op='in'."""
    ns: dict[str, Any] = {"_OxitestAssertionError": _OxitestAssertionError, "x": "bob"}
    _exec_rewritten('def test_f():\n    assert x in ["alice", "carol"]\n', ns)
    with oxitest.raises(_OxitestAssertionError) as exc:
        ns["test_f"]()
    assert exc.value.left == "bob", f"expected left == 'bob', got {exc.value.left!r}"
    assert exc.value.right == ["alice", "carol"], (
        f"expected right == ['alice', 'carol'], got {exc.value.right!r}"
    )
    assert exc.value.op == "in", f"expected op == 'in', got {exc.value.op!r}"


def test_bool_assert_failure_carries_value() -> None:
    """Rewritten bare 'assert flag' should set left=flag and right=_OXITEST_NO_RHS."""
    ns: dict[str, Any] = {
        "_OxitestAssertionError": _OxitestAssertionError,
        "_oxitest_no_rhs": _OXITEST_NO_RHS,
        "flag": False,
    }
    _exec_rewritten("def test_f():\n    assert flag\n", ns)
    with oxitest.raises(_OxitestAssertionError) as exc:
        ns["test_f"]()
    assert exc.value.left is False, f"expected left is False, got {exc.value.left!r}"
    assert exc.value.right is _OXITEST_NO_RHS, (
        f"expected right is _OXITEST_NO_RHS (sentinel), got {exc.value.right!r}"
    )
    assert exc.value.op == "", (
        f"expected op == '' for bool assert, got {exc.value.op!r}"
    )


def test_assert_with_message_carries_why() -> None:
    """Assert with a message string should carry that message in the exception args."""
    ns: dict[str, Any] = {"_OxitestAssertionError": _OxitestAssertionError, "x": 41}
    _exec_rewritten('def test_f():\n    assert x == 42, "should be 42"\n', ns)
    with oxitest.raises(_OxitestAssertionError) as exc:
        ns["test_f"]()
    assert exc.value.args[0] == "should be 42", (
        f"expected error message 'should be 42', got {exc.value.args[0]!r}"
    )
    assert exc.value.op == "==", f"expected op == '==', got {exc.value.op!r}"


def test_chained_compare_left_untouched() -> None:
    """Chained comparisons (a < b < c) are not rewritten and fall back gracefully."""
    ns: dict[str, Any] = {"_OxitestAssertionError": _OxitestAssertionError, "x": 20}
    _exec_rewritten("def test_f():\n    assert 1 < x < 10\n", ns)
    with oxitest.raises(AssertionError) as exc:
        ns["test_f"]()
    assert not isinstance(exc.value, _OxitestAssertionError), (
        "chained comparison should fall back to plain AssertionError, not "
        "_OxitestAssertionError"
    )


def test_bool_op_assert_left_untouched() -> None:
    """Boolean 'and' assertions fall back to plain AssertionError (not rewritten)."""
    ns: dict[str, Any] = {
        "_OxitestAssertionError": _OxitestAssertionError,
        "a": True,
        "b": False,
    }
    _exec_rewritten("def test_f():\n    assert a and b\n", ns)
    with oxitest.raises(AssertionError) as exc:
        ns["test_f"]()
    assert not isinstance(exc.value, _OxitestAssertionError), (
        "boolean 'and' assert should fall back to plain AssertionError, not "
        "_OxitestAssertionError"
    )


def test_passing_assert_does_not_raise() -> None:
    """A rewritten assert that passes should not raise any exception."""
    ns: dict[str, Any] = {"_OxitestAssertionError": _OxitestAssertionError, "x": 42}
    _exec_rewritten("def test_f():\n    assert x == 42\n", ns)
    ns["test_f"]()  # must not raise


def test_bare_assert_map_returned() -> None:
    """Rust rewriter returns bare-assert-by-function map."""
    src = (
        "def test_a():\n    assert True\n    assert 1 == 1, 'ok'\n"
        "\ndef test_b():\n    assert False\n"
    )
    _tree, bare = rewrite_asserts(src, "<test>")
    # test_a has one bare assert on line 2 (line 3 has a message)
    assert bare["test_a"] == [2], f"expected [2], got {bare.get('test_a')}"
    # test_b has one bare assert on line 6
    assert bare["test_b"] == [6], f"expected [6], got {bare.get('test_b')}"


def test_bare_assert_nested_fn_attributed_to_outer() -> None:
    """Bare asserts in nested functions attribute to outermost function."""
    src = (
        "def test_outer():\n    def helper():\n        assert True\n    assert False\n"
    )
    _tree, bare = rewrite_asserts(src, "<test>")
    # Both the nested assert (line 3) and direct assert (line 4) attribute to test_outer
    assert bare["test_outer"] == [3, 4], (
        f"expected [3, 4], got {bare.get('test_outer')}"
    )
