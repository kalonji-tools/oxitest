"""A shared test utility as a plain module — the whole point of #1780.

No decorator, no registration, no proxy. If this file is reachable by
``import`` then the helper *system* was never needed, which is what #1700
concluded.
"""

from __future__ import annotations


def make_user(name: str = "test") -> dict[str, str]:
    return {"name": name, "kind": "user"}


def describe(name: str = "test") -> str:
    """Return a one-line description of a user.

    The doctest below imports from the test tree, which only works if the
    doctest runner inherits the appended sys.path entry. It does — `run_doctest`
    is dispatched from inside `run_test` (`executor.py:587`), which runs after
    `create_session` on both the serial and worker paths — but that is a fact
    about the call graph and deserves a regression test rather than trust.

    Examples:
        >>> from rootdir_import.helpers import make_user
        >>> describe(make_user("alice")["name"])
        'user: alice'

    """
    return f"user: {name}"
