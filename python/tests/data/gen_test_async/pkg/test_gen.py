from collections.abc import AsyncGenerator


async def test_async_generator() -> AsyncGenerator[None, None]:
    yield
