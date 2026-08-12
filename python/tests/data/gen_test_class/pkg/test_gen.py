from collections.abc import Generator


class TestThing:
    """A test class holding one generator method (#2067)."""

    def test_method_generator(self) -> Generator[None, None, None]:
        """A generator test method — the class arm of the same defect."""
        yield
