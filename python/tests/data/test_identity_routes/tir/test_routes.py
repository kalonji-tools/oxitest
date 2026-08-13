"""One test per route. Each asserts its own name, so a shared value fails."""

from __future__ import annotations

from dataclasses import dataclass

import oxitest as oxi
from oxitest import Fixture


@dataclass(frozen=True)
class _Case:
    """One parametrize case."""

    label: str


def test_plain_route(plain: Fixture[str]) -> None:
    """The plain parameter route names this test."""
    assert plain == "test_plain_route", (
        "the plain parameter route must name THIS test; a fixture-name or a "
        "first-test value both look well-formed and are both wrong (#1874)"
    )


@oxi.parametrize(alpha=_Case("alpha"), beta=_Case("beta"))
def test_parametrized_route(case: _Case, case_view: Fixture[str]) -> None:
    """param_id differs per case."""
    assert case_view == f"test_parametrized_route|{case.label}|[]", (
        "param_id must differ per case; one value for both cases is the "
        "shared-identity defect wearing a parametrize hat"
    )


@oxi.mark.slow
def test_marked_route(case_view: Fixture[str]) -> None:
    """Marks reach the fixture; an unparametrized param_id is None."""
    assert case_view == "test_marked_route|None|['slow']", (
        "marks must reach the fixture, and an unparametrized test's param_id "
        "is None rather than a raise"
    )


def test_tempdir_prefix_is_unmoved(workspace: Fixture[str]) -> None:
    """A TempDir under a fixture keeps the fixture's prefix."""
    assert workspace.startswith("workspace_"), (
        "a TempDir resolved UNDER a fixture is prefixed by that fixture, not "
        "by the test — handing the test's bundle down must not move it"
    )


@oxi.arrange("arranged")
def test_arrange_route(arranged_seen: Fixture[list[str]]) -> None:
    """An arranged fixture resolves identity for the test that arranged it."""
    assert arranged_seen == ["test_arrange_route"], (
        "@oxi.arrange resolves the fixture for its side effect, on a path "
        "#1740 rewrote — the route most likely to move and the one that had "
        f"no test; got {arranged_seen}"
    )
