"""Fixture definitions for the use-fixtures how-to guide.

Every snippet marker in this package is included by `docs/user/how-to/use-fixtures.md`.
A declaration that no page includes does not belong here — the directory
exists to keep the guide's examples executable.
"""

import tempfile
from pathlib import Path

import oxitest
from oxitest import TestContext


# fmt: off
# --8<-- [start:imperative-teardown]
@oxitest.fixture(lifetime="function")
def managed_file(ctx: TestContext) -> Path:
    fd, name = tempfile.mkstemp()
    import os
    os.close(fd)
    path = Path(name)
    path.write_text("hello", encoding="utf-8")
    ctx.addfinalizer(lambda: path.unlink(missing_ok=True))
    return path
# --8<-- [end:imperative-teardown]
# fmt: on
