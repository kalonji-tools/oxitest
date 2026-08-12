import functools
from collections.abc import Callable, Generator


def passthrough(fn: Callable[..., object]) -> Callable[..., object]:
    @functools.wraps(fn)
    def inner(*args: object, **kwargs: object) -> object:
        return fn(*args, **kwargs)

    return inner


@passthrough
def test_wrapped_generator() -> Generator[None, None, None]:
    yield


def test_normal() -> None:
    value = 1
    assert value == 1, "an ordinary test that must still pass and still report"
