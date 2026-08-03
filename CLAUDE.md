# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

oxitest is a Python test runner rewritten in Rust. It exposes a Python API (fixture system, marks, builtins) implemented in `python/oxitest/_bridge/`, and a Rust core that orchestrates collection, scheduling, parallel execution, caching, and reporting. The two halves communicate via PyO3.

## Commands

```bash
# Enter development shell (provides cargo, python, maturin, just)
devenv shell

# Check all required tools are available
just health

# Check required agent skills are installed (warnings only)
just agent-health

# Build the Rust extension (required before running Python tests)
just build

# Run Python tests (no rebuild — build first if Rust changed)
just test-python

# Run a single Python test file
just test-python python/tests/test_fixture_registry.py

# Run Rust unit tests
just test-rust

# Run a single Rust test
just test-rust <test_name>

# Run all static checks (format, lint, clippy, spelling)
just check

# Format code and fix typos
just fmt

# Full pre-push gate (clean + check + test-rust + build + test-python + doc tests)
just preflight

# Clean build artifacts
just clean

# Show all available recipes
just
```

## Workflow

### Two rules that govern this section

**Arity — exactly one file defines each fact.** `CLAUDE.md` is that file *unless another consumer already owns it*. Where another consumer does own a fact, point at the live source instead of restating it: label values come from `gh label list`, gate definitions from the `justfile`, skill names from `docs/agents/required-skills.txt`. A restatement is a copy, and copies drift — two of this file's recorded defects were restatements that went stale while the thing they described moved.

**Enforcement tiers — every obligation below declares one.** Prose an agent may silently skip is a legitimate choice, but it must be a *chosen* one, so that a step nobody performs can be told apart from a step nobody thought about.

| Tier | Mechanism | Use for |
|---|---|---|
| `gate` | `just` / CI / prek fails | machine-checkable facts |
| `artifact` | produces visible output — a PR checklist tick, a required citation | claims a reviewer must be able to audit |
| `fold-in` | the step ceases to exist as a separate action | always prefer this where it is available |
| prose | honor system, by design | everything else |

### Track A — the change pipeline (one change, linear)

```
  1       2       3       4        5        6         7         8        9       10
Grill → Issue → Triage → Spec → Draft PR → Plan → Implement → Review → Merge → Debrief
                          ▲
                          └─ a re-scoped or re-grilled issue re-enters here (Track B)
```

The numbers are the stages below. Rebase, preflight and waiting for CI are *inside* stage 9 — see its merge sequence.

**1. Grill new ideas.** Any new feature, concept, or design direction MUST be stress-tested against the existing domain model and documented decisions before anything is committed to.

The **user** invokes `grill-with-docs` — it is marked `disable-model-invocation`, so an agent cannot call it and this stage will otherwise read as skipped rather than as impossible. An agent that reaches this stage unaided runs `grilling` plus `domain-modeling` (which is what `grill-with-docs` does) and records in the issue that it did so, and that no user-driven grilling took place.

**2. Create issues.** Once an idea survives grilling and is deemed worth implementing, create GitHub issues. Every issue MUST state the "why" — why is this change needed? What problem does it solve? Organize into milestones if the work spans multiple issues.

At creation, apply exactly one **category** label, **one or more** `area:` labels, and one **triage state** label. Run `gh label list` for the current vocabulary — this file deliberately does not restate it, because the tracker cannot disagree with itself and a restatement can.

**3. Triage issues.** Every issue gets a **state label** reflecting its triage status. See `docs/agents/triage-labels.md` for the state vocabulary. Triage is also where `priority:` and `size:` are applied — they are judgements, not facts known at filing time, and a guessed `size: M` is worse than no label at all.

**4. Spec every issue.** A spec is consumed as fact by every stage after it, so its claims are bound by *Evidence for analysis outputs* below — including the ones that merely characterise current behaviour. The most thoroughly-grilled spec in this repo's history carried three false claims, and every one was caught by measurement rather than by a stage.

