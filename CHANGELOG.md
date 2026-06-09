# Changelog

All notable changes to this project will be documented in this file.
## [1.0.0-alpha.1] - 2026-06-09

### Bug Fixes


- Address code review feedback

### Features


- Add shell completion generation via hidden `completions` subcommand (#786)
- Add realistic benchmark tier and dogfood benchmarks (#831)
- Add body weight computation to PrescanItem
- Carry AST weight sum through pipeline
- Blend AST fallback into estimated_duration for cold cache
- Enforce BUILTIN_MARKERS cross-language sync with integration test (#841)

### Performance


- Add lazy collection benchmarks and document results (#825)
- Pre-warm parallel workers to hide startup latency (#832)

## [0.14.0] - 2026-06-08

### Features


- Extract per-item metadata from AST prescan (#805, #808)
- Add prescan-level filtering for lazy collection (#806)
- Add conftests_for_modules for ancestor-chain filtering (#807)
- Add Prescanned and MetadataFiltered typestate states (#806)
- Add lazy_skipped field to FileProfile and show lazy/eager split in profile (#809)
- Add lazy plugin module import (#810)

## [0.13.0] - 2026-06-08

### Bug Fixes


- Add query/bridge.rs and reporter/bridge.rs to codecov ignore
- Resolve --affected with relative subdirectory paths
- --affected works in git worktrees (#778)
- Strip GIT_* env vars in gitignore integration tests (#814)
- Propagate all class-level marks to test methods (#818)
- Oxi_mark skip(when=False) treated as no-op, not violation (#819)

### Features


- Parallel failure context — worker ID and concurrent tests (#631)
- Add --version / -V flag to CLI (#784)
- Add CovReportFormat enum and CoverageProvider protocol (#803)
- Add --cov and --cov-report CLI flags (#803)
- Doctest collection and execution support
- Dogfood doctest support on oxitest public API

## [0.12.0] - 2026-06-06

### Bug Fixes


- Update docs reference for _TestContext after module move
- Add InvalidModuleMark to Rust ViolationKind enum (#721)
- Resolve rebase conflicts — migrate new make_item_raw call sites
- Migrate make_session_with to make_fixture_def #725
- Migrate test_executor.py FixtureDef calls to make_fixture_def #725
- Migrate test_proxy_ns.py FixtureDef calls to make_fixture_def #725
- Migrate remaining FixtureDef calls to make_fixture_def
- Include class name in node IDs for class-based tests (#737, #720)
- Redirect _fixture_instantiator.py import missed during rebase
- Make coverage upload best-effort and avoid redundant test run (#745)
- Surface invalid glob errors, test bracket escaping (#736)
- Use force_styling(true) to avoid global console race (#687)

### Features


- Track fixture setup and teardown timing on FixtureSession (#622)
- Add fixture timing to reporter and bridge (#622)
- Enrich TestItemBuilder with .arc() and default lineno=1
- Add #[cfg(test)] fluent setters on PipelineContext
- Add _scaffold_plugin_project() helper #724
- Add TestOutcome builder methods for Failed/Error (#726)
- Canonicalize rootdir on load for node ID consistency (#720)
- Partition positional args into paths and node IDs (#720)
- Add node ID prefix matching in FilterPhase (#720)
- Enable multi-select with resource headers (#720)
- Add TestRunContext ContextVar for per-test transient state (#715)
- Add ScopeRefs dataclass and _scope_for callback (#715)
- Add FixtureInstantiator class with resolution + creation chain (#715)
- Add glob matching in filter_by_node_ids (#736)
- Skip path extraction for glob node IDs (#736)
- Prepend rootdir for glob node IDs, skip canonicalize (#736)
- Add card chrome colors to inspect output (#687)
- Add AST-driven Python source highlighter (#687)
- Integrate source highlighting into inspect cards (#687)
- Add --color flag to query subcommand (#687)

## [0.11.0] - 2026-06-02

### Bug Fixes


- Use seed_data fixture and correct parametrize signatures

### Features


- Add Rust assert rewriter module
- Wire Python to Rust and delete Python rewriter
- Add test counting and parametrize case detection to prescan
- Add --count flag for instant prescan-only test counting
- Add --collection-profile for per-file timing breakdown
- `oxitest plugins` subcommand (#625)
- Scheduling transparency with -v (#628)
- Dataclass field diffs in assertion output (#623)
- Fixture cache hit rate reporting (#630)
- DSL engine — lexer, parser, evaluator, resource types
- Instant-tier AST extraction for tests, marks, helpers
- Output formatters — columnar, tab, jsonl, inspect card
- Add Query command, remove list/fixtures/plugins subcommands
- Full-tier Python bridge for fixture and plugin queries
- Fzf interactive fuzzy finder for queries
- Respect .gitignore in file discovery

## [0.10.0] - 2026-05-31

### Bug Fixes


- Make sep_width deterministic in test builds
- Suppress debug/trace banner output leaking to terminal (#605)
- Normalize wall-clock times in JUnit snapshot tests
- Use sys.executable instead of PYO3_PYTHON for workers

### Features


- Render collection-level diffs for Python assertion failures
- Add fixref_names to CollectedItem and TestItem
- Add edit_distance module for fixture name suggestions
- Add FixtureSession.validate_fixture_names() with tests
- Add FixtureValidationPhase with bridge validation and suggestions
- Add MissingReturnAnnotation strict violation (#621)
- Add FixtureShadowWarning at registration (#617)
- Fixture teardown error attribution (#618)
- Unused fixture detection as strict violation (#616)
- Add tree_fixtures_from_registry() with TDD
- Add --tree flag as standalone action mode
- Add TreePhase and tree_fixtures bridge method
- Replace flake.nix with devenv.nix
- Replace Python import graph with pure Rust implementation
- Move bare-assert detection from Python to Rust
- Add Rust pre-scan to skip test files with no test functions
- Show parametrize case count in collection summary (#627)

## [0.9.0] - 2026-05-29

### Bug Fixes


- Update Python tests for Frame.locals field and refresh Cargo.lock
- Expect tuple not list for Frame.locals in wire test
- Revert Cargo.lock to match main (bitflags 2.11.1)
- Add clippy type_complexity allow on partition_inprocess_groups
- Revert __all__ export and fix docstring wording
- Use ty suppression syntax instead of mypy
- Resolve ty type-checker errors

### Features


- Add ExitCode IntEnum on Python side
- Add ApproxBase, ApproxScalar, ApproxSequence, ApproxMapping
- Export approx and ApproxBase from public API
- Add KeepTmpMode enum and --keep-tmp CLI flag (#550)
- Thread keep_tmp through Rust bridge to Python worker (#550)
- Conditional teardown based on --keep-tmp mode (#550)
- Replace verbose bool with Verbosity enum
- Dual verbose syntax with -v/-vv count and --verbose=LEVEL
- Add conflict validation for action modes and quiet
- Add fixture_names to CollectedItem and TestItem
- Implement three-level --list output (Normal/Detailed/Full)
- Add --show-locals and --show-internals flags
- Add locals field to Frame for --show-locals
- Rewrite diagnostic renderer for new box format
- Rewrite _get_frames with filtering and locals capture
- Thread show_locals/show_internals through executor and worker
- Thread show_locals/show_internals through Rust pipeline
- Add inprocess to BUILTIN_MARKERS
- Add INVALID_MODULE_MARK violation kind
- Implement _extract_module_marks()
- Implement _apply_module_marks()
- Integrate oxi_mark into collect_module()
- Add auto-arrangement terms to CONTEXT.md
- Add shared_fixture_groups() for connected component analysis (#596)
- Add --auto-arrange / --no-auto-arrange config (#596)
- Bridge shared_fixture_groups() to Rust Session trait (#596)
- Auto-arrange tests by shared fixtures in execute() (#596)

### Performance


- Derive Copy on OutcomeKind, add Borrow<str> for NodeId
- Short-circuit empty frames in to_wire()
- Dedup conftest dirs in affected test filtering
- Use vars().items() to avoid double getattr lookups
- Use NodeId as item_lookup key, Arc<str> for python_bin
- Use HashSet<&str> in cache invalidate to avoid allocations
- Return Cow from truncate_name, take &str in fmt_line
- Use write! macro in diagnostic rendering

## [0.8.0] - 2026-05-28

### Features


- Extend TestContext with test identity metadata (#542)
- Add Cli::validate() with flag conflict checks
- Wire validate() into setup, simplify merge_cli
- Add --debug post-mortem debugging (Rust)
- Add pdb post-mortem integration with debug helpers
- Add --debug=always with _suspend_and_trace and _print_trace_banner
- Add DebuggerBackend plugin protocol
- Add namespace validation for keywords/builtins
- Add HelperNamespace and wire into conftest_loader
- Migrate test suite to conftest helpers namespace

## [0.7.0] - 2026-05-27

### Bug Fixes


- Collect Python coverage from worker subprocesses
- Resolve clippy lints, benchmark pytest, and release Cargo.lock

### Features


- Add oxi.partial() for parametrize composition
- Tuple-based param_cases and _PartialCases type
- Cartesian product expansion for composed parametrize
- Composed parametrize resolution with expand/compact/FixtureRef
- Add ColorCategory/JunitCategory enums and simplify reporters (#516)

## [0.6.0] - 2026-05-26

### Features


- Add protocol_version field to wire format (#455)
- Warn on protocol version mismatch (#455)
- Add _SkipMark decorator with when=/reason= validation

## [0.5.0] - 2026-05-25

### Bug Fixes


- Keep multi-line assertion messages inside diagnostic box (#433)
- Use python -m oxitest directly in bacon.toml
- Force color output in bacon oxitest job
- --color=always now forces console crate colors
- Case-insensitive git repo detection + review fixes
- Use oxitest TempDir instead of pytest tmp_path
- Use separate --skip flags for prek pre-push hooks
- Move pyo3/extension-module to maturin-only feature

### Features


- Ship bacon.toml with default dev jobs (#421)
- Add bacon to nix devshell (#422)
- Add bacon keybindings for job switching
- Add --fixtures/--fx and -q/--quiet flags
- Add --fixtures listing with box-style output
- Add --junit-xml flag and quick-xml dependency
- Add JUnit XML reporter for CI integration
- Add --list flag to print collected tests and exit
- Add --affected flag for git-aware test selection
- Add git diff and file classification module
- Add AST-based import graph for --affected
- Integrate --affected into test pipeline
- Add affected_base setting for default --affected ref
- Add affected-tests pre-push hook
- Add --retries and --retries-delay flags
- Add Flaky outcome with stats and summary
- Add flaky_count tracking and retry module
- Integrate retry phase after test execution
- Extend bridge-sync with wire format validation

## [0.4.0] - 2026-05-23

### Bug Fixes


- Remove double 'ms' suffix in TTY reporter (#364)
- Align parametrize duration column with regular test lines (#364)
- Use singular 'case' for single parametrize result (#366)
- Use .cases for len check on parametrize types
- Rename map_ser to state (codespell)
- Add 'ser' to codespell ignore-words-list (serde::ser)
- Use line-buffered stdout instead of per-group flush
- Suppress ty unresolved-attribute for stdout.reconfigure
- Read warned message from message field, not failure_repr
- Box-style warning output and suppress WarnCapture leaks
- Show duration and warning type only in inline WARN lines
- Use bold green for diff right value, distinct from dim green why
- Use neutral dim gray for why/value labels in failure blocks
- Add diff section header in diagnostic box

### Features


- Dynamic name width with truncation for aligned duration column (#364)
- Route tracing output through indicatif to prevent scrollback artifacts (#364)
- Add SINGLE_CASE_PARAMETRIZE violation kind
- Detect single-case parametrize at collection time
- Add SingleCaseParametrize to Rust ViolationKind
- Add SingleCaseParametrize violation type, mapping, and formatting
- Color-coded assertion diffs using similar crate
- Hide internal oxitest frames in short traceback mode
- Add fix suggestions for common error patterns
- Failures-only output in non-verbose mode
- Show multiple running test names during parallel execution

### Performance


- Reuse line buffer in worker reader thread (#373)
- Pre-allocate timings Vec with capacity (#374)
- Single-pass finalize via merge_timings/record_timing_outcomes (#375)
- Sorted Vec instead of BTreeMap in serialize_sorted (#376)
- In-place sort_by_key for FailedFirst strategy (#377)
- Move _middleware import to module scope (#378)
- Direct field access in _build_failure_repr (#379)
- Cache frozenset(fixref_fields) on _DataclassCases (#380)
- Compute unique_name once, pass to _load_and_resolve (#381)
- Move _PluginMarkHandler import to module scope (#382)
- Flush stdout per group instead of per test (#383)
- Pre-serialize conftest_paths as RawValue (#384)
- Compact result format — omit null/empty fields (#385)

## [0.3.0] - 2026-05-22

### Bug Fixes


- Accept warned status in task_group cancellation test

### Features


- Add is_async field to Python CollectedItem
- Detect async test functions via iscoroutinefunction
- Add is_async field to Rust CollectedItem and TestItem
- Flag async fixtures with is_async during registration
- Add AsyncBackend protocol and AsyncioBackend
- Add _run_base_async and async execution path in executor
- Reject async fixtures in sync tests with clear error
- Support async yield fixtures with teardown in reverse order
- Reject async yield fixtures in sync tests
- Route async timeouts through asyncio.wait_for
- Add async loop state to FixtureSession
- Eagerly resolve shared async fixtures on session loop
- Route tests using shared fixtures to session loop with stray task cleanup
- Async fixture teardown in end_session
- Add built-in task_group fixture
- Hard error when sync fixture depends on async fixture
- Hard error when shared fixture depends on non-shared async
- Show async test count in collection summary
- Expand AsyncBackend protocol with SharedAsyncSession
- Add async_backend field to Plugin and PluginRegistry
- Add resolve_backend with error types
- Add async_backend config field
- Init async backend from Rust pipeline
- Thread SharedAsyncSession through fixture session
- Add _fixture_scope context manager (#353)
- Add _PluginMarkHandler and unified evaluate_marks (#356)
- Add _resolve_deps combinator and async policies (#354)
- Add _FixtureOutcome and _unpack_sync (#357)
- Add middleware pipeline infrastructure (#352)

## [0.2.0] - 2026-05-21

### Bug Fixes


- Protect breaking commits, tighten parser regexes, clean up cliff.toml
- Remove unnecessary fetch-depth, pin action-gh-release to SHA
- Configure release-plz to skip crates.io registry
- Add name to fetchCargoVendor, maturin to nativeBuildInputs, mainProgram and platforms to meta
- Remove redundant maturin from nativeBuildInputs (maturinBuildHook provides it)
- Use deadline-based watchdog — empty lines no longer reset timer
- Replace unwrap() on child stdin/stdout with graceful error handling
- Skip unknown worker node_id instead of synthesising empty TestItem
- Replace unsound lineno as-usize casts with try_from
- Emit crashed results when worker stdin write fails
- Assert shared_fixture_names() sorted order directly
- Use most-local definition for shared fixture introspection
- Use tracing::warn! for shared fixture warning
- Add fixture_count field to shared fixture warning
- Show full tracing warning message in parallel how-to
- Add shared_fixture_names override test; clarify warning format note
- Preserve pyproject.toml workers when CLI flag absent (#68)
- Add worker_count docstring and serial+auto conflict test (#68)
- Resolve ty check errors for version-compatible type safety
- Remove stale oxitest and duplicate dev tools from devShell
- --schedule respects pyproject.toml + add --timeout CLI flag
- Remove LoguruLogBackend from core (leaked to stderr)
- Ignore plugin system files in codecov coverage
- Set core.hooksPath so worktrees share pre-commit hooks
- Allow codecov upload failure on dependabot PRs
- Harden test subprocess safety
- Log warning on plugin settings serialization failure (#162)
- Improve unreachable!() message in strict.rs per_test_error (#163)
- Preserve pyproject color when --color not passed
- Surface teardown errors instead of swallowing with .ok()
- Return exit 4 and print to stderr on JSON write failure
- Verbose() now propagates to show_tips/show_warnings
- Make outcome_label() match exhaustive
- Add return type annotations to PluginRegistry properties
- Update bridge sync script for BridgeFrame rename (#216)
- Correct _load_and_resolve return type annotation (#217)
- Resolve compiler warnings from trait seam refactor (#218)
- Remove old pipeline.rs after module restructure (#218)
- Resolve ruff lint violations in bench scripts

### Features


- Add cliff.toml for git-cliff changelog configuration
- Add release job to publish workflow — creates GitHub Release after PyPI publish
- Add nixpkgs-style derivation for oxitest v0.1.0
- Expose oxitest as flake package output and add to devShell
- Add drain_remaining_into_crashed helper with test
- Add has_shared_fixtures() and shared_fixture_names() to FixtureSession
- Expose shared_fixture_names() through FixtureSession
- Warn when shared=True fixtures detected in parallel run
- Add WorkerCount enum and update parse_workers (#68)
- Update Cli/Config to use WorkerCount, add -n short flag (#68)
- Add WorkerCount serde support and pyproject.toml workers field (#68)
- Update compute_optimal_workers for WorkerCount enum (#68)
- Add health recipe for tool availability check
- Add --schedule flag for pluggable group scheduling strategies
- Add exc_type field to TestResult and BridgeResult
- Populate exc_type in exception handlers
- Implement raises parameter check
- Add Long variant to TbStyle and pyproject.toml support
- Capture structured traceback frames in TestResult
- Thread traceback frames through serial path
- Serialize traceback frames in worker JSON
- Render --tb=long with frames section
- Revise --tb=line to single-line-per-failure mode
- Add pyproject config for verbose/maxfail/durations/serial + --color flag
- Add Plugin dataclass and Protocol stubs
- Implement plugin loader with validation and registry
- Parse plugins list and plugin_settings from pyproject.toml
- Wire plugin loading into session startup via bridge
- Integrate LogBackend protocol with LogCapture fixture
- Integrate FixtureProvider protocol with fixture resolution
- Add PyPluginReporter wrapper for Python plugin reporters
- Wire plugin reporters into CompositeReporter via make_reporter
- Integrate Collector protocol with collection pipeline
- Integrate ExecutionWrapper protocol with mark system
- Add bridge contract sync checker script
- Add record_teardown_warning to Reporter trait
- Implement record_teardown_warning on Tty/Ci reporters
- Add test generator for multi-tier bench suite
- Add multi-tier bench runner
- Add regression checker with unit tests

## [0.1.0] - 2026-05-13

### Bug Fixes


- Remove UV_PYTHON — causes maturin to target immutable Nix store
- Set VIRTUAL_ENV before maturin develop to prevent uv resolving into Nix store
- Strict = "abort" in pyproject.toml; remove test_skipped_via_pytest
- Resolve real git dir in build.rs for worktree compatibility
- Move capture-environment early-exit before filesystem setup; update exit-codes doc
- Correct remote-ref field in check-no-plans-on-main; add no-commit-to-branch builtin
- Annotate Fixtures.fixture impl signature to silence griffe warnings
- Migrate PyO3 API from 0.22 to 0.28
- Fix sticky comment path, pin tarpaulin output, add codecov fail_ci_if_error
- Add setup-uv to rust-coverage so PyO3 can find libpython for linking
- Switch tarpaulin to cargo-llvm-cov (compatible with PyO3 extension-module)

### Features


- Initial pipeline v0.1 — config, collector, PyO3 bridge, executor, reporter
- TTY/CI reporter — diagnostic blocks, ANSI color, exit codes, --json CTRF
- Marks — skip, skipif, xfail, usefixtures, custom markers, -m expression filter
- Fixture engine — Fixtures class, FixtureDef, FixtureSession, conftest loader
- Parametrize — mark.parametrize → oxitest.parametrize, dict/dataclass modes
- Type-safe fixture annotations — Fixture[T], FixtureRef[T], Yields[T]
- Built-in fixtures Tier 1 — TempDir, TempDirFactory, StdCapture, FdCapture, Patcher
- Built-in fixtures Tier 2 — LogCapture, WarnCapture, FixtureTeardownWarning
- Test helpers — raises, warns, importorskip, timeout
- Strict mode — bare-assert checker, dict-parametrize violations, --strict flag
- Test cache — TestCache, --lf, --ff, --durations, per-test timeout scaling
- Parallel execution — Scheduler, persistent worker pool, --workers N, --serial
- Fixture namespaces — Fixtures(name=), FixturesProxy, fx:Fixtures injection
- AST assert rewriter — operand capture for enriched failure output
- Add bug report issue template
- Add feature request issue template
- Add pull request template
- Add AI agent checklist items to PR template
- Create build.rs and add --capture-environment flag to Cli
- Implement --capture-environment environment snapshot
- Add pre-push hook to validate v* tag matches Cargo.toml version
