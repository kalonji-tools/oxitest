"""Tested examples for the getting-started tutorial."""


# --8<-- [start:full-test-file]
def test_addition():
    assert 1 + 1 == 2


def test_multiplication():
    assert 3 * 4 == 12


def test_string_repeat():
    assert "ha" * 3 == "hahaha"


# --8<-- [end:full-test-file]


def failing_multiplication():
    # --8<-- [start:failing-multiplication]
    assert 3 * 4 == 13  # wrong expected value
    # --8<-- [end:failing-multiplication]


def fixed_multiplication():
    # --8<-- [start:fixed-multiplication]
    assert 3 * 4 == 12
    # --8<-- [end:fixed-multiplication]