By the time a PR is created, every issue in that PR MUST have a design spec. If no issue exists yet for the work being specced, create one first — every spec needs a home issue. Specs can be written when the issue is picked up or ahead of time — but never skipped. Use the `superpowers:brainstorming` skill for spec design. Post each issue's spec section as a comment on that issue. When issues share a grouped spec, post only the section relevant to each issue — not the entire spec on every issue.

**5. Create a draft PR.** Open the draft PR *before* any implementation, so the approach can be reviewed early. GitHub requires at least one commit, so scaffold with an empty one and fold it away later:

```bash
git commit --allow-empty -m "chore: scaffold (#N)"
git push -u origin <branch>
gh pr create --draft --assignee @me --title "..." --body "..."
# the first real commit absorbs the scaffold — this rewrites an already-pushed
# commit, so the next push must be forced:
git commit --amend
git push --force-with-lease
```

`--force-with-lease` rather than `--force`: it refuses if the remote moved since your last fetch, so a force-push can never silently discard someone else's work.

Assignment is **folded into `gh pr create`** (`fold-in`) — there is no separate `gh pr edit --add-assignee` step left to forget. The previous separate step was skipped on 4/4 PRs in one session with nothing surfacing it.

**6. Plan before implementing.** Use the `superpowers:writing-plans` skill. Multiple issues can be grouped into one plan if they are tightly coupled or logically sequential. The plan MUST be posted as a comment on the PR — never on individual issues.

