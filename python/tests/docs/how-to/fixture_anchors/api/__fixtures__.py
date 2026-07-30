"""Fixture declarations for the use-fixtures how-to guide.

Anchored at ``api/``: everything declared here is visible from this directory
and every directory below it, and from nowhere else (ADR-0009 Rule 3). The
namespace ``api`` is the directory's own basename, which is why the tree lives
under ``fixture_anchors/`` rather than beside the legacy examples in
``how-to/fixtures/`` — ``fixtures`` is listed in ``norecursedirs``, so nothing
below that directory is collected by the doc-example run.
"""

from __future__ import annotations

from collections.abc import Iterator


class Pool:
    """Stub connection pool for doc examples."""

    def __init__(self, *, url: str) -> None:
        self.url = url
        self.closed = False

    def close(self) -> None:
        self.closed = True


# fmt: off
# --8<-- [start:declare-fixture]
import oxitest as oxi


@oxi.fixture(lifetime="function")
def tenant() -> str:
    return "acme"
# --8<-- [end:declare-fixture]

# --8<-- [start:yield-teardown]
@oxi.fixture(lifetime="function")
def audit_log() -> Iterator[list[str]]:
    entries: list[str] = []
    yield entries
    entries.clear()
# --8<-- [end:yield-teardown]

# --8<-- [start:fixture-dependency]
@oxi.fixture(lifetime="function")
def request_headers(tenant: oxi.Fixture[str]) -> dict[str, str]:
    return {"X-Tenant": tenant}
# --8<-- [end:fixture-dependency]

# --8<-- [start:module-lifetime]
@oxi.fixture(lifetime="module")
def pool() -> Iterator[Pool]:
    resource = Pool(url="postgres://localhost/test")
    yield resource
    resource.close()
# --8<-- [end:module-lifetime]
# fmt: on
