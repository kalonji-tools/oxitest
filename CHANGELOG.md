# Changelog

All notable changes to this project will be documented in this file.
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
