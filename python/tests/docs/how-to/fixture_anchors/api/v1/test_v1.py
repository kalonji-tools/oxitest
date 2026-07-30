"""A descendant package reaching up the B1 ancestor chain."""

from oxitest import Fixtures


# fmt: off
# --8<-- [start:descendant-access]
def test_v1_reaches_its_parent_package(fx: Fixtures) -> None:
    assert fx.api.tenant == "acme", "api/ is an ancestor of api/v1/, so this resolves"
    assert fx.v1.endpoint.endswith("/v1"), "the test's own package resolves too"
# --8<-- [end:descendant-access]
# fmt: on
