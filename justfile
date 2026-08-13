# Apply Nix environment workarounds only when inside a Nix shell.
# Outside Nix these expand to empty strings and have no effect.

# Nix injects Python paths into PYTHONPATH and _PYTHON_SYSCONFIGDATA_NAME,
# corrupting sysconfig.EXT_SUFFIX and causing maturin to name the .so with
# the wrong ABI tag. Unset both before any maturin/python invocation.
fix_env := if env("IN_NIX_SHELL", "") != "" {
    "unset _PYTHON_SYSCONFIGDATA_NAME PYTHONPATH &&"
} else { "" }

# maturin internally calls `uv pip install`; without VIRTUAL_ENV set, uv
# follows the venv python's symlink back to the immutable Nix store and fails.
# Point VIRTUAL_ENV at the project venv (devenv sets UV_PROJECT_ENVIRONMENT,
# falls back to .venv outside devenv).
venv_dir := env("UV_PROJECT_ENVIRONMENT", justfile_directory() / ".venv")

maturin_env := if env("IN_NIX_SHELL", "") != "" {
    "VIRTUAL_ENV=" + venv_dir
} else { "" }

# ── Color codes for log output ───────────────────────────────────────────────
_green := "32"
_red := "31"
_yellow := "33"
_blue := "34"

# ── Recipes ──────────────────────────────────────────────────────────────────

# Show available recipes
default:
    @just --list

[private]
_log color msg:
    @printf '\033[{{color}}m→ %s\033[0m\n' {{ quote(msg) }}

# Build the Rust extension
build *args: (_log _green "Building extension...")
    {{fix_env}} uv sync --group build
    {{maturin_env}} maturin develop {{args}}

# Run Python tests (no rebuild — use `just build` first if Rust changed)
test-python *args: (_log _blue "Running Python tests...")
    uv run python -m oxitest {{args}}

# Run Rust unit tests (matches CI: rejects unreferenced snapshots)
test-rust *args: (_log _blue "Running Rust tests...")
    cargo insta test --unreferenced=reject {{args}}

# Run all static checks (format, lint, clippy, spelling)
check: (_log _blue "Running static checks...")
    ruff format --check
    cargo fmt --check
    ruff check
    ty check
    cargo clippy --all-targets -- -D warnings
    codespell --toml pyproject.toml
    actionlint
    python scripts/check_subprocess_encoding.py
    python scripts/check_justfile_quoting.py

# Validate lock files match manifests (matches prek pre-push hooks)
check-locks: (_log _blue "Checking lock files...")
    uv lock --check
    cargo metadata --locked --format-version 1 --quiet > /dev/null

# Deliberately NOT part of `preflight`, which runs earlier in the merge sequence
# than a closing set or a disposition table is settled. Both checks accept
# `--pr`, so `args` reaches each unchanged.
#
# Order is load-bearing: the disposition check runs last because its question —
# does every closing issue say where its undelivered scope went — is only
# meaningful once the closure set is known to agree with the title (#2057).
#
# The unresolved-thread check that used to run first is gone (#2072).
# `required_conversation_resolution` is enabled on `main` and now binds at
# merge, because the merge no longer bypasses branch protection.
#
# The two checks below have no such platform equivalent, so this recipe is the
# only thing that runs them and nothing forces you to run it. A CI context
# carrying them was built and removed on #2072 — see the merge sequence in
# CLAUDE.md for why, and for when to revisit it.
#
# Only the line directly above a recipe becomes its `just --list` description.
# Refuse the merge on unnamed closures or a missing disposition (stage 9, step 4)
merge-ready *args: (_log _blue "Checking merge readiness...")
    python scripts/check_closing_issues.py {{ args }}
    python scripts/check_disposition.py {{ args }}

# Validates the whole spec against the diff before writing anything.
#
# Only the line directly above a recipe becomes its `just --list` description.
# Post one stage-8 review pass as anchored review threads (stage 8)
review-post spec *args: (_log _blue "Posting review findings...")
    python scripts/post_review_findings.py {{ quote(spec) }} {{ args }}

