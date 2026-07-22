# `AsyncBackend` and `AsyncSession`

!!! abstract "Reference"
    Plugin protocols for async execution backends (asyncio, trio, curio,
    etc.). oxitest ships an `asyncio` backend by default; plugins can
    register alternates.

!!! warning "Provisional API"
    `AsyncBackend` is listed as provisional in
    [API stability](../stability.md). The protocol may change with minor
    releases until it is promoted to stable. See
    [Provisional APIs](../../explanation/provisional-apis.md).

The `AsyncBackend` protocol acquires an `AsyncSession` for the lifetime of
a shared-scope async fixture or an async test body. See
[Write plugins](../../how-to/write-plugins.md) for implementation
examples and [ADR-0006](https://github.com/kalonji-tools/oxitest/blob/main/docs/adr/0006-async-organizational-strategy.md)
for the async-fixture interaction rules.

::: oxitest.AsyncBackend
    options:
      show_source: false
      heading_level: 2

::: oxitest.AsyncSession
    options:
      show_source: false
      heading_level: 2
