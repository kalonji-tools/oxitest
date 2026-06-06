# Fixture Types

!!! abstract "Reference"
    Type annotations used to declare fixture injection in test and fixture parameters.

oxitest uses type annotations to control fixture injection. There are four types,
each for a different purpose:

| Type | Purpose | Where |
|------|---------|-------|
| `Fixture[T]` | Inject a fixture into a test or another fixture | Test parameters, fixture parameters |
| `FixtureRef[T]` | Reference a fixture inside `@oxi.parametrize` kwargs | Parametrize keyword arguments only |
| `Yields[T]` | Declare the yield type of a generator fixture | Fixture return annotation |
| `Fixtures` | Inject a namespace accessor for programmatic fixture access | Test parameters |

## See also

- [Use fixtures](../../how-to/use-fixtures.md) — how-to guide with examples
- [Built-in fixtures](builtins.md) — fixtures that don't need `Fixture[T]` wrapping

## Fixture[T]

::: oxitest._bridge._fixture_type._FixtureType
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3

## FixtureRef[T]

::: oxitest._bridge._fixture_type._FixtureRefType
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3

## Yields[T]

::: oxitest._bridge._fixture_type._YieldsAlias
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3

## Fixtures

::: oxitest._bridge._fixture_session.Fixtures
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3
      members:
        - fixture
        - __getattr__
