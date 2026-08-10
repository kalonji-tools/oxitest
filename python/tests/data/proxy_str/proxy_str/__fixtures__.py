from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="module")
def price() -> float:
    return 3.14159


@oxi.fixture(lifetime="package")
def dsn() -> str:
    """Was a ``Fixtures(name="proxy_str_shared")`` registrar in a conftest.py.

    The namespace came from that ``name=``; it is the anchor directory's
    basename now (ADR-0009 Rule 5), so the accessor is ``fx.proxy_str.dsn``.
    """
    return "pg://db"