# Only `Fixed` is resolved by the agent; every other verb posts its reply and
# leaves the button to you.
#
# Only the line directly above a recipe becomes its `just --list` description.
# Record a stage-8 finding's disposition (stage 8)
review-dispose slug id verb reason *args: (_log _blue "Recording disposition...")
    python scripts/dispose_finding.py {{ quote(slug) }} {{ quote(id) }} {{ quote(verb) }} {{ quote(reason) }} {{ args }}

# A mutant applied over uncommitted work is destroyed with that work by the
# `git checkout -- <file>` that reverts it. That revert cannot touch untracked
# files, so untracked content is not a reason to refuse — refusing on it made
# this gate's verdict depend on which worktree you stood in (#1939). Gitignored
# scratch was already invisible to `git status --porcelain`.
#
# Only the line directly above a recipe becomes its `just --list` description.
# Refuse uncommitted tracked changes before applying a mutant (#1925, #1939)
mutation-guard:
    @if [ -n "$(git status --porcelain --untracked-files=no)" ]; then \
        just _log {{ _red }} "Dirty tree — a mutant applied now dies with the work it sits on:"; \
        git status --porcelain --untracked-files=no; \
        exit 1; \
    fi; \
    just _log {{ _green }} "Clean baseline @ $(git rev-parse HEAD^{tree})"

