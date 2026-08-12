import oxitest as oxi
from oxitest import Yields


@oxi.fixture(lifetime="function")
def test_both() -> Yields[int]:
    """A test by name and a fixture by decorator, in the yield form (#2066)."""
    yield 1
