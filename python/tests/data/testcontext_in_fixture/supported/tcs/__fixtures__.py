"""The supported use of ``ctx`` inside a fixture: teardown registration.

``module_path`` is exercised alongside it. It is not test identity — it is
where resolution is — so it must keep answering inside a fixture body.

The finalizer records to the file named by ``TESTCONTEXT_LOG`` rather than to a
module-level list. A test module importing ``__fixtures__`` by name gets a
second module object, and the loader's copy is the one whose fixtures run — so
an in-memory list would always read back empty and the assertion would prove
the opposite of what it says.
"""

from __future__ import annotations

import os
from pathlib import Path

import oxitest as oxi
from oxitest import TestContext


def _record(event: str) -> None:
    """Append one event to the log. A missing env value is a hard error."""
    with Path(os.environ["TESTCONTEXT_LOG"]).open("a") as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="function")
def schema(ctx: TestContext) -> str:
    name = "test_schema"
    ctx.addfinalizer(lambda: _record(f"FINALIZED {name}"))
    return name


@oxi.fixture(lifetime="function")
def where_i_am(ctx: TestContext) -> str:
    return ctx.module_path
