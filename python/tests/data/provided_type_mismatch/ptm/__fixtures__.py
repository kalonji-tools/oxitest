import oxitest as oxi


@oxi.fixture(lifetime="function")
def target() -> str:
    return "I AM A STR"


@oxi.fixture(lifetime="function")
def other1() -> int:
    return 1


@oxi.fixture(lifetime="function")
def other2() -> int:
    return 2
