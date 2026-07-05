from __future__ import annotations

__all__ = ["warns"]

import re
import warnings
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def warns(category: type[Warning], *, match: str | None = None) -> Iterator[None]:
    """Assert that a block of code emits a warning of the expected category.

    Args:
        category: The warning category expected to be emitted.
        match: Optional regex pattern checked against `str(warning.message)`
               via `re.search`. Raises `AssertionError` if no matching
               warning's message contains the pattern.

    Raises:
        AssertionError: If no warning of `category` is emitted, or if
                        `match` is given and no matching warning's message
                        contains the pattern.

    Example::

        with oxitest.warns(UserWarning, match="deprecated"):
            call_legacy_api()
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        yield
    matching = [w for w in caught if issubclass(w.category, category)]
    if not matching:
        msg = f"No warning of type {category.__name__!r} was raised"
        raise AssertionError(msg)
    if match is not None:
        pattern_matches = [w for w in matching if re.search(match, str(w.message))]
        if not pattern_matches:
            messages = [str(w.message) for w in matching]
            msg = (
                f"Pattern {match!r} not found in any "
                f"{category.__name__!r} warning messages. "
                f"Got: {messages!r}"
            )
            raise AssertionError(msg)
