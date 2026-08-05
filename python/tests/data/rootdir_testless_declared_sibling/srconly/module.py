"""Declared in `testpaths`, holds no test file.

This is the doctest-coverage subject: a project declares its source tree so the
audit reaches it. Nothing here matches `python_files`, which is exactly why it
must not contribute to the rootdir package fold.
"""

from __future__ import annotations


def helper() -> str:
    """Return a value.

    Examples:
        >>> helper()
        'value'
    """
    return "value"
