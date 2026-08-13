import oxitest as oxi
from oxitest import Yields


@oxi.fixture(lifetime="function")
def target() -> Yields[str]:
    yield "from-target"


@oxi.fixture(lifetime="function")
def competitor1() -> str:
    return "competitor1"


@oxi.fixture(lifetime="function")
def competitor2() -> str:
    return "competitor2"
