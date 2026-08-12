from collections.abc import Generator

import oxitest as oxi


@oxi.mark.skip(reason="skipped, and still refused — the shape is wrong either way")
def test_generator() -> Generator[None, None, None]:
    yield
