"""Tested examples for the use-markers how-to guide."""

import sys

import oxitest
import oxitest as oxi


def broken_function():
    """Stub — returns wrong value to demonstrate xfail."""
    return 0


def long_running_operation():
    """Stub — returns immediately."""
    return "done"


# fmt: off
# --8<-- [start:custom-marker]
@oxitest.mark.slow
def test_large_sort():
    data = list(range(1_000_000, 0, -1))
    assert sorted(data) == list(range(1, 1_000_001)), "sorted should produce ascending order"
# --8<-- [end:custom-marker]

# --8<-- [start:skip-with-reason]
@oxitest.mark.skip(reason="feature not yet implemented")
def test_not_ready_with_reason():
    ...
# --8<-- [end:skip-with-reason]

# --8<-- [start:skip-without-reason]
@oxitest.mark.skip
def test_not_ready_bare():
    ...
# --8<-- [end:skip-without-reason]

# --8<-- [start:skip-imperative]
def test_platform_specific():
    if sys.platform != "linux":
        oxitest.skip("Linux only")
    assert sys.platform == "linux", "should only run on Linux"
# --8<-- [end:skip-imperative]

# --8<-- [start:skip-conditional]
@oxitest.mark.skip(when=sys.platform == "win32", reason="POSIX only")
def test_symlinks():
    ...
# --8<-- [end:skip-conditional]

# --8<-- [start:xfail]
@oxitest.mark.xfail(reason="upstream bug #123")
def test_known_bug():
    assert broken_function() == 42
# --8<-- [end:xfail]

# --8<-- [start:timeout]
@oxitest.mark.timeout(seconds=5)
def test_must_finish_quickly():
    result = long_running_operation()
    assert result is not None, "operation should return a result"
# --8<-- [end:timeout]

# --8<-- [start:module-mark]
oxi_mark = oxi.mark.timeout(5)
# --8<-- [end:module-mark]

# --8<-- [start:module-mark-list]
oxi_mark = [oxi.mark.timeout(5), oxi.mark.slow]
# --8<-- [end:module-mark-list]

# --8<-- [start:inprocess]
@oxi.mark.inprocess
def test_needs_debugger():
    entry = sys.modules["__main__"].__file__ or ""
    assert not entry.endswith("worker.py"), "ran on the coordinator, not a worker"
# --8<-- [end:inprocess]
# fmt: on
