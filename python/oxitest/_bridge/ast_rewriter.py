from __future__ import annotations

import ast
from typing import Any, cast


# NOTE: The rewriter emits `_OxitestAssertionError` as a bare name in
# generated AST. Callers (e.g. executor.py) must inject this class into the
# module's globals before exec:
# module.__dict__["_OxitestAssertionError"] = _OxitestAssertionError
class _OxitestAssertionError(AssertionError):
    """AssertionError subclass carrying operand info for enriched diagnostics."""

    def __init__(self, left: object, right: object, op: str, msg: str = "") -> None:
        super().__init__(msg)
        self.left = left
        self.right = right
        self.op = op


class _OxitestNoRhs:
    """Sentinel: this assertion had no right-hand operand (bool/value assert)."""


_OXITEST_NO_RHS = _OxitestNoRhs()

__all__ = [
    "OxitestAssertRewriter",
    "_BareAssertCollector",
    "_OxitestAssertionError",
    "_OxitestNoRhs",
    "_OXITEST_NO_RHS",
]


_OP_MAP: dict[type, str] = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.In: "in",
    ast.NotIn: "not in",
    ast.Is: "is",
    ast.IsNot: "is not",
}


def _load(id_: str) -> ast.Name:
    return ast.Name(id=id_, ctx=ast.Load())


def _store(id_: str) -> ast.Name:
    return ast.Name(id=id_, ctx=ast.Store())


def _const(value: object) -> ast.Constant:
    return ast.Constant(value=cast(Any, value))


class _BareAssertCollector(ast.NodeVisitor):
    """Collect bare assert line numbers grouped by outermost function name.

    A bare assert is ``assert expr`` with no message argument.
    Line numbers are 1-based absolute (file-level), matching the adjusted
    values produced by ``_find_bare_asserts`` in executor.py.
    Bare asserts inside nested functions are attributed to the outermost
    test function, matching the behaviour of ``inspect.getsourcelines``-based
    detection.
    """

    def __init__(self) -> None:
        self._fn_stack: list[str] = []
        self.by_fn: dict[str, list[int]] = {}

    def _visit_fn(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._fn_stack.append(node.name)
        self.generic_visit(node)
        self._fn_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_fn(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_fn(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        if node.msg is None and self._fn_stack:
            outer = self._fn_stack[
                0
            ]  # outermost function — matches getsourcelines scope
            self.by_fn.setdefault(outer, []).append(node.lineno)


class OxitestAssertRewriter(ast.NodeTransformer):
    """Rewrite assert statements to capture operand values on failure."""

    def visit_Assert(  # noqa: N802
        self, node: ast.Assert
    ) -> list[ast.stmt] | ast.Assert:
        test = node.test
        msg_node = node.msg  # None if no message

        if isinstance(test, ast.Compare):
            if len(test.comparators) != 1:
                # Chained comparison (a < b < c) — leave untouched
                return node
            op_str = _OP_MAP.get(type(test.ops[0]))
            if op_str is None:
                return node

            msg_expr: ast.expr = msg_node if msg_node is not None else _const("")
            stmts: list[ast.stmt] = [
                ast.Assign(
                    targets=[_store("_oxitest_l")],
                    value=test.left,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                ),
                ast.Assign(
                    targets=[_store("_oxitest_r")],
                    value=test.comparators[0],
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                ),
                ast.If(
                    test=ast.UnaryOp(
                        op=ast.Not(),
                        operand=ast.Compare(
                            left=_load("_oxitest_l"),
                            ops=test.ops,
                            comparators=[_load("_oxitest_r")],
                        ),
                    ),
                    body=[
                        ast.Raise(
                            exc=ast.Call(
                                func=_load("_OxitestAssertionError"),
                                args=[
                                    _load("_oxitest_l"),
                                    _load("_oxitest_r"),
                                    _const(op_str),
                                    msg_expr,
                                ],
                                keywords=[],
                            ),
                            cause=None,
                        )
                    ],
                    orelse=[],
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                ),
            ]
            return stmts

        if isinstance(test, ast.BoolOp):
            # assert a and b / assert a or b — leave untouched
            return node

        # Simple value assert: assert x, assert not x, assert fn(), etc.
        msg_expr = msg_node if msg_node is not None else _const("")
        stmts: list[ast.stmt] = [
            ast.Assign(
                targets=[_store("_oxitest_v")],
                value=test,
                lineno=node.lineno,
                col_offset=node.col_offset,
            ),
            ast.If(
                test=ast.UnaryOp(op=ast.Not(), operand=_load("_oxitest_v")),
                body=[
                    ast.Raise(
                        exc=ast.Call(
                            func=_load("_OxitestAssertionError"),
                            args=[
                                _load("_oxitest_v"),
                                _load("_oxitest_no_rhs"),
                                _const(""),
                                msg_expr,
                            ],
                            keywords=[],
                        ),
                        cause=None,
                    )
                ],
                orelse=[],
                lineno=node.lineno,
                col_offset=node.col_offset,
            ),
        ]
        return stmts
