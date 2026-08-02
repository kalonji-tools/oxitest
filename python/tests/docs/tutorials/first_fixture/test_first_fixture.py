"""Tests for the first-fixture examples on the front page and in the tutorial."""

from oxitest import Fixture


# fmt: off
# --8<-- [start:use-fixture]
def test_sum(sample_numbers: Fixture[list[int]]) -> None:
    assert sum(sample_numbers) == 10, "the fixture supplies the numbers under test"
# --8<-- [end:use-fixture]
# fmt: on


# Tier check for `lifetime="function"`. The two tests are symmetric on purpose:
# each asserts the list arrives pristine, then mutates it, so whichever runs
# second fails under any caching. An assert-then-mutate pair would not — a
# `lifetime="module"` mutant survived exactly that shape.


def test_a_mutation_does_not_leak_into_the_next_test(
    sample_numbers: Fixture[list[int]],
) -> None:
    assert sample_numbers == [2, 3, 5], (
        "a function-lifetime fixture is rebuilt per test, so no earlier test's "
        "append can be visible here"
    )
    sample_numbers.append(7)


def test_a_mutation_does_not_leak_in_from_the_previous_test(
    sample_numbers: Fixture[list[int]],
) -> None:
    assert sample_numbers == [2, 3, 5], (
        "a function-lifetime fixture is rebuilt per test, so no earlier test's "
        "append can be visible here"
    )
    sample_numbers.append(11)
