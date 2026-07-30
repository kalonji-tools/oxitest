"""An async fixture at ``function`` lifetime — the narrowest illegal cell.

A sync test cannot await anything, so handing it this fixture can only produce
an un-awaited coroutine. #1733 removed that silent failure on the qualified
route; the shortcut route must not reintroduce it.
"""

from __future__ import annotations

import oxitest as oxi

from slice7_shortcut_async_sync._kinds import Conn


@oxi.fixture(lifetime="function")
async def conn() -> Conn:
    return Conn("async-function-scope")
