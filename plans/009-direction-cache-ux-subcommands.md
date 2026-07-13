# Plan 009: Design cache UX — clear/info subcommands

> **Executor instructions**: This is a **design/spike plan**. Your
> deliverable is a design document with open questions resolved, not a full
> implementation. Follow the investigation steps, prototype if useful, and
> produce a spec suitable for a follow-up implementation plan.

## Status

- **Priority**: P3
- **Effort**: S–M (design); M (implementation, separate plan)
- **Risk**: LOW
- **Depends on**: none
- **Category**: direction
- **Planned at**: commit `f60b5a0`, 2026-07-11
- **Issue**: https://github.com/kalonji-tools/oxitest/issues/1396

## Why this matters

oxitest has a timing cache (`--lf`, `--ff`, `cache_max_age` config) that
stores test results for scheduling decisions. Users can't inspect what's
cached, clear stale data, or understand cache invalidation rules. When
`--lf` returns unexpected results across branches or after dependency changes,
there's no diagnostic path.

Pip has `pip cache info`/`pip cache purge`. Cargo has `cargo clean`. pytest
has `--cache-clear` and `--cache-show`. oxitest has none of these — the
cache is a black box.

## Current state

- Cache implementation: `src/cache/` module (6 files: `mod.rs`, `module.rs`, `outcome.rs`, `serde.rs`, `test_helpers.rs`, `timing.rs`)
- Cache location: `.oxitest_cache/` in project root (per `.gitignore`)
- Config: `cache_max_age` in `[tool.oxitest]` (pyproject.toml:93)
- CLI: `--lf` (last failed), `--ff` (failed first) flags exist
- No subcommand for cache management
- Existing subcommands: `run` (default), `debug`, `query`, `inspect`, `env`

## Investigation steps

### Step 1: Map current cache structure

Read `src/cache/mod.rs` and the 5 submodules. Document:
- What data is stored (timing data, outcome data, both?)
- File format (JSON, binary, other?)
- Cache key strategy (by test node ID? by file path? by content hash?)
- How `cache_max_age` is enforced (on read? on write? periodic sweep?)
- How `--lf`/`--ff` query the cache

### Step 2: Survey prior art

Document how these tools handle cache UX:
- pytest: `--cache-show`, `--cache-clear`, `.pytest_cache/` structure
- cargo: `cargo clean` scope flags
- pip: `pip cache info`, `pip cache purge`, `pip cache list`

Identify the minimal viable surface for oxitest.

### Step 3: Design the subcommand

Propose a `oxitest cache` subcommand with sub-subcommands. Suggested surface:

```
oxitest cache info     — show cache location, size, entry count, max_age
oxitest cache clear    — delete all cached data
oxitest cache show     — list cached entries (test node IDs, timestamps)
```

For each, define:
- Arguments and flags
- Output format (human-readable, JSON with `--json`?)
- Interaction with `cache_max_age` config
- Behavior when cache doesn't exist

### Step 4: Identify open questions

- Should `oxitest cache clear` be available as a `--cache-clear` flag on `oxitest run` too? (pytest does this)
- Should cache be per-branch? (Currently it's per-project — stale across branches)
- Is the cache location configurable? Should it be?

## Deliverable

A design document (comment on the GitHub issue) with:
1. Current cache architecture summary
2. Proposed subcommand surface (commands, flags, output)
3. Open questions with recommended answers
4. Estimated implementation effort for the chosen design

## Done criteria

- [ ] Cache structure documented
- [ ] Prior art surveyed
- [ ] Subcommand design proposed with arguments and output format
- [ ] Open questions listed with recommendations
- [ ] Design posted as comment on the GitHub issue
