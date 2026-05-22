from __future__ import annotations

__all__ = ["raises"]

import re
from typing import Any

_ExcType = type[BaseException] | tuple[type[BaseException], ...]


def _exc_name(exc_type: _ExcType) -> str:
    if isinstance(exc_type, tuple):
        return "(" + " | ".join(t.__name__ for t in exc_type) + ")"
    return exc_type.__name__


class _RaisesContext:
    """Context manager returned by raises(). Stores the caught exception in .value."""

    def __init__(self, exc_type: _ExcType, match: str | None) -> None:
        self._exc_type = exc_type
        self._match = match
        self.value: BaseException | None = None

    def __enter__(self) -> _RaisesContext:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        if exc_val is None:
            raise AssertionError(f"Expected {_exc_name(self._exc_type)} to be raised")
        if not isinstance(exc_val, self._exc_type):
            return False  # wrong type — let it propagate
        if self._match is not None and not re.search(self._match, str(exc_val)):
            raise AssertionError(
                f"Pattern {self._match!r} not found in {str(exc_val)!r}"
            )
        self.value = exc_val
        return True  # suppress the exception


def raises(exc_type: _ExcType, *, match: str | None = None) -> _RaisesContext:
    """Assert that a block of code raises the expected exception type.

    Args:
        exc_type: The exception type, or a tuple of types, expected to be raised.
        match: Optional regex pattern checked against ``str(exc)`` via
               ``re.search``. Raises ``AssertionError`` if the pattern is
               not found in the exception message.

    Returns:
        A context manager. Use ``as exc_info`` to access ``exc_info.value``
        (the caught exception) after the block.

    Example::

        with oxitest.raises(ValueError, match="must be positive"):
            validate(-1)

        with oxitest.raises((AttributeError, RuntimeError)):
            obj.method()

        with oxitest.raises(KeyError) as exc_info:
            d["missing"]
        assert exc_info.value.args[0] == "missing"
    """
    return _RaisesContext(exc_type, match)
