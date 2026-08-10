"""Build a :class:`FixtureSession`, with the Rootdir importable.

Split out of ``conftest_loader.create_session`` (#1720). That function did
three things: it appended the Rootdir to ``sys.path``, it loaded ``conftest.py``
files, and it built the session. Only the middle one is about ``conftest.py``,
and only the middle one is being retired.

The ``sys.path`` append is #1780, and #1720 asserts it as a regression guard:
a stateless utility must stay reachable by plain import from a test module.
It lived in ``conftest_loader`` for historical reasons rather than conceptual
ones, so it needs a home that outlives that module — this one.
"""

from __future__ import annotations

__all__ = ["create_session"]


from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._syspath import ensure_rootdir_importable


def create_session(*, rootdir: str | None = None) -> FixtureSession:
    """Build an empty session and make *rootdir* importable.

    Args:
        rootdir: Project Rootdir to append to ``sys.path`` so test modules can
            import sibling utility modules (#1780). ``None`` skips the append
            entirely — oxitest's own runner builds a session during its own
            bootstrap, before a Rootdir is known.
    """
    if rootdir is not None:
        ensure_rootdir_importable(rootdir)
    return FixtureSession([], rootdir=rootdir)
