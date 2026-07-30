"""Fixtures anchored at ``api/``.

The admin tests are siblings, so B1 puts this out of their reach by every
route — including the shortcut, which is the point.
"""

from __future__ import annotations

import oxitest as oxi

from slice7_shortcut_cross._kinds import ApiOnly


@oxi.fixture(lifetime="function")
def api_only() -> ApiOnly:
    return ApiOnly("api")
