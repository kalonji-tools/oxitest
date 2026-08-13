from oxitest import Fixture


def test_mismatch(target: Fixture[int]) -> None:
    assert isinstance(target, int), "the run must refuse before this line is reached"
