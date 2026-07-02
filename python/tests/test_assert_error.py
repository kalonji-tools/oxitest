from __future__ import annotations

from typing import Any

from oxitest._bridge._assert_error import (
    _OXITEST_NO_RHS,
    _OxitestAssertionError,
)


def _exec_rewritten(src: str, ns: dict[str, Any]) -> None:
    """Parse, rewrite via Rust, compile, and exec src with the given namespace."""
    from oxitest._oxitest import rewrite_asserts

    tree, _bare = rewrite_asserts(src, "<test>")
    code = compile(tree, "<test>", "exec")
    exec(code, ns)


def test_compare_equal_failure_carries_left_right_op():
    ns: dict[str, Any] = {"_OxitestAssertionError": _OxitestAssertionError, "x": 41}
    _exec_rewritten("def test_f():\n    assert x == 42\n", ns)
    try:
        ns["test_f"]()
        raise AssertionError("should have raised")
    except _OxitestAssertionError as e:
        assert e.left == 41, f"expected e.left == 41, got {e.left!r}"
        assert e.right == 42, f"expected e.right == 42, got {e.right!r}"
        assert e.op == "==", f"expected e.op == '==', got {e.op!r}"


def test_compare_in_failure_carries_operands():
    ns: dict[str, Any] = {"_OxitestAssertionError": _OxitestAssertionError, "x": "bob"}
    _exec_rewritten('def test_f():\n    assert x in ["alice", "carol"]\n', ns)
    try:
        ns["test_f"]()
        raise AssertionError("should have raised")
    except _OxitestAssertionError as e:
        assert e.left == "bob", f"expected e.left == 'bob', got {e.left!r}"
        assert e.right == ["alice", "carol"], (
            f"expected e.right == ['alice', 'carol'], got {e.right!r}"
        )
        assert e.op == "in", f"expected e.op == 'in', got {e.op!r}"


def test_bool_assert_failure_carries_value():
    ns: dict[str, Any] = {
        "_OxitestAssertionError": _OxitestAssertionError,
        "_oxitest_no_rhs": _OXITEST_NO_RHS,
        "flag": False,
    }
    _exec_rewritten("def test_f():\n    assert flag\n", ns)
    try:
        ns["test_f"]()
        raise AssertionError("should have raised")
    except _OxitestAssertionError as e:
        assert e.left is False, f"expected e.left is False, got {e.left!r}"
        assert e.right is _OXITEST_NO_RHS, (
            f"expected e.right is _OXITEST_NO_RHS (sentinel), got {e.right!r}"
        )
        assert e.op == "", f"expected e.op == '' for bool assert, got {e.op!r}"


def test_assert_with_message_carries_why():
    ns: dict[str, Any] = {"_OxitestAssertionError": _OxitestAssertionError, "x": 41}
    _exec_rewritten('def test_f():\n    assert x == 42, "should be 42"\n', ns)
    try:
        ns["test_f"]()
        raise AssertionError("should have raised")
    except _OxitestAssertionError as e:
        assert e.args[0] == "should be 42", (
            f"expected error message 'should be 42', got {e.args[0]!r}"
        )
        assert e.op == "==", f"expected e.op == '==', got {e.op!r}"


def test_chained_compare_left_untouched():
    """Chained comparisons (a < b < c) are not rewritten — fall back gracefully."""
    ns: dict[str, Any] = {"_OxitestAssertionError": _OxitestAssertionError, "x": 20}
    _exec_rewritten("def test_f():\n    assert 1 < x < 10\n", ns)
    try:
        ns["test_f"]()
        raise AssertionError("should have raised")
    except AssertionError as e:
        assert not isinstance(e, _OxitestAssertionError), (
            "chained comparison should fall back to plain AssertionError, not "
            "_OxitestAssertionError"
        )


def test_bool_op_assert_left_untouched():
    """assert a and b is not rewritten."""
    ns: dict[str, Any] = {
        "_OxitestAssertionError": _OxitestAssertionError,
        "a": True,
        "b": False,
    }
    _exec_rewritten("def test_f():\n    assert a and b\n", ns)
    try:
        ns["test_f"]()
        raise AssertionError("should have raised")
    except AssertionError as e:
        assert not isinstance(e, _OxitestAssertionError), (
            "boolean 'and' assert should fall back to plain AssertionError, not "
            "_OxitestAssertionError"
        )


def test_passing_assert_does_not_raise():
    ns: dict[str, Any] = {"_OxitestAssertionError": _OxitestAssertionError, "x": 42}
    _exec_rewritten("def test_f():\n    assert x == 42\n", ns)
    ns["test_f"]()  # must not raise


def test_bare_assert_map_returned():
    """Rust rewriter returns bare-assert-by-function map."""
    from oxitest._oxitest import rewrite_asserts

    src = (
        "def test_a():\n    assert True\n    assert 1 == 1, 'ok'\n"
        "\ndef test_b():\n    assert False\n"
    )
    _tree, bare = rewrite_asserts(src, "<test>")
    # test_a has one bare assert on line 2 (line 3 has a message)
    assert bare["test_a"] == [2], f"expected [2], got {bare.get('test_a')}"
    # test_b has one bare assert on line 6
    assert bare["test_b"] == [6], f"expected [6], got {bare.get('test_b')}"


def test_bare_assert_nested_fn_attributed_to_outer():
    """Bare asserts in nested functions attribute to outermost function."""
    from oxitest._oxitest import rewrite_asserts

    src = (
        "def test_outer():\n    def helper():\n        assert True\n    assert False\n"
    )
    _tree, bare = rewrite_asserts(src, "<test>")
    # Both the nested assert (line 3) and direct assert (line 4) attribute to test_outer
    assert bare["test_outer"] == [3, 4], (
        f"expected [3, 4], got {bare.get('test_outer')}"
    )
