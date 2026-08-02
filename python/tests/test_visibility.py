"""Unit coverage for the B1 path predicate (#1713).

Nine topologies, no subprocess. The layer exists because of one landmine that
no hand-written directory tree catches: ``"/t/apiv2".startswith("/t/api")`` is
True and the two are siblings, so a prefix-based predicate declares a sibling
package visible and B1 leaks silently. You would have to *think* to name a
directory ``apiv2``; a unit test can just assert it.
"""

from __future__ import annotations

from dataclasses import dataclass

import oxitest as oxi
from oxitest import BoundaryError
from oxitest._bridge._visibility import anchor_depth, anchors_overlap, is_visible


@dataclass(frozen=True)
class VisibilityCase:
    """Parametrize case for the nine-topology B1 visibility matrix."""

    label: str
    anchor: str
    defining: str
    module_path: str
    expected: bool


@oxi.parametrize(
    self_=VisibilityCase(
        label="self",
        anchor="/t/api",
        defining="/t/api/__fixtures__.py",
        module_path="/t/api/test_a.py",
        expected=True,
    ),
    descendant_dir=VisibilityCase(
        label="descendant-dir",
        anchor="/t/api",
        defining="/t/api/__fixtures__.py",
        module_path="/t/api/v1/test_a.py",
        expected=True,
    ),
    rootdir=VisibilityCase(
        label="rootdir",
        anchor="/t",
        defining="/t/__fixtures__.py",
        module_path="/t/api/v1/test_a.py",
        expected=True,
    ),
    upward=VisibilityCase(
        label="upward",
        anchor="/t/api/v1",
        defining="/t/api/v1/__fixtures__.py",
        module_path="/t/api/test_a.py",
        expected=False,
    ),
    sibling=VisibilityCase(
        label="sibling",
        anchor="/t/api",
        defining="/t/api/__fixtures__.py",
        module_path="/t/admin/test_a.py",
        expected=False,
    ),
    cousin=VisibilityCase(
        label="cousin",
        anchor="/t/api/v1",
        defining="/t/api/v1/__fixtures__.py",
        module_path="/t/admin/v1/test_a.py",
        expected=False,
    ),
    inline_same_module=VisibilityCase(
        label="inline-same-module",
        anchor="/t/api/test_a.py",
        defining="/t/api/test_a.py",
        module_path="/t/api/test_a.py",
        expected=True,
    ),
    inline_sibling_module=VisibilityCase(
        label="inline-sibling-module",
        anchor="/t/api/test_a.py",
        defining="/t/api/test_a.py",
        module_path="/t/api/test_b.py",
        expected=False,
    ),
    prefix_trap=VisibilityCase(
        label="prefix-trap",
        anchor="/t/api",
        defining="/t/api/__fixtures__.py",
        module_path="/t/apiv2/test_a.py",
        expected=False,
    ),
)
def test_is_visible_over_nine_topologies(case: VisibilityCase) -> None:
    """Every shape the tree can take, including the one a directory tree hides."""
    # Act
    actual = is_visible(
        anchor=case.anchor, defining=case.defining, module_path=case.module_path
    )

    # Assert
    assert actual is case.expected, (
        f"topology {case.label!r}: B1 says a fixture anchored at {case.anchor} is "
        f"usable only from that package or below, so {case.module_path} must be "
        f"{'allowed' if case.expected else 'refused'} — a wrong verdict here either "
        f"leaks fixtures across sibling packages or breaks legal descendant use"
    )


def test_visibility_is_not_symmetric() -> None:
    """A descendant's fixture is invisible to its own ancestor.

    Named separately from the parametrized 'upward' case because symmetry is the
    intuitive-but-wrong reading of "these two packages are related", and a
    reviewer scanning test names should see it refuted.
    """
    # Arrange
    anchor, defining = "/t/api/v1", "/t/api/v1/__fixtures__.py"

    # Act
    down = is_visible(
        anchor="/t/api",
        defining="/t/api/__fixtures__.py",
        module_path="/t/api/v1/test_a.py",
    )
    up = is_visible(anchor=anchor, defining=defining, module_path="/t/api/test_a.py")

    # Assert
    assert down is True and up is False, (
        "B1 is a containment relation, not an equivalence: an ancestor package's "
        "fixtures flow down but a descendant's must not flow up, or a package "
        "fixture could cache a value from a narrower boundary"
    )


