# Use parametrize

!!! abstract "How-to"
    Run one test function against multiple named input cases.

## Choose a parametrize mode

oxitest supports three modes for parametrize cases. Dataclass mode is recommended.

=== "Dataclass mode (recommended)"
    Define a frozen dataclass for your case type, then pass keyword arguments to
    `@oxitest.parametrize`. Each kwarg is a named test case; the key becomes part of
    the test ID (e.g. `test_add[basic]`).

    ```python
    --8<-- "python/tests/docs/how-to/test_parametrize.py:dataclass-expanded"
    ```

    oxitest infers **expanded mode** from the signature: parameters whose names match
    dataclass fields receive the field values directly.

=== "Compact mode"
    Annotate a single parameter with the dataclass type to receive the whole instance:

    ```python
    --8<-- "python/tests/docs/how-to/test_parametrize.py:compact-mode"
    ```

    oxitest detects compact mode when exactly one non-`Fixture[T]` parameter carries
    the case type annotation.

=== "Dict mode"
    Use plain dicts instead of a dataclass. All dicts must have the same keys, and the
    keys must match the non-fixture parameters of the test function:

    ```python
    --8<-- "python/tests/docs/how-to/test_parametrize.py:dict-mode"
    ```

!!! tip "Which mode should I use?"
    Start with **Dataclass mode** — it gives you named fields, type safety, and IDE completion. Use **Compact mode** when you have a single parameter with simple values. Use **Dict mode** only for backward compatibility.

!!! info "How mode detection works"
    oxitest examines the test function's type annotations to choose a mode:

    1. **Compact mode**: exactly one non-`Fixture[T]` parameter is annotated
       with the dataclass type used in the cases. That parameter receives the
       whole dataclass instance.
    2. **Expanded mode** (default): no parameter carries the case dataclass
       type. Parameters whose names match dataclass fields receive the
       corresponding field values.

    **Common errors:**

    - *"compact parametrize: multiple parameters annotated with 'Case'"* —
      two or more parameters have the same case type annotation. Use at most
      one, or switch to expanded mode by removing the type annotation.
    - *Fields not injected in expanded mode* — parameter names must exactly
      match dataclass field names. Check for typos.

## Inject fixtures into parametrize cases

A dataclass field annotated `FixtureRef[T]` tells oxitest to inject a
[fixture](use-fixtures.md) for that case. Pass a fixture function as the field value:

```python
--8<-- "python/tests/docs/how-to/test_parametrize.py:fixture-ref"
```

The fixture is resolved at run time with the same scope/teardown rules as any other fixture.

## Compose parametrize layers

When a test has multiple independent dimensions (e.g. database backends × operations),
writing every combination by hand is tedious. Stack multiple `@oxitest.parametrize`
decorators with `oxitest.partial()` to express the cartesian product:

```python
--8<-- "python/tests/docs/how-to/test_parametrize.py:composed"
```

This produces 4 test variants: `test_query[real-users]`, `test_query[real-empty]`,
`test_query[mock-users]`, `test_query[mock-empty]`.

### How it works

1. Each `@oxitest.parametrize` layer provides a **subset** of the dataclass fields
   via `partial()`.
2. At collection time, oxitest takes the cartesian product of all layers and merges
   the partial fields into a complete instance.
3. The test function receives the same kwargs as a regular parametrized test — the
   signature does not change.

### Rules

- All `partial()` calls across layers must target the **same dataclass type**.
- Each layer must provide **disjoint fields** — no field may appear in two layers.
- The **union** of all layers must cover every field on the dataclass.
- Composition requires **at least 2** stacked `@parametrize` layers. A single layer
  with `partial()` values is an error — use a full dataclass instance instead.
- The source dataclass does **not** need to be frozen. Only full (non-composed)
  parametrize cases require `@dataclass(frozen=True)`.

!!! note
    Compact mode (single `case: QueryCase` parameter) is **not** compatible with
    `FixtureRef` fields. When a case type has `FixtureRef` fields, use expanded
    mode — annotate individual parameters in the test function signature.

### Test IDs

Composed test IDs join each layer's case name with a dash, outer decorator first:

```text
test_query[real-users]
test_query[real-empty]
test_query[mock-users]
test_query[mock-empty]
```

Filter with `-E` as usual:

```console
$ oxitest -E 'name(real)'        # all real-db variants
$ oxitest -E 'name(real-users)'  # one specific combination
```

## Understand test IDs

oxitest names parametrized variants using the keyword argument key:

```text
test_add[basic]
test_add[negative]
test_add[zero]
```

Use `-E` to run a specific variant:

```console
$ oxitest -E 'name(test_add[basic])'
```

## See also

- [Use fixtures](use-fixtures.md) — fixture injection and `FixtureRef[T]` for parametrize cases
- [Filter tests](filter-tests.md) — run a specific parametrized variant with `-E`
- [Strict mode](../explanation/strict-mode.md) — why strict mode requires dataclasses over dicts
