# CONTEXT.md — oxitest Domain Glossary

## Core Concepts

**Test Item** — A single runnable test. Identified by a node ID. May be a standalone function or a parametrized variant of one.

**Node ID** — Unique identifier for a test item. Format: `module_path::fn_name` or `module_path::fn_name[param_id]` for parametrized tests.

**Collection** — The phase where oxitest discovers test files, imports them, and produces test items.

**Execution** — The phase where test items are run and produce outcomes.

## Parametrize

**Parametrize** — Mechanism for running one test function against multiple named input cases. Each case produces a distinct test item.

**Case** — A named set of input values for a parametrized test. The case name becomes part of the node ID (e.g. `test_add[basic]`).

**Expanded Mode** — Parametrize mode where dataclass fields are spread as individual function parameters.

**Compact Mode** — Parametrize mode where the entire dataclass instance is passed as a single function parameter. Detected when exactly one non-fixture parameter is annotated with the case type.

**Composition** — Stacking multiple `@parametrize` decorators with `partial()` values to express a cartesian product of cases across independent dimensions.

**Partial** — An incomplete set of fields for a dataclass, used in composition. Created via `oxi.partial(T, **fields)`.

**Layer** — One `@parametrize` decorator in a composition stack. Each layer provides a disjoint subset of the target dataclass's fields.

**Compound ID** — A node ID produced by composition. Joins each layer's case name with a dash, outer decorator first (e.g. `test_math[pg-add]`).

## Fixtures

**Fixture** — A reusable value injected into test functions. Declared via a `Fixtures()` registry and requested via `Fixture[T]` annotation.

**Fixture[T]** — Type annotation that signals oxitest to inject the matching fixture. Unannotated parameters are never injected.

**FixtureRef[T]** — Type annotation on a dataclass field indicating the field holds a reference to a fixture function, not a literal value. Resolved at execution time.

**Fixtures (registry)** — An instance-based registry (`fixtures = Fixtures()`) that collects fixture definitions via the `@fixtures.fixture` decorator.

**Namespace** — A `Fixtures()` instance acts as a namespace. Two registries can define fixtures with the same name without conflict.

**Scope** — The lifetime of a fixture value: `"each"` (per-test, default) or `"shared"` (per-module).

**Autouse** — A fixture that is automatically injected into every test in its scope without explicit annotation.

**Yield Fixture** — A fixture that uses `yield` to separate setup from teardown. Return type annotated `Yields[T]`.

## Marks

**Mark** — Metadata attached to a test function via `@oxi.mark.<name>`. Controls test behavior (skip, xfail, timeout) or categorization.

**skip** — Mark that unconditionally skips a test.

**xfail** — Mark that declares a test as expected to fail. An xfail test that passes is an "xpass."

**timeout** — Mark that sets a per-test deadline in seconds.

## Outcomes

**Outcome** — The result of running a test item. One of: passed, failed, error, skipped, xfailed, xpassed, timeout, warned, flaky.

**Flaky** — A test that failed on the initial run but passed on retry. Not a hard failure.

## Execution Model

**Worker** — A subprocess (`python -m oxitest._bridge.worker`) that receives test tasks over stdin and writes results to stdout. Persistent within a run.

**Serial Execution** — Tests run sequentially in the main process. Used when the test count is below `min_parallel_tests` or when `--serial` is passed.

**Parallel Execution** — Tests distributed across worker subprocesses by the scheduler.

## Strict Mode

**Strict Mode** — Enforcement of code quality rules at collection time. Configured via `strict = "warn"` or `strict = "abort"`.

**Violation** — A strict-mode rule breach detected during collection (e.g. bare assert, dict parametrize, missing mark reason).
