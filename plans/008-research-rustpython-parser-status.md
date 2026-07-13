# Plan 008: Research rustpython-parser maintenance status and migration options

> **Executor instructions**: This is a **research/spike plan**, not an
> implementation plan. Your deliverable is a written report, not code changes.
> Follow the research steps, document your findings, and report back.
>
> **Drift check (run first)**: `git diff --stat f60b5a0..HEAD -- Cargo.toml src/doctest.rs src/prescan.rs src/bare_asserts.rs src/python_ast.rs src/import_graph.rs src/query/extract.rs src/query/highlight.rs src/query/detail.rs`
> If any in-scope file changed since this plan was written, note it but
> proceed — this is research, not code changes.

## Status

- **Priority**: P2
- **Effort**: S (research only)
- **Risk**: LOW
- **Depends on**: none
- **Category**: migration
- **Planned at**: commit `f60b5a0`, 2026-07-11
- **Issue**: https://github.com/kalonji-tools/oxitest/issues/1395

## Why this matters

oxitest depends on `rustpython-parser = "0.4"` for Python AST parsing. This
crate is used in 8 Rust source files across critical features: prescan
(file-level AST analysis), doctest discovery, bare-assert detection, import
graph construction, and query DSL evaluation. If the crate is abandoned or
unmaintained, oxitest has no path to Python grammar updates (new syntax in
3.13+, 3.14+) or bug fixes.

## Current state

- `Cargo.toml:37`: `rustpython-parser = "0.4"`
- `Cargo.lock:1845-1846`: resolves to `rustpython-parser 0.4.0`
- Sub-dependencies: `rustpython-parser-core 0.4.0`, `rustpython-parser-vendored 0.4.0`
- Used in 8 files:
  - `src/python_ast.rs` — shared AST utilities
  - `src/prescan.rs` — file-level AST prescan
  - `src/doctest.rs` — `>>>` example extraction
  - `src/bare_asserts.rs` — bare assert detection
  - `src/import_graph.rs` — import dependency graph
  - `src/query/extract.rs` — query DSL extraction
  - `src/query/highlight.rs` — syntax highlighting
  - `src/query/detail.rs` — query detail views

## Research steps

### Step 1: Check crate release history

Visit or fetch: `https://crates.io/crates/rustpython-parser`

Document:
- Latest version and release date
- Release cadence (how often are versions published?)
- Number of downloads (recent activity signal)

### Step 2: Check upstream repository status

Visit or fetch the RustPython GitHub repository.

Document:
- Is the repo archived?
- Last commit date on main/default branch
- Open issues and PRs count
- Any announcement about maintenance status or handoff

### Step 3: Check Python version support

Determine which Python grammar versions `rustpython-parser 0.4.0` supports.

Document:
- Does it parse Python 3.12 syntax? (e.g., `type` statement, PEP 695)
- Does it parse Python 3.13 syntax? (e.g., `except*` improvements)
- Does it parse Python 3.14 syntax?
- Are there known parsing failures in oxitest's CI? (Check recent CI logs)

### Step 4: Identify alternatives

Search for alternative Rust crates that parse Python AST:
- `ruff_python_parser` (from the Ruff project — actively maintained)
- Any other maintained Python parser crates on crates.io

For each alternative, document:
- API compatibility with rustpython-parser's `ast` module
- License
- Maintenance status
- Migration effort estimate (how much of oxitest's 8 files would need changes?)

### Step 5: Assess migration urgency

Based on findings, recommend one of:
- **No action needed** — crate is maintained, Python grammar support is adequate
- **Monitor** — crate is slowing down but functional; revisit in 6 months
- **Plan migration** — crate is abandoned or missing critical grammar; estimate effort and recommend target

## Deliverable

A written report (comment on the GitHub issue) with:
1. Crate status summary (maintained / slowing / abandoned)
2. Python version grammar support matrix
3. Alternative crates comparison table
4. Migration urgency recommendation
5. If migration is recommended: estimated effort (S/M/L) and suggested target crate

## Done criteria

- [ ] Crate release history documented
- [ ] Repository status documented
- [ ] Python version support assessed
- [ ] At least one alternative crate evaluated
- [ ] Recommendation written with evidence
- [ ] Report posted as comment on the GitHub issue

## STOP conditions

- If `rustpython-parser` has released a version newer than 0.4.0 that supports Python 3.13+, document it and recommend upgrading (this changes the plan from "research migration" to "upgrade dependency").
- If the RustPython project has been archived with no maintained fork, escalate urgency to P1.