def test_anchor_depth_orders_nested_anchors() -> None:
    """Deepest-visible-wins needs a total order on anchors."""
    # Arrange
    shallow, deep = "/t/api", "/t/api/v1"

    # Act
    ordered = anchor_depth(deep) > anchor_depth(shallow)

    # Assert
    assert ordered, (
        "resolution picks the deepest visible anchor so a nested __fixtures__.py "
        "overrides its parent's; without a depth order the winner falls back to "
        "filesystem walk order"
    )


@dataclass(frozen=True)
class AnchorOverlapCase:
    """Parametrize case for the anchor-collision predicate."""

    label: str
    first: str
    second: str
    expected: bool


@oxi.parametrize(
    nested=AnchorOverlapCase(
        label="nested", first="/t/api", second="/t/api/v1", expected=True
    ),
    nested_reversed=AnchorOverlapCase(
        label="nested-reversed", first="/t/api/v1", second="/t/api", expected=True
    ),
    identical=AnchorOverlapCase(
        label="identical", first="/t/api", second="/t/api", expected=True
    ),
    siblings=AnchorOverlapCase(
        label="siblings", first="/t/api", second="/t/admin", expected=False
    ),
    prefix_trap=AnchorOverlapCase(
        label="prefix-trap", first="/t/api", second="/t/apiv2", expected=False
    ),
    empty_first=AnchorOverlapCase(
        label="empty-first", first="", second="/t/api", expected=False
    ),
    empty_second=AnchorOverlapCase(
        label="empty-second", first="/t/api", second="", expected=False
    ),
)
def test_anchors_overlap_decides_real_collisions(case: AnchorOverlapCase) -> None:
    """Same (namespace, name) in disjoint subtrees is legal, not a clash."""
    # Act
    actual = anchors_overlap(case.first, case.second)

    # Assert
    assert actual is case.expected, (
        f"anchor pair {case.label!r}: namespaces are directory basenames, so "
        f"tests/api/v1 and tests/admin/v1 both derive 'v1' — treating that as a "
        f"duplicate declaration kills the run for two packages that can never "
        f"see each other's fixtures. The empty cases are the other direction: "
        f"'' has no components, and a prefix test that accepts it would make a "
        f"path-less module overlap every anchor in the run"
    )


def test_boundary_error_names_anchor_test_and_exits() -> None:
    """The message has to carry the whole fix, not a pointer to the docs."""
    # Act
    message = str(
        BoundaryError(
            "api_conn",
            "api",
            "/t/api",
            "/t/admin/test_admin.py",
            leaf_exists=True,
        )
    )

    # Assert
    for expected in ("fixture-boundary", "api.api_conn", "api", "test_admin.py"):
        assert expected in message, (
            f"the message must contain {expected!r}: a boundary violation is "
            f"only actionable if the failure output names the fixture's anchor "
            f"and the test's own package, so the user does not have to go "
            f"reconstruct the tree by hand"
        )
    assert message.count("\n    ") >= 3, (
        "at least three numbered exits must be listed — B1 has no escape hatch, "
        "so the message is the only place the legal restructurings appear"
    )


def test_boundary_error_appends_the_leaf_fact_without_leading_with_it() -> None:
    """Typo plus boundary: both true, boundary first (decision 9)."""
    # Act
    message = str(
        BoundaryError(
            "typo", "api", "/t/api", "/t/admin/test_admin.py", leaf_exists=False
        )
    )

    # Assert
    assert message.index("fixture-boundary") < message.index("typo'"), (
        "leading with the misspelling implies that fixing the spelling makes "
        "the access work, which is false — the access is illegal either way, "
        "and a leaf-first message costs the user two runs to learn two facts"
    )
    assert "will not make this access legal" in message, (
        "the leaf fact must be stated *and* defused; naming the missing fixture "
        "without saying it is not the blocker is the misleading half on its own"
    )
