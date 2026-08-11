"""One binding type per lifetime tier.

Four distinct types rather than four ``str`` fixtures: ``Fixture[T]``
resolution consults the type index before the name index, and four fixtures
sharing ``str`` would raise ``AmbiguousFixtureError`` before the async
question was ever reached.
"""

from __future__ import annotations


class Fn:
    """Value of the ``function``-lifetime async fixture."""

    label = "fn"


class Mod:
    """Value of the ``module``-lifetime async fixture."""

    label = "mod"


class Pkg:
    """Value of the ``package``-lifetime async fixture."""

    label = "pkg"


class Sess:
    """Value of the ``session``-lifetime async fixture."""

    label = "sess"


class Ref:
    """Value of the module-lifetime async fixture reached by ``FixtureRef``."""

    label = "ref"
