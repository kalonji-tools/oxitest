"""Every function-lifetime route that can carry test identity (#1879)."""

from __future__ import annotations

import oxitest as oxi
from oxitest import Fixture, TempDir, TestIdentity


@oxi.fixture(lifetime="function")
def plain(test: TestIdentity) -> str:
    """The plain parameter route."""
    return test.name


@oxi.fixture(lifetime="function")
def case_view(test: TestIdentity) -> str:
    """Renders name, param_id and marks so one assertion covers all three."""
    return f"{test.name}|{test.param_id}|{sorted(test.marks)}"


@oxi.fixture(lifetime="function")
def workspace(tmp: Fixture[TempDir]) -> str:
    """A builtin resolved UNDER a fixture — its prefix must not move."""
    return tmp.path.name


_ARRANGED_SEEN: list[str] = []


@oxi.fixture(lifetime="function")
def arranged(test: TestIdentity) -> str:
    """Reached by @oxi.arrange — the route #1740 rewrote, so pin it here."""
    _ARRANGED_SEEN.append(test.name)
    return test.name


@oxi.fixture(lifetime="function")
def arranged_seen() -> list[str]:
    """What the arranged fixture recorded, so a test can read it back."""
    return _ARRANGED_SEEN
