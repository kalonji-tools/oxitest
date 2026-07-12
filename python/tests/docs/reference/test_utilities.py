"""Tested examples for the utilities reference page."""

import sys

import oxitest


# fmt: off
# --8<-- [start:approx]
def test_pi():
    assert oxitest.approx(3.14159, abs=0.01) == 3.14, "should be approximately pi"


def test_vector():
    assert oxitest.approx([0.3, 0.3]) == [0.1 + 0.2, 0.3], "vector approx"


def test_mapping():
    assert oxitest.approx({"x": 1.0, "y": 2.001}, abs=0.01) == {"x": 1.0, "y": 2.0}, "mapping approx"
# --8<-- [end:approx]

# --8<-- [start:skip-runtime]
def test_only_on_linux():
    if sys.platform != "linux":
        oxitest.skip("requires Linux")
    assert True, "should only run on Linux"
# --8<-- [end:skip-runtime]
# fmt: on
