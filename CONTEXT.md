# CONTEXT.md — oxitest Domain Glossary

## Design Principles

**Immutable by Default** — All Python-side interfaces are immutable unless explicitly proven mutable. Dataclasses are `frozen=True, slots=True`. Public attributes are read-only (`@property`, no setter). Collection accessors return immutable views (`tuple`, `MappingProxyType`). Parameters are never mutated. Classes that need mutability are listed as `&mut` exceptions in ADR-0005 — the single source of truth. Mirrors Rust's `let` vs `let mut` convention.

**Block-Scoped Forms Belong on the Object** — An object with a lifetime boundary exposes any narrower, block-scoped form as a method or classmethod on itself (`StdCapture.disabled()`, `LogCapture.at_level()`), never as a second concept or a separate registration. A `classmethod` when the form must be reachable without injecting the fixture, an instance method when it narrows an object already in hand. Not every such object needs one — `TempDir` and `TestContext` correctly have none. Conversely, a lifetime boundary is not by itself a reason to *be* a fixture: `with` has one too, so mediation is justified only when the boundary must open before the test body, or teardown needs the test's outcome/name/config, or the boundary is wider than one test. A fourth condition — "the value is framework state" — was retracted by #1949: ambient state has no lifetime to schedule, so having nothing to construct argues *against* mediation, not for it. Framework state is read ambiently instead (`TestContext.current()`). Built-ins are partitioned and gated in `python/tests/test_builtin_shape_rule.py`; ADR-0012 is the single source of truth. Why a separate concept is not an option: ADR-0009 Amendment 5.

## Core Concepts

**Test Item** — A single runnable test. Identified by a node ID. May be a standalone function or a parametrized variant of one.

**Test Function** — The callable a Test Item runs. **A test function returns `None`.** Its return value is not part of its outcome, so a value returned is a value discarded — an assertion written as `return a == b` is evaluated and thrown away. A function containing `yield` is worse than that: calling it returns a generator and runs no part of the body, so the test reports passed having verified nothing. Refused wherever the shape becomes knowable, which is at collection for a shape a static answer can see and at execution for one only the returned value shows (ADR-0017).

**Node ID** — Unique identifier for a test item. Format: `module_path::fn_name` or `module_path::ClassName::method_name` for class methods. Parametrized variants append `[param_id]` (e.g., `module_path::fn_name[case1]`, `module_path::ClassName::method_name[case1]`).

