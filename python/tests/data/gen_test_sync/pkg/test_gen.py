from collections.abc import Generator


def test_generator() -> Generator[None, None, None]:
    yield


def test_normal() -> None:
    value = 1
    assert value == 1, "a normal test beside the offender, to prove it is unaffected"
