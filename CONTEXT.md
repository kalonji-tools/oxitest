# CONTEXT.md — oxitest Domain Glossary

## Design Principles

**Immutable by Default** — All Python-side interfaces are immutable unless explicitly proven mutable. Dataclasses are `frozen=True, slots=True`. Public attributes are read-only (`@property`, no setter). Collection accessors return immutable views (`tuple`, `MappingProxyType`). Parameters are never mutated. Classes that need mutability are listed as `&mut` exceptions in ADR-0005 — the single source of truth. Mirrors Rust's `let` vs `let mut` convention.

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

**Fixture** — A reusable value injected into test functions, or read through the `fx` proxy. Resolved primarily by type via `Fixture[T]` annotation. Sources: `@oxi.fixture` declarations in a `__fixtures__.py`, `__init__.py`, or inline in a `test_*.py`; conftest definitions; plugin providers; and builtins.

**Fixture[T]** — Type annotation that signals oxitest to inject a fixture whose binding type is `T`. Resolution: match by type first; if ambiguous, the parameter name acts as a qualifier.

**Binding Type** — The type a fixture provides, used as the primary key for resolution. For conftest fixtures, the return annotation. For plugins, `FixtureProvider.fixture_type`. For builtins, the registered `fixture_type`.

**Qualifier** — The parameter name used to disambiguate when multiple fixtures share the same binding type. Only consulted when type-based resolution yields more than one candidate.

**FixtureRef[T]** — Type annotation on a dataclass field indicating the field holds a reference to a fixture function, not a literal value. Resolved at execution time.

**Fixtures (registry)** — An instance-based registry (`fixtures = Fixtures()`) that collects fixture definitions via the `@fixtures.fixture` decorator.

**Namespace** — The qualifier in `fx.<namespace>.<name>`. Two sources: the basename of a fixture's anchor directory (`tests/api/__fixtures__.py` → `api`), or the name of a `Fixtures()` instance, which is rejected if it is a Python keyword or builtin. Directory-derived namespaces are **not unique in a tree** — `tests/api/v1/` and `tests/admin/v1/` both derive `v1` — so `fx.v1.conn` means whichever declaration is visible from the reading test, and resolution picks the deepest visible anchor.

**Anchor** — The directory a fixture is scoped to: the package holding its `__fixtures__.py`, or, for an inline declaration, the test module itself. Conftest, plugin, and builtin fixtures have no anchor.

**B1 boundary** — ADR-0009 Rule 3: a fixture is usable only by tests in its anchor package or a descendant of it. Enforced at access time on both resolution routes (`fx.` proxy and `Fixture[T]` injection), and again when a fixture resolves its own dependencies — those are governed by the fixture's anchor, not by the location of whichever test triggered resolution. On the `fx.` proxy a violation raises `BoundaryError` (diagnostic code `fixture-boundary`), which is distinct from `FixtureNotFoundError`: the fixture exists, elsewhere. The `Fixture[T]` route resolves by bare name and has no namespace segment to attribute, so it reports the invisible fixture as `FixtureNotFoundError`.

**Lifetime** — What a `@oxi.fixture` declaration writes: `"function"` (per test), `"module"` (per test module), `"package"` (per anchor package — exactly once per run, which collapses the subtree onto one worker), or `"session"` (per worker process, legal only in a rootdir package). Required keyword; there is no default. Capped by the declaration site (ADR-0009 Rule 4): inline declarations may not exceed `module`.

**Scope** — The caching vocabulary `Lifetime` translates into, via `LIFETIME_SCOPES`. Five members: `each`, `module`, `package`, `session`, plus `shared` — the legacy `Fixtures(shared=True)` tier, which no `Lifetime` maps to and which stays separate until #1720 retires the old API.

**Autouse** — A fixture that is automatically injected into every test in its scope without explicit annotation.

**Yield Fixture** — A fixture that uses `yield` to separate setup from teardown. Return type annotated `Yields[T]`.

## Conftest

**conftest.py** — A reserved filename discovered by walking from rootdir to the test file's directory. Holds fixtures and helpers. Unlike pytest — and unlike `@oxi.fixture` declarations — its fixtures are registered **run-wide**, not scoped to the containing subtree, so they are exempt from the B1 boundary. Two visibility regimes therefore run side by side until `conftest.py` support is retired (#1720); the gap is tracked as #1760.

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

## Diagnostic System

**Diagnostic** — A user-facing message emitted by the Python bridge and rendered by the Rust reporter. Carries severity (error, warning, notice), context (e.g. "fixture teardown"), message, and optional file/lineno. Frozen dataclass in `result.py`, `DiagnosticEntry` struct in `stats.rs`.

**DiagnosticSeverity** — One of `error`, `warning`, or `notice`. Controls color in the reporter summary block (red, yellow, dim) and sort order (errors first).

**emit_diagnostic()** — The single Python call site for emitting user-facing diagnostics. Appends to a `ContextVar`-based collector (`_diagnostic_collector_var`) owned by `FixtureSession`. No-op when no session is active.

**Trace** — A developer-level log event routed through Rust's `tracing` crate via the `trace()` PyO3 function (serial path) or LDJSON `{"type": "trace"}` (worker path). Gated by `RUST_LOG` environment variable.

**Wire Protocol v3** — The LDJSON protocol between workers and the Rust coordinator. Each stdout line has a `"type"` discriminator: `"result"` (test outcome), `"diagnostic"` (user-facing), or `"trace"` (developer). Missing `"type"` defaults to `"result"` for backwards compatibility.

## Execution Model

**Worker** — A subprocess (`python -m oxitest._bridge.worker`) that receives test tasks over stdin and writes results to stdout as LDJSON (wire protocol v3). Persistent within a run.

**Serial Execution** — Tests run sequentially in the main process. Used when the test count is below `min_parallel_tests` or when `--serial` is passed.

**Parallel Execution** — Tests distributed across worker subprocesses by the scheduler.

**Debug Mode** — An interactive execution mode that runs tests serially and transfers control to a debugger. `post-mortem` drops into the debugger on the first failure. `always` drops into the debugger before every test and again on failure.

**Debugger Backend** — A plugin-provided implementation of the debugger interface. Receives `trace()` and `post_mortem()` calls from the execution pipeline. The default backend wraps `pdb`.

## Auto-Arrangement

**Auto-Arrangement** — Automatic grouping of tests onto the same worker based on shared fixture dependencies. Tests that transitively depend on the same `shared=True` fixture(s) are co-located on a single worker so the fixture is created once, not per-worker.

**Connected Component** — A set of fixture names linked by transitive dependency. If fixture A depends on shared fixture B, and fixture C also depends on B, then {A, B, C} form one connected component. All tests depending on any member land on the same worker.

**Arrangement Threshold** — The percentage of parallel-eligible tests beyond which the largest connected component triggers a fallback to serial execution. Controlled via `--auto-arrange[=THRESHOLD]`.

## CLI Structure

**Subcommand** — A top-level operation that determines what oxitest does: `run` (execute tests, default), `debug` (interactive debugger), `query` (filter and print test artifacts), `inspect` (interactive TUI explorer), `env` (print environment). Each subcommand has its own flag set.

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
