import oxitest as oxi


@oxi.fixture(lifetime="function")
def test_both() -> int:
    """A test by name and a fixture by decorator, returning a value (#2066)."""
    return 1