# The applier lives in scripts/apply_mutant.py, which documents the anchor
# contract and the exit codes this recipe maps. Every terminal state below
# exits explicitly: a mid-recipe failure does not propagate on its own
# (measured — `false` followed by an `echo` exits 0), which is how a void run
# reads as a pass.
#
# Two inputs produce a correct VOID that reads like a tooling fault (#2005):
#
#   1. A mutant that orphans a binding. Inverting `if !x.is_terminal()` to
#      `if false` leaves `use std::io::IsTerminal` unused, which fails the build
#      under `warnings = "deny"` and exits 2. That is the recipe working. Invert
#      the condition rather than deleting the call. Recorded twice before being
#      written down here, at a cost of one cycle each time.
#   2. A test_cmd that is a bare file path. test_cmd is evaluated as a whole
#      shell command, so a path exits 126 and a typo exits 127; both mean no
#      test ran. Pass a runnable command, such as: just test-python <file>.
#
# Only the line directly above a recipe becomes its `just --list` description.
# Run one mutant end to end: apply, build, test, revert (#1939)
mutate path old new *test_cmd: mutation-guard
    #!/usr/bin/env bash
    set -uo pipefail

    if ! git ls-files --error-unmatch {{ quote(path) }} > /dev/null 2>&1; then
        just _log {{ _red }} "MUTANT NOT APPLIED — {{ path }} is not tracked, so the revert would have nothing to restore"
        exit 1
    fi

    python3 scripts/apply_mutant.py {{ quote(path) }} {{ quote(old) }} {{ quote(new) }}
    applied=$?

    # 9 is the applier's own "this mutant cannot be applied"; anything else
    # non-zero is the applier failing for a reason of its own, which is void.
    if [ "$applied" -eq 9 ]; then
        just _log {{ _red }} "MUTANT NOT APPLIED — tree untouched"
        exit 1
    elif [ "$applied" -ne 0 ]; then
        just _log {{ _red }} "VOID: scripts/apply_mutant.py failed (exit $applied) — 127 means python3 is not on PATH, so run inside the devenv shell; 2 means it could not read an anchor, and <old>/<new> are file paths rather than inline text. The docstring in that script documents the rest."
        exit 2
    fi

    # From here the mutant is on disk, so every exit path owes a revert. The
    # recipe runs under `set -uo pipefail` without `-e`, so a shell syntax error
    # inside an eval-ed test_cmd aborts before the explicit revert below and
    # strands the mutant (#2005). Since #2015 this is an `eval` failure only:
    # test_cmd reaches the shell through `quote()`, so a quote in it can no
    # longer break the assignment — but `eval` still interprets the value as a
    # command, so a test_cmd that is not a valid command still fails here.
    # The explicit reverts stay: this is the backstop for the paths that never
    # reach them, and a second checkout of an already-clean file is a no-op.
    # Installed here rather than earlier because before the applier runs there is
    # nothing to revert, and the MUTANT NOT APPLIED path must leave the tree
    # exactly as it found it.
    mutant_path={{ quote(path) }}
    trap 'git checkout -- "$mutant_path"' EXIT

    just _log {{ _blue }} "Mutant applied:"
    git --no-pager diff -- {{ quote(path) }}

    just build
    built=$?
    if [ "$built" -ne 0 ]; then
        # No rebuild here: a failed build installed nothing, so the extension on
        # disk is still the pre-mutant one.
        git checkout -- {{ quote(path) }}
        just _log {{ _red }} "VOID: BUILD FAILED (exit $built) — no test result can be read from a stale binary; mutant reverted"
        exit 2
    fi

    test_cmd={{ quote(test_cmd) }}
    [ -z "$test_cmd" ] && test_cmd='just test-python'
    just _log {{ _blue }} "Testing the mutant with: $test_cmd"
    eval "$test_cmd"
    tested=$?

    git checkout -- {{ quote(path) }}
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
        just _log {{ _red }} "REVERT INCOMPLETE — tracked changes remain after reverting {{ path }}:"
        git status --porcelain --untracked-files=no
        exit 4
    fi

    # The source is honest again but the built extension still holds the mutant,
    # so a later bare test run would fail with no cause anywhere in the tree.
    just build
    rebuilt=$?
    if [ "$rebuilt" -ne 0 ]; then
        just _log {{ _red }} "BINARY STILL MUTATED — source reverted but the rebuild failed (exit $rebuilt); run 'just build' before trusting any later test run"
        exit 5
    fi

    # 126 and 127 are the shell saying it never ran the command: found but not
    # executable, and not found at all. Both are non-zero, so without this they
    # read as KILLED — the one word this pipeline treats as proof that a test is
    # real (#2005).
    #
    # Keep an EVEN number of apostrophes in every _log message: _log renders the
    # message inside a single-quoted printf argument, so an odd count leaves
    # that literal unterminated and the recipe dies with "unexpected EOF" and
    # exit 2 — which is also a legitimate VOID code, so it reads exactly like
    # the guard below firing correctly. Measured while writing this guard.
    if [ "$tested" -eq 126 ] || [ "$tested" -eq 127 ]; then
        just _log {{ _red }} "VOID: THE TEST COMMAND NEVER RAN (exit $tested) — 126 means it was found but is not executable, 127 means it was not found at all. A bare test-file path is the usual cause: test_cmd is evaluated as a whole shell command, so pass a runnable one such as: just test-python <file>. No test result can be read from this."
        exit 2
    fi

    if [ "$tested" -ne 0 ]; then
        just _log {{ _green }} "KILLED (test command exit $tested) — confirm it is the failure you predicted"
        exit 0
    fi
    just _log {{ _yellow }} "SURVIVED — the suite passed with the mutant applied. This is a finding, not a pass."
    exit 3

