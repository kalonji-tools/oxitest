import oxitest as oxi


class TestThing:
    """A test class declaring a fixture on a method (#2068)."""

    @oxi.fixture(lifetime="function")
    def conn(self) -> int:
        """A declaration that no walk can see, so nothing registers it."""
        return 7

    def test_uses(self, conn: oxi.Fixture[int]) -> None:
        """Reports `fixture 'conn' not found` before the refusal existed."""
        assert conn == 7, "a class-method fixture resolved for a sibling method"
