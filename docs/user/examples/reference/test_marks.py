"""Tested examples for the marks reference page."""

import os
import sys

import oxitest


# fmt: off
# --8<-- [start:skip-reference]
@oxitest.mark.skip(reason="not implemented yet")
def test_feature() -> None:
    ...


@oxitest.mark.skip(when=sys.platform == "win32", reason="POSIX only")
def test_symlinks() -> None:
    ...
# --8<-- [end:skip-reference]

# --8<-- [start:inprocess-reference]
@oxitest.mark.inprocess
def test_env_mutation():
    os.environ["KEY"] = "value"
    assert os.environ["KEY"] == "value", "inprocess should allow env mutation"
# --8<-- [end:inprocess-reference]
# fmt: on
