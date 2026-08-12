from collections.abc import Iterator


async def test_returns_a_generator() -> Iterator[int]:
    """An ordinary coroutine whose return value happens to be a generator.

    `iscoroutinefunction` is True, so this runs on the async path. The awaited
    value is a generator, which is what the async runtime guard classifies.
    """
    return (index for index in range(3))


async def test_normal() -> None:
    value = 1
    assert value == 1, "an ordinary async test that must still pass and report"
