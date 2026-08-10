"""End-to-end coverage for module identity (#1680).

Two layouts, because the fix is rootdir-relative rather than derived from an
``__init__.py`` walk: ``pkg_layout`` has ``__init__.py``, ``ns_layout`` is a
PEP 420 namespace package and has none. Both symptoms appear in both trees —
a module-level relative import, and a library reading the caller's ``__name__``.

Each tree runs serially and under ``-n 2``. Each worker calls
``ensure_rootdir_importable`` for itself and owns its own ``sys.path``, and the
adopted name is a function of ``sys.path`` — the worker is the dimension no
premise behind this change ever varied.

The packages are **not** called ``tests``. This repository is installed
editable, which puts ``<repo>/python`` on ``sys.path``, and that directory
provides a top-level ``tests``. A fixture package by that name would be
shadowed, the round-trip check would correctly decline it, and both trees would
pass without exercising the fix at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import oxitest as oxi
from tests import helpers

_TREES = Path(__file__).parent / "data" / "module_identity"


@dataclass(frozen=True)
class Case:
    """One tree run one way."""

    layout: str
    extra: tuple[str, ...]


@oxi.parametrize(
    pkg_serial=Case(layout="pkg_layout", extra=()),
    pkg_parallel=Case(layout="pkg_layout", extra=("-n", "2")),
    ns_serial=Case(layout="ns_layout", extra=()),
    ns_parallel=Case(layout="ns_layout", extra=("-n", "2")),
)
def test_both_symptoms_are_fixed_in_both_layouts(case: Case) -> None:
    """A relative import resolves and the caller's real module name is visible."""
    # Arrange
    tree = _TREES / case.layout

    # Act
    stdout, stderr, code = helpers.run_oxitest(None, *case.extra, cwd=str(tree))

    # Assert
    assert code == 0, (
        f"a relative import that fails collection and a caller name that does "
        f"not match both take this run non-zero\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "2 passed" in stdout, (
        f"both tests must run, not only the one that survives collection — a "
        f"collection error can leave a summary that reads green\n"
        f"stdout:\n{stdout}"
    )