# The Rust path, and it is a different shape from `mutate` above (#2072).
# cargo-mutants generates mutants from the AST and runs each one itself, so
# three failure modes of the anchored applier cannot occur here:
#
#   1. No anchor. There is nothing to match zero or many times, so "the mutant
#      never applied" — which reads exactly like SURVIVED — is not a state.
#   2. No dirty-tree guard needed. cargo-mutants copies the source tree to a
#      scratch directory and mutates the copy, so uncommitted work is never
#      destroyed by a revert. `mutation-guard` is deliberately not a dependency.
#   3. An orphaned binding is reported as `unviable`, not as a VOID this recipe
#      has to distinguish from tooling failure. Measured on this crate:
#      `5 mutants tested in 61s: 2 caught, 3 unviable`.
#
# Scope it. Unfiltered, this crate offers 3607 candidates at roughly one build
# each. `--file` takes a glob; `--re` filters by mutant description.
#
# WHICH SURFACE A VERDICT COVERS (#2113). There is no `mutants.toml` and no
# `.cargo/mutants.toml`, so cargo-mutants runs its default test command,
# `cargo test`. The Python suite never runs. So a MISSED verdict here means
# "no Rust unit test refuses this mutant". It does NOT mean "nothing refuses
# this mutant", and a reader who takes it that way deletes tested behaviour.
#
# For a module whose behaviour is visible end to end only, the verdict carries
# no information at all. Measured on `src/reporter/print.rs`, which writes its
# output with `println!`; five of its six functions return nothing, and no Rust
# test calls the one that returns an `ExitCode`:
#
#      41 mutants tested in 4m: 28 missed, 1 caught, 12 unviable
#
# That is 1 of 29 viable mutants caught, and no test could move it: `cargo test`
# cannot observe what that file does. The four tests that call into it, in
# `src/reporter/options.rs`, assert nothing for the same reason.
#
# So a gate that brings a file into the mutation-tested set must name a surface
# `cargo test` can observe — a function that returns a value, not one that
# prints. Gate G7 of the #2100 audit slate was first specified against
# `src/reporter/print.rs`; it is respecified against `src/reporter/format/`,
# where the same rendering behaviour returns `String`. #2113 carries the worked
# case and the reasoning.
#
# Giving cargo-mutants the Python suite instead was refused, on cost measured
# at #2113: the `cargo test` phase is 3 s, and `just build` plus
# `just test-python` is 44.7 s. cargo-mutants 27.0.0 also offers no arbitrary
# test command — `--test-tool` takes `cargo` or `nextest`, and the config
# schema has no `test_command` key. Reach for `just mutate` instead: its
# default test command is `just test-python`, so the two recipes cover
# opposite surfaces.
#
# The notice below is printed BEFORE cargo mutants, not after: cargo mutants
# exits non-zero on a missed mutant and `just` then abandons the recipe, so a
# trailing line would be skipped on exactly the runs that need it.
#
# Only the line directly above a recipe becomes its `just --list` description.
# Run Rust mutants under cargo-mutants — scope with --file or --re (#2072)
mutate-rust *args: (_log _blue "Running Rust mutants...")
    @just _log {{ _yellow }} "MISSED below means no Rust unit test refuses the mutant. It does not mean nothing refuses it — this recipe runs cargo test only (#2113)."
    cargo mutants {{ args }}

# Full pre-push gate: clean, check, test everything
preflight: clean check-locks check test-rust build test-python
    @just _log {{_blue}} "Running doc example tests..."
    uv run python -m oxitest python/tests/docs/ --strict=off
    @just _log {{_blue}} "Building docs (strict)..."
    uv run --group docs mkdocs build --strict
    mdbook build docs/internals
    cargo doc --no-deps --document-private-items
    @just _log {{_green}} "Preflight passed"

# Format code and fix typos
#
# No paths and no `*args`. `pyproject.toml`'s `[tool.ruff] include` declares
# the linted surface and `just check` reads it the same way, so a path here
# would be a second copy of that declaration — which is how `scripts/` and
# `benchmarks/` came to be checked and never formatted. One argument list
# cannot serve all three tools either: a path breaks `cargo fmt`, and
# `--check` never reaches `codespell`, which then writes. `just check` is
# the check-mode entry point (#2064).
fmt: (_log _yellow "Formatting...")
    ruff format
    cargo fmt
    codespell --toml pyproject.toml --write-changes

# Build all documentation sites
docs-build: (_log _green "Building all docs...")
    uv run --group docs mkdocs build
    mdbook build docs/internals
    cargo doc --no-deps --document-private-items

# Serve docs with live reload (hot-reload on save, but cross-discipline links 404)
docs-serve: (_log _green "Starting doc servers...")
    -cargo doc --no-deps --document-private-items
    uv run --group docs mkdocs serve --dev-addr localhost:8000 &
    mdbook serve docs/internals --port 3000 &
    python3 -m http.server 3001 --directory target/doc &
    @just _log {{_green}} "User docs:      http://localhost:8000"
    @just _log {{_green}} "Internals book: http://localhost:3000"
    @just _log {{_green}} "Rust API docs:  http://localhost:3001/_oxitest"
    @just _log {{_green}} "Stop with: just docs-stop"

