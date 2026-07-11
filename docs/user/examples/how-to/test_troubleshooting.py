"""Tested examples for the troubleshooting how-to guide."""

from oxitest import TempDir


# fmt: off
# --8<-- [start:tempdir-path-workaround]
def test_example(tmp: TempDir) -> None:
    assert tmp.path.is_dir(), "TempDir should inject a temporary directory"
# --8<-- [end:tempdir-path-workaround]
# fmt: on