**7. Implement via subagents or inline.** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans`.

When you dispatch, `docs/agents/dispatch-protocol.md` defines what a dispatched agent owes the rest of this pipeline — stage obligations, workspace isolation, citation scope, which gates it must not run, and its standing permission to refuse you. Every clause there was measured on a real wave. A dispatched agent inherits none of this pipeline's stages unless the prompt names them: in the run that measured it, stage 10 compliance was **0/4** (`artifact` — the prompt must name the stages it delegates).

**8. Post-implementation review.** After all plan tasks are implemented and pushed, run these passes before marking the PR ready:

- **Scope the diff against the merge-base, three-dot** — `git diff main...HEAD`, after a fetch. Two-dot compares *tips*, so anything merged to `main` since the branch point renders as a deletion by your branch; one review opened on a `main..HEAD` diff reporting **902 phantom deletions** across files the branch had never opened. The fetch matters for the second half of this rule: re-run any gate added to `main` since the branch point — a strict-docs gate once never ran on a branch until review caught it.
- **`ponytail:ponytail-review`** on the branch diff — hunt over-engineering, dead code, and unnecessary complexity. May be skipped on a single-commit PR touching no public surface, **provided the skip and its reason are recorded in the PR checklist** (`artifact`). No size threshold is set: the yield data is currently too thin to justify removing a gate, and the recorded skips are how that data gets collected.
- **`/improve branch`** — audit the branch changes for correctness, security, test coverage gaps, and tech debt.
- **Cross-reference the two passes before acting — for ordering, not just for overlap.** Findings that look unrelated can be sequenced: one pass once flagged four duplicated test harnesses while the other flagged a missing test, and the missing test would have been a *fifth* copy of the harness, so the deduplication had to land first. Neither pass can see that from inside itself.
- **Explore findings before acting.** Present findings to the user. For each finding, explore the cited code to verify it's real and determine if the fix is safe. Only fix after exploration confirms the finding is actionable. Never blindly apply review suggestions.
- **Docs evaluation.** Check whether the changes affect user-facing documentation. Scan `docs/user/`, `docs/internals/`, `CONTEXT.md`, and error references for stale content. If docs need updating, fix them in the same PR — don't let stale docs ship.

**9. Merge rules.**

Three different operations in this stage get called "rebase" in ordinary speech. They are named separately here and used consistently throughout: **regroup** (rewriting your branch's own commits into coherent units), **rebase onto `main`** (merge-sequence step 1), and **`--rebase` merge** (the GitHub merge strategy).

- **Never push directly to main.** All changes go through pull requests.
- **Never merge without approval.** Wait for either a GitHub review approval or an explicit user command (e.g., "merge", "merge rebase delete branch"). Do not auto-merge after CI passes.
- Only `--rebase` merge is allowed. Never squash merge, never merge commits.
- Every commit message title MUST include its related issue number: `feat: add Foo (#42)`
- Multiple issues per commit are fine: `feat: add Bar and Baz (#43, #44)`
- **PR closing keywords**: GitHub requires the keyword before EACH issue number. Write `Closes #1, Closes #2, Closes #3` — NOT `Closes #1, #2, #3` (only the first gets closed).
- Run `just preflight` before pushing.

**Pre-merge commit regroup (`artifact`).** When a merge is triggered, regroup the branch into coherent commits — or record in the PR why the existing grouping is already coherent. Either way it leaves a mark, because this step has now been skipped silently, reported as done when it was not, and argued against with a false claim that the tooling made it impossible. A tick that says "already coherent" is a legitimate outcome; a tick that is absent is not.

The tooling is available, contrary to that claim:

```bash
git branch -f backup/<slug> HEAD          # 1. safety net, before touching anything

git reset --soft HEAD~N && git commit     # collapse the last N into one
git rebase --onto <base> <old-parent> <branch>   # move a middle commit
GIT_SEQUENCE_EDITOR=true git rebase --autosquash -i <base>   # fold fixup! commits

git diff --quiet backup/<slug> HEAD       # 2. empty ⇒ nothing lost or gained
```

`git rebase -i` works here **provided `GIT_SEQUENCE_EDITOR` is set** — it is only the interactive editor that is unavailable, not the command, so `--autosquash` is usable too. The two bracketing commands are the point: tree equality proves the regroup preserved content whatever the commits became, which means every gate result from before the regroup still applies afterwards.

**Merge sequence** — this order, every time:

1. rebase onto latest `main`;
2. re-run `just preflight` **after** rebasing onto `main` — even if CI was green before it;
3. push; wait for CI green;
4. `gh pr merge --rebase`.

**"CI green" means the required contexts, not every check.** The required set is defined by branch protection — query it (`gh api repos/{owner}/{repo}/branches/main/protection`) rather than trusting a remembered list, because a copy here would drift. A red **non-required** check is not a merge blocker; say so in the debrief and move on. Do not make a coverage check green by measuring less — widening an `ignore:` list over untested code is the recorded anti-pattern, not a fix.

**A cross-cutting change must be re-verified against a freshly-rebased branch.** CI builds the *merge commit*, so a rename or a vocabulary change is broken by construction by anything that lands on `main` meanwhile — and every local gate stays green throughout, because locally the two halves never meet. This is a trigger for merge-sequence step 2, not a new step: for any rename, vocabulary change, or branch left open more than a day, rebase onto latest `main` and re-run the gate *before* requesting merge rather than discovering it in CI.

`.config/wt.toml` sets `pre-merge = "just preflight"`, so step 2 happens automatically for `wt merge` and is **bypassed by `gh pr merge`**. That asymmetry is why the sequence is written here rather than assumed.

**Never pass `--delete-branch`.** `main` is pinned to the primary worktree, so the merge lands but local cleanup cannot succeed — it failed 4/4 times in one session. Do the cleanup directly instead:

```bash
gh pr merge --rebase
git push origin --delete <branch>
git -C <primary-worktree> pull --ff-only
wt remove <branch> -D --foreground --yes
```

`--yes` is not optional here: `wt` prompts for approval, and without it the command fails outright in a non-interactive session — in the very block offered as the workaround for a known trap. The same applies to `wt switch --create`.

**10. Post-merge debrief.** After a PR is merged, if the implementation diverged from the plan, add a debrief comment to the closed PR explaining how, where, and why it diverged. Apply the `diverged-from-plan` label to the PR. This label is only applied to closed/merged PRs.

### Track B — backlog maintenance (whole backlog, cyclical)

Track A is per-change and linear. Backlog maintenance — triage sweeps, relevance audits, re-grilling existing issues — runs over the whole backlog at once and produces no merge. It is a **separate track, not a stage**, and it joins Track A at Spec when an issue is re-scoped.

```
Sweep → Verdict → Disposition ─→ re-scoped issue re-enters Track A at Spec
```

**Issues rot three ways, and only one of them justifies closing:**

| Rot mode | What is stale | Disposition | Evidence required |
|---|---|---|---|
| (a) the defect is fixed | the issue itself | **close** | the citation in "Evidence for analysis outputs" below (`artifact`) |
| (b) the defect stands, its *characterisation* is stale | the description | **re-scope** | comment recording what changed and why this is not a close (`artifact`) |
| (c) the defect stands, its *vocabulary* names deleted concepts | the wording | **re-word** | comment mapping old term → current term (`artifact`) |

The default disposition for a stale-*looking* issue is correct it, not close it. In the audit that produced this section, ~30 issues were reviewed: **0 were closeable and 8+ needed correction.** A "close what looks stale" pass would have destroyed information in eight places.

Re-labelling and re-prioritising need no evidence (prose).

### Evidence for analysis outputs (`artifact`)

The pipeline gates code. This gates *conclusions*.

**The rule fires on any claim whose acceptance subtracts work or a gate** — on consequence, not on wording. A wording trigger is evaded the moment someone writes "appears resolved" instead of "no longer reproduces".

| Claim **adds** work | Claim **subtracts** work |
|---|---|
| "this looks wrong", "missing a test", "possible bug here" | "no longer reproduces", "this issue is stale", "clippy is green", "this file is unused" |
| Wrong ⇒ someone investigates and finds nothing. Self-correcting. | Wrong ⇒ information is destroyed and nothing looks again. Silent and permanent. |
| **no citation needed** | **citation required** |

**Direction is the common case, not the rule.** The rule is *consequence*, and a claim that becomes an input to a decision is load-bearing whichever column it falls in. Three kinds slip through the table above: a claim that **specifies the verification itself** (which mutant, which command, which assert fails) — get it wrong and the test it prescribes is vacuous while reading as coverage; a claim that merely **characterises current behaviour** and then becomes spec input; and a claim **inherited** from an issue or spec written days earlier. Re-verify an inherited claim **when you act on it**, not when it was written — this repo ships fast enough that claims go stale between filing and dispatch, and a `confirmed` label is exactly what suppresses the re-check.

A subtracting claim MUST carry:

1. the **exact command** re-run, and its output;
2. evidence the command is **the one the claim is about** — one real verdict cited `just check` against an issue whose reproduction used a different clippy invocation, and so measured a different thing. That particular gap was later closed in #1815; the lesson is the mismatch, not the command;
3. evidence the run **executed** rather than replaying a cache — a cached `cargo clippy` once returned 0 where a forced rebuild found 11. "Green" and "ran" are different claims.

This is `artifact` tier: it binds when someone reads the comment. Its value is that the omission becomes visible — a missing quote is the tell — where today there is nothing to look for.

### Believing a verdict (`artifact`)

**"Printed something friendly" ≠ "did the thing".** A command can report success without having executed, and ten distinct mechanisms for it have been observed in this repo — so this is stated as an invariant rather than as a list of traps to memorise, because the list has been outgrown ten times.

Before believing any verdict:

1. **Capture the exit status directly**, never through a pipe. `cmd | tail -30` reports *tail's* status, which is how a `command not found` once read as a passing gate.
2. **Pin an asynchronously-fetched verdict to its subject** before reading it. Resolve the head SHA first (`gh pr view "$PR" --json headRefOid`) and refuse any answer that is not about that SHA: an **empty** CI rollup reads as "nothing pending", and after a force-push a **complete green tally belonging to the previous head** reads as success.
3. **Treat an implausible duration as "did not run".** `just preflight` costs 150–300 s here; four branches once reported a failing preflight in 0–4 s because a `sed` had rewritten the recipe name. Wall-clock caught what the exit code did not.
4. **State the run count.** N clean runs is not evidence of absence. Say how many times you ran it and capture the output — a flake and a fix are indistinguishable from a single green.

### Gate coverage (`artifact`)

**Name the gate that covers your change. If you cannot name one, verify it by hand and describe how in the PR.**

The `justfile` is authoritative for what the gates do; this file deliberately does not restate it. Gates get added — strict mkdocs, mdbook and `cargo doc` each entered preflight in separate changes — so any coverage table written here would have been wrong three times over.

Illustrative only, **not exhaustive**. Note that syntax-valid is not verified:

| Semantically gated | Syntax-only or ungated |
|---|---|
| `src/**.rs` — fmt, clippy, `test-rust`, `cargo doc` | `bacon.toml`, `prek.toml`, `cliff.toml`, `codecov.yml` — `check-toml`/`check-yaml` parse them; nothing validates them |
| `python/**.py` — ruff, ty, `test-python` | `justfile`, `devenv.nix`, `flake.nix`, `nix/` — no gate at all |
| `docs/**.md` in the mkdocs nav — `mkdocs --strict` | `.github/workflows/*` — YAML syntax only, no actionlint |
| `docs/internals/**` — mdbook | `.envrc`, `.config/wt.toml` — no gate |
| `Cargo.lock`, `uv.lock` — lock checks | `*.md` outside the mkdocs nav — codespell only, no link check |

## Tools

### Worktrunk (`wt`)

All branch management uses Worktrunk. Never use raw `git checkout` or `git branch` for feature work.

```bash
# Create a new worktree for a feature branch
wt switch --create <branch>

# Switch to an existing worktree
wt switch <branch>
```

Worktrunk runs `direnv reload` on switch (`post-switch` hook), which activates the devenv shell automatically. This means all tools (`cargo`, `ruff`, `just`, `prek`, etc.) are on PATH immediately — no manual nix store path hunting.

### devenv

The development environment is managed by devenv. All commands assume you are inside the devenv shell.

```bash
# Enter manually (if not using wt)
devenv shell

# Load into current shell without subshell
eval "$(devenv print-dev-env)"
```

Never install tools globally or via `pip install` / `cargo install`. If a tool is missing, add it to `devenv.nix`.

### prek

Pre-commit hooks are managed by prek (not pre-commit). Hooks run automatically on `git commit`. To run all hooks manually:

```bash
prek run --all-files
```

## Architecture

### Two-layer design

**Rust layer** (`src/`): Entry point is `src/lib.rs`, which exposes `run(args)` and `trace(level, module, message)` PyO3 functions. The Rust layer handles:
- `config.rs` — CLI parsing (clap) and `pyproject.toml` config under `[tool.oxitest]`
- `collector.rs` — file discovery based on `testpaths`/`python_files` patterns
- `cache.rs` — timing cache for parallel scheduling decisions and `--lf`/`--ff` support
- `filter.rs` — query DSL (`-E`) filtering, `--lf`/`--ff`, grouping by module
- `query/` — query DSL compiler, evaluator, and `oxitest query` subcommand
- `parallel.rs` — spawns worker subprocesses; each worker runs `python/oxitest/_bridge/worker.py`
- `scheduler.rs` — distributes test groups across workers
- `reporter/` — TTY, CI, and JSON (CTRF) reporters; `DiagnosticEntry`/`DiagnosticSeverity` in `stats.rs`; severity-sorted dedup rendering in `format/summary.rs`
- `strict.rs` — strict-mode violation checking (bare asserts, dict parametrize, missing mark reason)
- `bridge.rs` — PyO3 calls into the Python bridge: `collect_module`, `run_test`, `FixtureSession`, `drain_session_diagnostics`

**Python bridge** (`python/oxitest/_bridge/`): Pure-Python layer that does the actual test execution. Key modules by responsibility:

*Fixture system:*
- `_fixture_registry.py` — `FixtureDef`, `FixtureRegistry`; fixture definition and registry
- `_fixture_session.py` — `FixtureSession`, `_SessionProtocol`, `_Scope`; fixture lifecycle (scope caching, yield teardown, autouse)
- `_fixture_context.py` — fixture resolution context, `_warn_teardown` diagnostic helper
- `_fixture_instantiator.py` — fixture instantiation and dependency injection
- `_fixture_type.py` — `Fixture[T]`, `FixtureRef[T]`, `Yields[T]` type aliases
- `_fixture_validator.py` — fixture signature and type validation
- `proxy.py` / `proxy_ns.py` — `FrozenProxy` (shared fixtures) and `FixturesProxy` (namespace-aware `fx: Fixtures` injection)
- `_builtins/` — built-in injectable fixtures: `TempDir`, `TempDirFactory`, `StdCapture`, `FdCapture`, `Patcher`, `LogCapture`, `TestContext`

*Mark system:*
- `_mark_api.py` — mark evaluation: skip, xfail, timeout, and custom marks
- `_mark_registry.py` — mark registration and custom mark definitions

*Plugin system:*
- `plugin_loader.py` — plugin import, validation, `PluginRegistry` (frozen dataclass), `_PluginRegistryBuilder`
- `_plugin_config.py` — plugin settings resolution

*Execution:*
- `executor.py` — `run_test()`: loads module, resolves fixtures/parametrize, runs test, returns `TestResult`
- `_runners.py` — test execution runners (serial, debug)
- `result.py` — `TestResult` and outcome types, `Diagnostic` and `DiagnosticSeverity`
- `worker.py` — entry point for parallel worker subprocesses; reads JSON tasks from stdin, writes LDJSON results/diagnostics/traces to stdout via `_emit()`
- `parametrize.py` — resolves `@mark.parametrize` kwargs into per-case values

*Collection:*
- `importer.py` — `collect_module()`: imports test file, discovers `test_*` functions, returns `CollectedItem` list
- `conftest_loader.py` — loads `conftest.py` files, registers their `Fixtures()` instances
- `_loader.py` — module loading infrastructure

*Infrastructure:*
- `_coverage.py` — coverage provider integration (`CoveragePyProvider`)
- `_debugger.py` — debugger backend integration
- `_fn_metadata.py` — `FunctionMetadata` frozen dataclass
- `_violation_checkers.py` — strict-mode violation checking
- `_namespace_validation.py` — fixture namespace validation
- `_diagnostic_collector.py` — `ContextVar`-based `emit_diagnostic()` and `_diagnostic_collector_var`
- `_assert_error.py` — `_OxitestAssertionError` and enriched assertion diagnostics

### PyO3 data contract

Both the serial PyO3 path (`bridge.rs`) and the parallel JSON path (`worker_result/`) converge on `RawOutcome` (in `worker_result/convert.rs`) before producing a `TestOutcome`. `CollectedItem` fields must stay in sync with the Python `collect_module` return type. When adding fields to the Python result objects, update the corresponding `RawOutcome` variant and the PyO3 extraction logic in `bridge.rs`.

### Parallel execution

The Rust scheduler spawns `python -m oxitest._bridge.worker` subprocesses. Each worker receives a JSON task (modules + their items + conftest paths + `rootdir`) via stdin and writes LDJSON lines to stdout (wire protocol v6). Each line has a `"type"` discriminator: `"result"` (test outcome), `"diagnostic"` (user-facing message), or `"trace"` (developer log). The drain loop in `parallel/drain.rs` dispatches on this field. The worker is persistent within a run — it processes tasks until stdin is closed.

### Fixture injection protocol

Parameters annotated with `Fixture[T]` are injected; unannotated parameters are NOT (except built-in types like `TempDir`, `TestContext` which carry their own injection marker). `FixtureRef[T]` is for fixture references inside `@mark.parametrize` kwargs. `Fixtures` (bare, not `Fixture[T]`) injects a `FixturesProxy` namespace accessor.

### Configuration

`[tool.oxitest]` in `pyproject.toml` controls: `testpaths`, `python_files`, `norecursedirs`, `markers`, `timeout`, `cache_max_age`, `min_parallel_tests`, `timeout_multiplier`, `spawn_overhead_ms`, `strict`. All CLI flags override pyproject values.

### Type checking

`ty check` is the project's type checker. `just check` runs it over the **whole project**, tests included — `python/tests` is on `extra-paths` in `pyproject.toml`, so type errors in test code fail the build exactly like errors in `python/oxitest/`.

## Testing

- **Rust unit tests** (`just test-rust`): Unit tests for Rust modules.
- **Python integration tests** (`just test-python`): Run real commands. Tests use oxitest itself as the runner (`strict = "abort"`).
- **CI**: GitHub Actions. Two parallel jobs: `check` (static analysis via `just check`) and `test` (`just test-rust`, `just build`, `just test-python`). Uses `dtolnay/rust-toolchain`, `astral-sh/setup-uv`, `Swatinem/rust-cache` — no devenv in CI.
- **Every `assert` MUST have a message.** oxitest runs with `strict = "abort"` — bare asserts are violations. The message explains *why* the assertion matters — oxitest already shows the where, when, and what (expected vs actual). The message gives the developer the *why* so they can debug the *how*. Bad: `"expected 4 methods, got 3"` (oxitest already shows that). Good: `"FixtureProvider protocol added a method — HostProvider needs to implement it to avoid runtime TypeError"`.
- **Mutation checks need a clean baseline.** A test proves nothing until a mutation makes it fail — but applying a mutant to a file that also holds uncommitted work, then reverting with `git checkout -- <file>`, destroys that work along with the mutant. **`git status --porcelain` must print nothing before you apply a mutant**, and again show only the mutant gone after you revert. Run it; do not intend to. This has now bitten three times, and the run that lost the most had **quoted this rule in its own plan** while violating it — which is why the obligation is a command rather than a sentence.
- **A mutant that passes is a finding until explained.** If the test does not fail, one of two things is true: the test is weaker than it looks, or the mutation is not the inverse of the behaviour you think you changed. Both are worth knowing and neither is "write a better mutant and move on". The worst bug found in this repo's fixture work — a scope cache that was never cleared, leaking a temp directory after every worker's first task group — surfaced because a mutant *passed* and the pass was investigated.
- **Re-run the mutation set after any later change to the same code.** A mutant that *stops* failing is an unintended semantic change, and a review pass late in a branch is exactly when that happens.

### Testing guidelines

Tests in `python/tests/` must follow these rules:

1. **No class-based tests.** Use standalone `def test_*()` functions. The only exception is a class that shares `@oxi.parametrize` parameters across all its methods.
2. **Arrange, Act, Assert.** Every test should have three clear phases: set up test data (arrange), call the thing being tested (act), check the result (assert). Don't interleave setup and assertions.
3. **Dogfood oxitest features.** We are our own best user feedback. Always prefer oxitest APIs over stdlib/third-party equivalents:
   - `oxi.raises()` not `try/except` or `assertRaises`
   - `oxi.warns()` or `WarnCapture` not `warnings.catch_warnings()`
   - `TempDir` fixture not `tempfile.mkdtemp()` or `tempfile.TemporaryDirectory()`
   - `Patcher` fixture not `unittest.mock.patch` or raw `os.environ` manipulation
   - `StdCapture`/`FdCapture` not manual `sys.stdout` redirection
   - `LogCapture` not manual `logging.Handler` setup
   - `@oxi.parametrize` for multiple similar cases, not copy-pasted test functions
   - Dataclass-based test doubles not `unittest.mock.MagicMock`
   - Exception: when testing an oxitest feature itself requires bootstrapping (e.g., testing `Patcher` needs direct `os.environ` access), stdlib is acceptable in the arrange phase.
4. **Import test utilities as plain functions.** Shared utilities live in the `python/tests/helpers/` package, reached with `from tests import helpers` and called as `helpers.<function>()` — never `sys.path.insert`. Do **not** use the retired `helpers.common.<function>()` registry proxy (#1700, #1787): it resolved through `Helpers.__getattr__` to `Any`, so `ty` checked nothing at the call site. That blind spot hid 141 real type errors across ~790 calls.

## Agent skills

### Issue tracker

GitHub Issues on `kalonji-tools/oxitest`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default label vocabulary (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout — `CONTEXT.md` + `docs/adr/` at root. See `docs/agents/domain.md`.

### Dispatch protocol

What a dispatched agent owes this pipeline. See `docs/agents/dispatch-protocol.md`, referenced from stage 7.
