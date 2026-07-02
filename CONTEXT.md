# CONTEXT.md — oxitest Domain Glossary

## Core Concepts

**Test Item** — A single runnable test. Identified by a node ID. May be a standalone function or a parametrized variant of one.

**Node ID** — Unique identifier for a test item. Format: `module_path::fn_name` or `module_path::ClassName::method_name` for class methods. Parametrized variants append `[param_id]` (e.g., `module_path::fn_name[case1]`, `module_path::ClassName::method_name[case1]`).

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

**Fixture** — A reusable value injected into test functions. Resolved primarily by type via `Fixture[T]` annotation. Sources: conftest definitions, plugin providers, and builtins.

**Fixture[T]** — Type annotation that signals oxitest to inject a fixture whose binding type is `T`. Resolution: match by type first; if ambiguous, the parameter name acts as a qualifier.

**Binding Type** — The type a fixture provides, used as the primary key for resolution. For conftest fixtures, the return annotation. For plugins, `FixtureProvider.fixture_type`. For builtins, the registered `fixture_type`.

**Qualifier** — The parameter name used to disambiguate when multiple fixtures share the same binding type. Only consulted when type-based resolution yields more than one candidate.

**FixtureRef[T]** — Type annotation on a dataclass field indicating the field holds a reference to a fixture function, not a literal value. Resolved at execution time.

**Fixtures (registry)** — An instance-based registry (`fixtures = Fixtures()`) that collects fixture definitions via the `@fixtures.fixture` decorator.

**Namespace** — A `Fixtures()` instance acts as a namespace. Two registries can define fixtures with the same name without conflict. Namespace names must not be Python keywords or builtins.

**Scope** — The lifetime of a fixture value. Three tiers: `"each"` (per-test, default), `"shared"` (per-module, FrozenProxy-wrapped), or `"session"` (per-process/run).

**Autouse** — A fixture that is automatically injected into every test in its scope without explicit annotation.

**Yield Fixture** — A fixture that uses `yield` to separate setup from teardown. Return type annotated `Yields[T]`.

## Conftest

**conftest.py** — A reserved filename discovered by walking from rootdir to the test file's directory. Holds fixtures and helpers scoped to that directory subtree.

**Helpers** — Callables explicitly registered via a `Helpers()` instance in conftest.py. Accessed via `from oxitest import helpers` as `helpers.<namespace>.<fn>()`. Sources: conftest definitions and plugin providers.

**Helpers (registry)** — An instance-based registry (`helpers = Helpers()`) that collects helper definitions via the `@helpers.helper` decorator.

**HelperProvider** — Plugin protocol for providing helpers. Properties: `name` (str) and `helper` (callable). Namespace derived from `provider.__module__` at registration time.

**Allow Comment** — Inline comment `# oxitest: allow[rule-name]` that authorizes behavior that would otherwise be a strict-mode violation. Used to opt in to `Fixtures()` or `Helpers()` in test modules.

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

**Debug Mode** — An interactive execution mode that runs tests serially and transfers control to a debugger. `post-mortem` drops into the debugger on the first failure. `always` drops into the debugger before every test and again on failure.

**Debugger Backend** — A plugin-provided implementation of the debugger interface. Receives `trace()` and `post_mortem()` calls from the execution pipeline. The default backend wraps `pdb`.

## Auto-Arrangement

**Auto-Arrangement** — Automatic grouping of tests onto the same worker based on shared fixture dependencies. Tests that transitively depend on the same `shared=True` fixture(s) are co-located on a single worker so the fixture is created once, not per-worker.

**Connected Component** — A set of fixture names linked by transitive dependency. If fixture A depends on shared fixture B, and fixture C also depends on B, then {A, B, C} form one connected component. All tests depending on any member land on the same worker.

**Arrangement Threshold** — The percentage of parallel-eligible tests beyond which the largest connected component triggers a fallback to serial execution. Controlled via `--auto-arrange[=THRESHOLD]`.

## CLI Structure

**Subcommand** — A top-level operation that determines what oxitest does: `run` (execute tests, default), `debug` (interactive debugger), `list` (show collected tests), `query` (filter and print test artifacts), `inspect` (interactive TUI explorer), `env` (print environment). Each subcommand has its own flag set.

**Inspect Node** — A navigable entity in the `inspect` TUI. One of six built-in kinds: Fixture, Test, Mark, Conftest, Plugin, Helper. Plugins may add extension node kinds. Each node has fields, edges to other nodes, and a detail view.

**Overview** — The cartographic landing screen of `inspect`. Shows curated sections (e.g., Fixture Gravity, Marks, Signals) that reveal the shape and hotspots of the test suite. Sections populate progressively as phase-2 data arrives.

**Node Focus** — The diagnostic screen of `inspect`. Shows a single node's properties and its followable edges, with a preview pane for the cursor-selected edge target.

**Preview** — The right pane in `inspect` that shows a summary of the cursor-selected item before navigating into it. Updates automatically as the cursor moves.

**Edge** — A typed, directed connection between two inspect nodes (e.g., "depends on", "consumer of", "defined in"). Edges are followable — selecting one navigates to the target node.

**Section** — A titled group of ranked items on the overview (e.g., Fixture Gravity, Marks, Conftests, Signals). Pluggable — plugins can contribute additional sections via `InspectSectionProvider`.

**ScopeMode** — Controls which nodes are searched in inspect's search mode. `Context` restricts candidates to the nodes visible on the current screen (e.g., edges on a Node Focus, items on the Overview). `Global` searches all nodes in the entire graph. Toggle between scopes with `Tab` while in search mode.

**Inspect Keybindings (normal mode)** — `q`/`Esc` quit; `j`/`k` or arrow keys move the cursor; `Enter`/`l`/`→` navigate into the selected item; `h`/`←`/`Backspace` navigate back; `H` opens the session history screen; `/` enters search mode; `?` toggles the help overlay; `r` triggers a manual refresh (re-runs file collection and rebuilds the graph, re-applying startup filters such as `-E`, `--affected`, and `--lf`).

**Inspect Keybindings (search mode)** — Characters append to the query; `Backspace` removes the last character; `↑`/`↓` navigate between results; `Tab` toggles `ScopeMode` between Context and Global; `Enter` accepts the search and returns to normal mode (results remain visible); `Esc` clears the search and returns to normal mode.

## Strict Mode

**Strict Mode** — Enforcement of code quality rules at collection time. Configured via `strict = "warn"` or `strict = "abort"`.

**Violation** — A strict-mode rule breach detected during collection (e.g. bare assert, dict parametrize, missing mark reason).
