"""The rootdir package's own test.

It has to exist: declaration homes are registered per directory that holds a
test file, so a ``__fixtures__.py`` in a package with no tests of its own is
never discovered and ``api/test_api.py``'s ancestor lookup would fail for a
reason that has nothing to do with B1.
"""

from __future__ import annotations

from oxitest import Fixtures


def test_rootdir_package_resolves_its_own_fixture(fx: Fixtures) -> None:
    assert fx.slice6_boundary.root_conn == "root", (
        "the ancestor's own base case — if this fails, the ancestor assertion "
        "in api/test_api.py is about registration, not about visibility"
    )