# Serve all docs from a single origin (cross-links work, no live reload)
docs-unified: docs-build (_log _green "Starting unified doc server...")
    python3 -m http.server 9000 --directory docs &
    @just _log {{_green}} "All docs:       http://localhost:9000/index.html"
    @just _log {{_green}} "User docs:      http://localhost:9000/site/"
    @just _log {{_green}} "Internals:      http://localhost:9000/internals/book/"
    @just _log {{_green}} "Architecture:   http://localhost:9000/internals/architecture-map.html"
    @just _log {{_green}} "Stop with: just docs-stop"

# Stop all background doc servers
docs-stop: (_log _red "Stopping doc servers...")
    -pkill -f "mkdocs serve"
    -pkill -f "mdbook serve"
    -pkill -f "http.server 3001"
    -pkill -f "http.server 9000"

# Remove build artifacts
clean: (_log _red "Removing build artifacts...")
    cargo clean
    rm -f python/oxitest/_oxitest*.so

# Run hyperfine benchmarks
bench: (_log _blue "Running benchmarks...")
    bash benchmarks/run.sh

# Print speedup summary against baseline
bench-compare: (_log _blue "Comparing benchmarks...")
    python benchmarks/compare.py

# Check that all required tools are on PATH
health:
    #!/usr/bin/env bash
    missing=0
    for cmd in cargo uv maturin ruff ty mkdocs mdbook codespell python3; do
        if command -v "$cmd" > /dev/null 2>&1; then
            printf '  ✓ %s (%s)\n' "$cmd" "$(command -v "$cmd")"
        else
            printf '  ✗ %s NOT FOUND\n' "$cmd"
            missing=$((missing + 1))
        fi
    done
    if [ "$missing" -gt 0 ]; then
        printf '\n%d tool(s) missing\n' "$missing"
        exit 1
    else
        printf '\nAll tools available\n'
    fi

    printf '\n'
    just agent-health

# Check that required agent skills are installed (warnings only)
agent-health:
    #!/usr/bin/env bash
    skills_file="docs/agents/required-skills.txt"
    if [ ! -f "$skills_file" ]; then
        printf '  SKIP: %s not found\n' "$skills_file"
        exit 0
    fi

    found_agent=0
    for agent in claude codex; do
        if command -v "$agent" > /dev/null 2>&1; then
            printf '  \033[32m✓\033[0m %s (%s)\n' "$agent" "$(command -v "$agent")"
            found_agent=1
        fi
    done

    if [ "$found_agent" -eq 0 ]; then
        printf '  No agent detected — skipping skill checks\n'
        exit 0
    fi

    missing=0
    checked=0
    if command -v claude > /dev/null 2>&1; then
        printf '\n  Claude Code skills:\n'
        while IFS= read -r skill || [ -n "$skill" ]; do
            skill=$(echo "$skill" | sed 's/#.*//' | xargs)
            [ -z "$skill" ] && continue
            checked=$((checked + 1))
            if echo "$skill" | grep -q ':'; then
                plugin=$(echo "$skill" | cut -d: -f1)
                if ls -d ~/.claude/plugins/cache/*/"${plugin}" > /dev/null 2>&1; then
                    printf '    \033[32m✓\033[0m %s\n' "$skill"
                else
                    printf '    \033[33mWARN\033[0m %s not installed (plugin: %s)\n' "$skill" "$plugin"
                    missing=$((missing + 1))
                fi
            else
                if [ -e ~/.claude/skills/"$skill" ]; then
                    printf '    \033[32m✓\033[0m %s\n' "$skill"
                else
                    printf '    \033[33mWARN\033[0m %s not installed\n' "$skill"
                    missing=$((missing + 1))
                fi
            fi
        done < "$skills_file"
    fi

    if [ "$checked" -eq 0 ]; then
        printf '\n  No skill checks available for detected agent(s)\n'
    elif [ "$missing" -gt 0 ]; then
        printf '\n  %d skill(s) missing\n' "$missing"
    else
        printf '\n  All required skills available\n'
    fi
