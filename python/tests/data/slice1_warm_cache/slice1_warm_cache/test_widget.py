from __future__ import annotations

from oxitest import Fixtures


def test_widget_resolves(fx: Fixtures) -> None:
    widget = fx.slice1_warm_cache.widget
    assert widget is not None, (
        "fixture must resolve through ModuleSource path on every run, "
        "including warm-cache runs where the test module is cached"
    )
