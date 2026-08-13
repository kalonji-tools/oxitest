from oxitest import Fixture


def test_route(target: Fixture[str]) -> None:
    assert target == "from-target", (
        "the parameter name is the fixture name, so it must select that "
        "fixture whatever else provides str (#2094)"
    )
