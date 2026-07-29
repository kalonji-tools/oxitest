from __future__ import annotations

from oxitest import Fixtures


def test_shared_fixture_renders_its_value(fx: Fixtures) -> None:
    dsn = fx.proxy_str_shared.dsn

    rendered = f"{dsn}"

    assert rendered == "pg://db", (
        "a shared fixture interpolated into an assertion message must show the "
        f"DSN, not the wrapper — got {rendered!r}"
    )


def test_module_fixture_renders_its_value(fx: Fixtures) -> None:
    price = fx.proxy_str.price

    rendered = f"{price}"

    assert rendered == "3.14159", (
        "a module-lifetime fixture is wrapped at the same site as a shared one, "
        f"so it must render identically — got {rendered!r}"
    )


def test_module_fixture_honours_format_spec(fx: Fixtures) -> None:
    price = fx.proxy_str.price

    rendered = f"{price:.2f}"

    assert rendered == "3.14", (
        "an unforwarded format spec raises TypeError rather than misreporting, "
        f"so this is a hard break in a user's f-string — got {rendered!r}"
    )
