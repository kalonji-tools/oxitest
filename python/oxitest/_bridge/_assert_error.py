from __future__ import annotations


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
    "_OXITEST_NO_RHS",
    "_OxitestAssertionError",
    "_OxitestNoRhs",
]