**Target** — A path, a directory, or a node ID given as a command-line argument. A Target that names something absent is a usage error: the run is refused and nothing executes. A **glob** node-ID Target is exempt, because a glob asks to match what is present rather than asserting that it exists. A **relative** Target is resolved against the directory `oxitest` was invoked from — never against the Rootdir, which is derived from the Target and so cannot be its own reference point (ADR-0014, amended by #2026). Not to be confused with the worker's current directory, which is process-global and may be moved by `patch.chdir`.

**Collection** — The phase where oxitest discovers test files, imports them, and produces test items.

**Execution** — The phase where test items are run and produce outcomes.

## API Kinds

The public surface contains four kinds of thing. They are spelled alike — `oxitest.approx` and `oxitest.skip` are both lowercase callables four entries apart in `__all__` — so the kind is not visible at the call site, and naming them is what keeps a new addition from being filed as the wrong one.

**The discriminating question is "does this mean anything with no runner listening?"**

- **No** → **Signal**.
- **Yes**, and the framework must schedule its setup and disposal → **Fixture**.
- **Yes**, and it is read at import time, before any test runs → **Declaration**.
- **Yes**, none of the above → **Library**.

**Library** — Works unchanged in a plain script; the framework never learns it was called. `oxi.raises()`, `oxi.warns()`, `oxi.approx()`. Their modules import only stdlib. A stateless test utility is one of these, reached by `import` — there is no separate "helper" concept (#1700).

**Fixture** — A value whose lifetime the framework owns: it decides when the value is built and disposed, and the test declares only what it needs. `TempDir`, `StdCapture`, and user `@oxi.fixture` declarations. When mediation is justified at all, and what shape a narrower block-scoped form takes, are ADR-0009 and ADR-0012's — not restated here.

**Signal** — Changes the test's *outcome* rather than returning a value, and is meaningless with no runner to react. `oxi.skip()` raises `unittest.SkipTest`, which the runner maps to a skipped result; the test never catches it. Annotated `NoReturn`, because it does not come back. `oxi.importorskip()` is a **hybrid** — Library on the success path, where it returns the module, and Signal on the failure path.

**Declaration** — Read at import time, before any test runs. `mark.skip`, `mark.xfail`, and `@oxi.parametrize` **attach metadata to a test that already exists**; the `@oxi.fixture` decorator **declares a new entity**, which must be named, anchored, scoped and resolved. The two operations share a syntax and nothing else — which is why the first three reach a method of a `Test*` class and `@oxi.fixture` does not (#2068). Distinct from Signal by *when*: `mark.skip` decides before setup, `oxi.skip()` decides after setup has already begun.

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

**Fixture** — A reusable value injected into test functions, or read through the `fx` proxy. Resolved primarily by type via `Fixture[T]` annotation. Sources: `@oxi.fixture` declarations in a `__fixtures__.py`, `__init__.py`, or inline in a `test_*.py`; plugin providers; and builtins.

**Fixture[T]** — Type annotation that signals oxitest to inject a fixture whose binding type is `T`. Resolution: match by type first; if ambiguous, the parameter name acts as a qualifier.

**Binding Type** — The primary key for type-based resolution. For a declaration, the return annotation exactly as written. For plugins, `FixtureProvider.fixture_type`. For builtins, the registered `fixture_type`. **Not always what a consumer receives** — see Provided Type.

**Provided Type** — The type a consumer receives. For a Yield Fixture it is the type yielded, which the Binding Type wraps: `Yields[T]` is `Generator[T, None, None]`, so the Binding Type is the generator and the Provided Type is `T`. For every other fixture the two are identical. A Yield Fixture is therefore absent from the type index and is reachable by name — which is correct, and is why the index is not unwrapped (ADR-0002 amendment, #2094).

**Qualifier** — The parameter name used to disambiguate when multiple fixtures share the same binding type. Only consulted when type-based resolution yields more than one candidate. **It matches on the Provided Type.** Comparing Binding Types rejects the very fixture the name selects, because a Yield Fixture's Binding Type is a generator and no parameter is annotated with one. When the name matches exactly one fixture and the types still disagree, nothing is ambiguous and the run reports `FixtureTypeMismatchError` instead.

**FixtureRef[T]** — Type annotation on a dataclass field indicating the field holds a reference to a fixture function, not a literal value. Resolved at execution time.

**Fixtures (annotation)** — The bare `fx: Fixtures` parameter annotation, which injects the namespace accessor. Injection matches it by identity. Calling it raises: it was an instance-based registry until #1720, and ADR-0009 Rule 5 reuses the name rather than freeing it.

**Namespace** — The qualifier in `fx.<namespace>.<name>`. The basename of a fixture's anchor directory (`tests/api/__fixtures__.py` → `api`), the stem of a test module for an inline declaration (`test_inline.py` → `test_inline`), or a plugin's module path. There is no decorator override (ADR-0009 Amendment 16). Directory-derived namespaces are **not unique in a tree** — `tests/api/v1/` and `tests/admin/v1/` both derive `v1` — so `fx.v1.conn` means whichever declaration is visible from the reading test, and resolution picks the deepest visible anchor.

**Reachable namespace** — One that can be *written* as `fx.<namespace>`: `namespace.isidentifier() and not keyword.iskeyword(namespace)`. Because the name is derived rather than declared, it can fail this — a `class/` directory yields a `SyntaxError` and `integration-tests/` yields an expression that parses as subtraction, so the access never reaches oxitest. Builtins and soft keywords (`int`, `match`, `type`) are reachable and legal. A derived namespace that is unreachable **warns**; one written by hand in `plugin_settings` is **refused**. Shortcut access (`fx.<name>`) is unaffected either way, which is why the derived case is not fatal.

**Anchor** — The directory a fixture is scoped to: the package holding its `__fixtures__.py`, or, for an inline declaration, the test module itself. Plugin, framework, and builtin fixtures have no anchor.

**B1 boundary** — ADR-0009 Rule 3: a fixture is usable only by tests in its anchor package or a descendant of it. Enforced by **two gates** (ADR-0009 Rule 3, Amendment 14). A literal `fx.<namespace>.<name>` or `fx.<name>` written in a test body is read out by prescan and refused at **collection time**, before any test runs — which is what stops a violation hiding inside a skipped test, an `xfail`, or a branch never taken. Everything else is refused at **access time** on both resolution routes (`fx.` proxy and `Fixture[T]` injection), and again when a fixture resolves its own dependencies — those are governed by the fixture's anchor, not by the location of whichever test triggered resolution. Access-time enforcement is permanent, not a fallback: `getattr(fx, name)` defeats any static gate. The two deliberately disagree on unreachable code, which the static gate refuses and the access-time gate never sees. On the `fx.` proxy a violation raises `BoundaryError` (diagnostic code `fixture-boundary`), which is distinct from `FixtureNotFoundError`: the fixture exists, elsewhere. The `Fixture[T]` route resolves by bare name and has no namespace segment to attribute, so it reports the invisible fixture as `FixtureNotFoundError`. A violation sets the run's exit code to `UsageError` (4) wherever it is caught. Caught at access time it does not stop the run — every test still reports (#1761); caught at collection it refuses the run outright, because no test has started. Both error types vote, because both are the same violation seen from different routes.

**Rootdir** — The directory holding `pyproject.toml`, found by walking up from the first Target (or from the invocation directory when there is none). It is placed on `sys.path`, and it is where the timing cache is written. Always absolute, never empty. **Derived** from the Target, which is why a relative Target is not resolved against it (#2026).

**Synthesized module name** — The `_oxitest_<route>_<digest>` string a loaded module is registered under in `sys.modules`. One per load route (`collect`, `exec`, `doctest`), all derived from the module path alone, so two routes never disagree about which file a name refers to. Never a dotted path. The `_oxitest_` prefix is *not* what marks a module as oxitest's own — see **Dotted module name**.

**Dotted module name** — A loaded module's `__name__`, and the `__package__` derived from it. Equal to the module's path relative to the **Rootdir**, but only when that name resolves back through the current `sys.path` to that same file; otherwise the module carries its Synthesized module name instead and relative imports from it do not resolve (#1680). The check performs no import: it resolves the first segment against `sys.path` by hand and compares with `os.path.samestat`, because asking `importlib` would execute the parent package's `__init__.py`. A module with a Dotted module name is registered under **both** names, because the standard library resolves a class's module through `sys.modules[cls.__module__]` and dereferences it without a guard. Modules oxitest built are recognised by an origin marker rather than by their key's spelling.

**Rootdir package** — The deepest directory containing every path the project declares in `testpaths` that holds test files; when nothing is declared, the deepest directory containing every test oxitest finds by walking from the project root. The only site where `lifetime="process"` is legal (ADR-0009 Rule 4). **Not** the same as the **Rootdir** above — the two coincide only when a project declares nothing and its tests sit at the root. Because it is derived, adding `testpaths` can move it and invalidate a `process` declaration that was previously legal, so the declaration error names which derivation produced it.

**Lifetime** — What a `@oxi.fixture` declaration writes: `"function"` (per test), `"module"` (per test module), `"package"` (per anchor package — exactly once per run, which collapses the subtree onto one worker), or `"process"` (per worker process, so as many instances as `-n`; legal only in a rootdir package). Renamed from `"session"` by #1777, which is no longer accepted. Required keyword; there is no default. Capped by the declaration site (ADR-0009 Rule 4): inline declarations may not exceed `module`, enforced during registration rather than by the static prescan (#1859).

**Scope** — The caching vocabulary `Lifetime` translates into, via `LIFETIME_SCOPES`. Five members: `each`, `module`, `package`, `process`, `session`. `session` is the one tier no `Lifetime` maps to — it holds the builtins and drains at the task boundary. The legacy `shared` tier collapsed into it in #1720; they always shared a rate.

**Autouse** — A fixture that runs for every test in its B1 boundary without being requested, for its side effects; the value is discarded unless the test also requests it, in which case both routes share one instance. How often it runs follows its Lifetime, and where several apply they run widest-Lifetime-first. **Applies** and **fires** are distinct: a fixture applies to every test in its boundary, but it is built once per boundary, inside whichever test reaches it first — so which test pays depends on execution order, worker assignment and deselection. `inspect` shows the set that applies to a test, and cannot show which test builds one.

**Yield Fixture** — A fixture that uses `yield` to separate setup from teardown. Return type annotated `Yields[T]`.

**Setup Completed** — Said of a yield fixture whose body has reached its `yield`, as distinct from one whose body merely *began*. Only a fixture whose setup completed is torn down: one interrupted before its `yield` has no post-`yield` half to run, and one that was never started would have its setup executed by the attempt. The distinction is load-bearing because a teardown is registered *before* the body is allowed to run — otherwise an interrupt arriving between the two would strand a set-up fixture with nothing to dispose it (ADR-0009 Amendment 11). The cost is deliberate and matches `contextlib.contextmanager`: a resource acquired before the `yield` and interrupted leaks, and a fixture that cannot accept that writes its own `try`/`finally` inside the body.

## Marks

**Mark** — Metadata attached to a test function via `@oxi.mark.<name>`. Controls test behavior (skip, xfail, timeout) or categorization.

**skip** — Mark that unconditionally skips a test.

**xfail** — Mark that declares a test as expected to fail. An xfail test that passes is an "xpass."

**Deadline** — The time limit one test runs under, in seconds. Declared by the **timeout** mark, or ambiently for every test by the `timeout` key in `[tool.oxitest]`; a mark wins over the ambient value. Enforced in the worker, in the process that runs the test. A Deadline bounds the **call of the test function**, and nothing on either side of it: Fixture setup and Fixture teardown are outside it, for a sync test and for an async one alike, so a Fixture that never completes is stopped by the **watchdog** and not by the Deadline. Exactly one timer enforces a Deadline for one test. Where more than one Deadline is live at once — a test that runs another test — the effective Deadline is the **shortest** of them, so nesting never extends a Deadline that is already running (ADR-0016). Distinct from the **watchdog**. On Unix a Deadline is delivered by a process-global timer that oxitest does not own exclusively: other code that writes that timer voids the Deadline, and the test is then reported as **warned** rather than counted as a pass it did not earn.

**timeout** — The Mark that declares a **Deadline** on one test, `@oxi.mark.timeout(seconds=N)`. Exactly one is allowed per test; a second is refused where it is written. The `timeout` config key declares the same Deadline for every test without a Mark.

**watchdog** — The coordinator's per-result silence budget for a worker. A worker that emits nothing for the budget is killed and every test in flight on it is reported as an error. Infrastructure, not a user-facing deadline: no test declares it, and exceeding it says nothing about how long any individual test ran.

## Outcomes

**Outcome** — The result of running a test item. One of: passed, failed, error, skipped, xfailed, xpassed, timeout, warned, flaky.

**Flaky** — A test that failed on the initial run but passed on retry. Not a hard failure.

**Usage Error** — One concept in two languages. `ExitCode::UsageError` is exit code 4; `oxitest._bridge._errors.UsageError` is the exception class. Both mean the request itself was invalid. The exit code is fixed by the **class** of the error and never by the transition that caught it, so a startup failure keeps the class it was raised with rather than reporting as a collection error (ADR-0014, #2172). `is_usage_error` is the single source of truth for the vote, and both the Rust startup funnels and the Python execution funnels ask it. A broken oxitest invariant is an **Internal Error**, which is not a usage error: it means oxitest failed to hold a property of its own, so telling the user their request was invalid would name the wrong culprit.

## Diagnostic System

**Diagnostic** — A user-facing message emitted by the Python bridge and rendered by the Rust reporter. Carries severity (error, warning, notice), context (e.g. "fixture teardown"), message, and optional file/lineno. Frozen dataclass in `result.py`, `DiagnosticEntry` struct in `stats.rs`. Every path a Diagnostic prints is shown against the **Rootdir**, and the run announces the Rootdir once at its start so a reader can resolve one (#1851). The base is never the worker's current directory, which is process-global and may be moved by `patch.chdir`. A path outside the Rootdir stays absolute, and `<plugin:…>` and `<builtin>` are origin labels rather than paths. Paths stay canonical **internally**: the relative form is display only.

**DiagnosticSeverity** — One of `error`, `warning`, or `notice`. Controls color in the reporter summary block (red, yellow, dim) and sort order (errors first).

**emit_diagnostic()** — The single Python call site for emitting user-facing diagnostics. Appends to a `ContextVar`-based collector (`_diagnostic_collector_var`) owned by `FixtureSession`. No-op when no session is active.

**Trace** — A developer-level log event routed through Rust's `tracing` crate via the `trace()` PyO3 function (serial path) or LDJSON `{"type": "trace"}` (worker path). Gated by `RUST_LOG` environment variable.

**Wire Protocol** — The LDJSON protocol between workers and the Rust coordinator. Each stdout line has a `"type"` discriminator: `"result"` (test outcome), `"diagnostic"` (user-facing), or `"trace"` (developer). `"type"` is required, and it is the protocol's membership test: a line without it is a **Non-protocol line** (#2143). **This glossary holds no version number for it.** `PROTOCOL_VERSION` in `src/worker_result/wire.rs` declares the version, `python/oxitest/_bridge/result.py` mirrors it, and `scripts/check_bridge_sync.py` refuses a wire-shape change that leaves the two out of step. A number written here would be a third copy that no gate reads — which is what this entry and the **Worker** entry below each became, disagreeing with the constant and with each other (#2109).

**Non-protocol line** — A line on a worker's stdout that does not carry the `"type"` discriminator, so it is not the worker answering: a test, a C extension, or an uncaptured child process writing to fd 1, which is the same pipe. It is logged and dropped, it does **not** count toward the results the coordinator expects, and it does **not** reset the **watchdog** (#2010, #2143). A line that carries `"type": "result"` and is not a valid result is protocol traffic, and still counts. `docs/internals/src/worker-protocol.md` owns the full table.

## Execution Model

**Worker** — A subprocess (`python -m oxitest._bridge.worker`) that receives test tasks over stdin and writes results to stdout as LDJSON, on the **Wire Protocol**. Persistent within a run. **Runs one Test Item at a time**: `run_task` iterates its items in a plain loop, so process-global state mutated by one test is never observed by a simultaneous one — only inherited by later ones. `Patcher`'s four surfaces and the CWD-liveness guard both rest on this. The reporter's `worker #N | concurrent: …` line counts node IDs in flight *across all workers*, including queued ones, and is not intra-Worker concurrency.

**Serial Execution** — Tests run sequentially in the main process. Used when the test count is below `min_parallel_tests` or when `--serial` is passed.

**Parallel Execution** — Tests distributed across worker subprocesses by the scheduler.

**Debug Mode** — An interactive execution mode that runs tests serially and transfers control to a debugger. `post-mortem` drops into the debugger on the first failure. `always` drops into the debugger before every test and again on failure.

**Debugger Backend** — A plugin-provided implementation of the debugger interface. Receives `trace()` and `post_mortem()` calls from the execution pipeline. The default backend wraps `pdb`.

## Arrangement

**Arrangement** — Grouping tests onto the main process because they asked to be grouped. A test names one or more fixtures in `@oxi.arrange`, and every test in the Connected Component of a named fixture is co-located. Nothing is derived from a fixture's lifetime: #1848 retired an inference that read `lifetime="module"` (and `shared=True` before #1720), because at that tier co-location cannot reduce a build — the tier rebuilds per module and a module is the scheduling unit.

**Arrangement Input** — A fixture designated by an `@oxi.arrange` on a collected test. Membership is a declaration, not a property of the fixture, so a fixture at any lifetime can be one.

**Arrangement Spelling** — How an Arrangement Input is written: its **name**, as a string, or its **type**, as an `@injectable` class. Both denote the same fixture and schedule the same way. The distinction is only in what the framework must resolve — a type reaches its fixture through the binding type, because a builtin is registered under its private implementation class and never under the public type name.

**Declaring Module** — A test module that can resolve a `lifetime="module"` fixture. It is never split across two dispatch phases, because each phase owns its own fixture session and a split would build the fixture once in each. The test is visibility rather than use: a module qualifies if the fixture is visible to it, whether or not any test in it reaches the fixture.

**Connected Component** — A set of fixture names linked by transitive dependency. If fixture A depends on an Arrangement Input B, and fixture C also depends on B, then {A, B, C} form one connected component. All tests depending on any member land on the same process.

## CLI Structure

**Subcommand** — A top-level operation that determines what oxitest does: `run` (execute tests, default), `debug` (interactive debugger), `query` (filter and print test artifacts), `inspect` (interactive TUI explorer), `env` (print environment). Each subcommand has its own flag set.

**Inspect Node** — A navigable entity in the `inspect` TUI. One of five built-in kinds: Fixture, Test, Mark, Declaration, Plugin. Plugins may add extension node kinds. Each node has fields, edges to other nodes, and a detail view.

**Overview** — The cartographic landing screen of `inspect`. Shows curated sections (e.g., Fixture Gravity, Marks, Signals) that reveal the shape and hotspots of the test suite. Sections populate progressively as phase-2 data arrives.

**Node Focus** — The diagnostic screen of `inspect`. Shows a single node's properties and its followable edges, with a preview pane for the cursor-selected edge target.

**Preview** — The right pane in `inspect` that shows a summary of the cursor-selected item before navigating into it. Updates automatically as the cursor moves.

**Edge** — A typed, directed connection between two inspect nodes (e.g., "depends on", "consumer of", "defined in"). Edges are followable — selecting one navigates to the target node.

**Section** — A titled group of ranked items on the overview (e.g., Fixture Gravity, Marks, Declarations, Signals). Pluggable — plugins can contribute additional sections via `InspectSectionProvider`.

**ScopeMode** — Controls which nodes are searched in inspect's search mode. `Context` restricts candidates to the nodes visible on the current screen (e.g., edges on a Node Focus, items on the Overview). `Global` searches all nodes in the entire graph. Toggle between scopes with `Tab` while in search mode.

**Inspect Keybindings (normal mode)** — `q`/`Esc` quit; `j`/`k` or arrow keys move the cursor; `Enter`/`l`/`→` navigate into the selected item; `h`/`←`/`Backspace` navigate back; `H` opens the session history screen; `/` enters search mode; `?` toggles the help overlay; `r` triggers a manual refresh (re-runs file collection and rebuilds the graph, re-applying startup filters such as `-E`, `--affected`, and `--lf`); `s` opens the in-TUI source view for the focused node; `e` opens it in `$EDITOR`.

**Inspect Keybindings (search mode)** — Characters append to the query; `Backspace` removes the last character; `↑`/`↓` navigate between results; `Tab` toggles `ScopeMode` between Context and Global; `Enter` accepts the search and returns to normal mode (results remain visible); `Esc` clears the search and returns to normal mode.

## Strict Mode

**Strict Mode** — Enforcement of code quality rules at collection time. Configured via `strict = "enforce"` or `strict = "abort"`; absent or `"off"` disables it. There is no `"warn"` position — `"enforce"` is the warn-only one, and it never escalates a diagnostic to an error.

**Violation** — A strict-mode rule breach detected during collection (e.g. bare assert, dict parametrize, missing mark reason).

## Doctest Coverage

**Coverage Subject** — A definition (module, function, class, or method) eligible for doctest auditing. Three rules decide the set, and they do not agree. A **leading-underscore leaf name** is never a subject, under either scope form, because it is dropped when subjects are enumerated. A **private module path** (`_internal/`) is not a subject under the scalar `public`, but is under any list-form entry, which switches that filter off for the files it covers. An **`__all__` declaration is authoritative** where present: each entry is a subject, overriding both rules above.

**Doctest Root** — A source tree whose public API the coverage audit covers, declared rather than derived. Selects *files*; a Doctest Scope and Doctest Skip select subjects within them. Empty means the audit covers the Declared Test Tree, which is what `testpaths` names — a different question, and conflating the two is what the term exists to stop. _Avoid_: Rootdir, Rootdir package (both taken, and neither is this).

**Scope Entry** — One element of the doctest scope/skip grammar. Four forms: `Prefix` (`dir/`), `File` (`f.py`), `Symbol` (`f.py::name`), `Member` (`f.py::Cls::name`).

**Doctest Scope** — Which coverage subjects are audited: every public one, or exactly those named by a list of scope entries. _Avoid_: Scope (in this repo that names the fixture caching vocabulary).

**Doctest Skip** — Coverage subjects subtracted from the audited set. _Avoid_: skip (in this repo that names the mark).

**Stale Entry** — A scope or skip entry that cannot match any coverage subject under any invocation. A property of the entry and the project, never of a particular run.

**Declared Test Tree** — The test surface a project declares in its configuration.

**Effective Run Set** — The paths a single invocation actually walks.

## Test Bands

This section is about oxitest's own tests, not a user's. ADR-0019 is the authority.

**Band** — A class of test. The classifying axis is how much of the system is live. A test belongs to exactly one band, and the unit is the test, not the file. _Avoid_: tier (in this glossary that names a **Lifetime** and its **Scope**; elsewhere in this repository it names a benchmark size class, an enforcement mechanism, and a pipeline phase).

**Crate band** — The test starts no Python.

**Library band** — The test starts no product process. Both languages are live in one process, because `import oxitest` loads `oxitest._oxitest`.

**Command band** — The test starts a product process: the CLI or the **Worker**.

**Distribution band** — The test installs the wheel and imports it from outside the source tree.

**Band record** — The committed derivation of which band each test belongs to, at `scripts/band_record.tsv`. Keyed on a test. `just check` refuses when the tree and the record disagree. Distinct from the obligation record, which keys on a region of product code.

**Attribute** — A property of a test that names its subject rather than its liveness. Cuts across the bands, so it is not a partition. Three exist: `documentation`, `regression`, `tooling`.

**Specimen** — A test-shaped function that a band test writes into a project as input. No band collects a Specimen.

**Performance Gate** — The instrument that refuses a change on a measured regression. Not a band: a benchmark run is not a test, so the liveness axis does not reach it.

**Release Performance Report** — The instrument that describes a release and refuses nothing. Not a band, for the same reason.

**Baseline** — The measurement an instrument compares against. For the **Performance Gate** it is the merge-base, built in the same job. The **Release Performance Report** has none.

**Calibration run** — One revision measured twice in one job, which states the noise floor as a measurement instead of an assumption.
