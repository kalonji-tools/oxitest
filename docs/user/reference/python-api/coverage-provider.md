# `CoverageProvider`

!!! abstract "Reference"
    Plugin protocol for coverage backends. oxitest ships a
    `coverage.py`-based implementation (`CoveragePyProvider`); plugins can
    provide alternatives (e.g. Rust-side instrumentation).

Activated by the `--cov` CLI flag. See [Use coverage](../../how-to/use-coverage.md)
for end-user configuration and [Write plugins](../../how-to/write-plugins.md)
for authoring a custom provider.

::: oxitest.plugin.CoverageProvider
    options:
      show_source: false
      heading_level: 2
