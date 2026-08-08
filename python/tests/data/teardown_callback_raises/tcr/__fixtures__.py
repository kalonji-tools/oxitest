"""A yield fixture whose teardown raises — the negative control arm.

Its failure has always been reported, because ``_unpack_sync`` wraps the
generator drain in ``safe_teardown`` before the teardown ever reaches a
teardown list. That is what makes it the control: if the function tier's own
wrap ever re-reported an exception the inner wrapper had already handled, this
fixture would produce a second, callback-worded line for one failure.
"""

from __future__ import annotations

from collections.abc import Iterator

import oxitest as oxi


@oxi.fixture(lifetime="function")
def loud() -> Iterator[str]:
    yield "v"
    msg = "YIELD-FIXTURE blew up"
    raise RuntimeError(msg)
