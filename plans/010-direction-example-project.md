# Plan 010: Design an example project showcasing oxitest patterns

> **Executor instructions**: This is a **design/spike plan**. Your
> deliverable is a design document outlining the example project structure
> and content, not a full implementation. The goal is to define what the
> example should teach and how it should be organized.

## Status

- **Priority**: P3
- **Effort**: S (design); M (implementation, separate plan)
- **Risk**: LOW
- **Depends on**: none
- **Category**: direction
- **Planned at**: commit `f60b5a0`, 2026-07-11
- **Issue**: https://github.com/kalonji-tools/oxitest/issues/1397

## Why this matters

Users new to oxitest (especially those migrating from pytest) lack a
curated, runnable example project showing real-world patterns. The README
has quick-start snippets, but they're isolated — there's no "clone this and
run it" experience that demonstrates how fixtures, parametrize, marks,
conftest, helpers, and plugins compose in a realistic project.

oxitest's own test suite dogfoods itself, but it tests the runner's
internals, not user-facing application patterns. A user studying
`python/tests/test_fixtures.py` learns how oxitest validates fixtures,
not how to write fixtures for their own app.

## Current state

- README has 4 quick-start code blocks (basic test, fixture, parametrize, marks)
- Docs have tutorials at `docs/user/tutorials/getting-started/`
- No `examples/` directory
- No sample project showing multi-file conftest/fixture/helper/plugin composition
- Test suite in `python/tests/` is self-referential (tests oxitest itself)

## Investigation steps

### Step 1: Survey what patterns need showcasing

List the oxitest features a user needs to learn, ordered by frequency of use:

1. Basic test functions (`def test_*`)
2. Fixtures with `Fixture[T]` annotation
3. Fixture scopes (`each`, `shared`, `session`)
4. Parametrize with dataclass cases
5. Parametrize composition with `partial()`
6. Marks (`skip`, `xfail`, `timeout`, custom)
7. Conftest hierarchy (root + subdirectory conftests)
8. Helpers via `Helpers()` registry
9. Built-in fixtures (`TempDir`, `StdCapture`, `Patcher`, `LogCapture`, `TestContext`)
10. Strict mode and assertion messages
11. Async test support
12. Simple plugin (fixture provider)

### Step 2: Choose a sample application domain

Pick a domain small enough to fit in `examples/` but rich enough to
exercise all patterns. Candidates:

- **Todo API** — CRUD operations, database fixture, parametrized validation
- **Calculator library** — pure functions, parametrized math, error cases
- **File processor** — TempDir usage, StdCapture for CLI, file fixtures

Recommend the simplest domain that covers the most patterns.

### Step 3: Design the directory structure

Propose an `examples/` layout. Consider:

```
examples/
  todo_app/
    todo.py            — the "application" (minimal, just enough to test)
    conftest.py        — root fixtures (database session, etc.)
    tests/
      conftest.py      — test-level fixtures
      test_create.py   — basic tests + fixture injection
      test_validate.py — parametrize with dataclass cases
      test_errors.py   — marks (xfail, skip), oxi.raises()
      test_cli.py      — StdCapture, TempDir usage
    pyproject.toml     — minimal [tool.oxitest] config
    README.md          — what this example teaches
```

### Step 4: Define what each file teaches

For each file in the example, list:
- Which oxitest feature it demonstrates
- What the user should learn from it
- Any comments/docstrings needed to guide the reader

### Step 5: Identify open questions

- Should the example be a standalone project with its own `pyproject.toml`, or part of oxitest's test suite?
- Should it be tested in CI (to prevent staleness)?
- Should it be linked from the docs tutorial, or be a separate "Examples" section?

## Deliverable

A design document (comment on the GitHub issue) with:
1. Chosen domain and rationale
2. Directory structure
3. Per-file feature mapping
4. Open questions with recommendations
5. Estimated implementation effort

## Done criteria

- [ ] Feature list to showcase documented
- [ ] Domain chosen with rationale
- [ ] Directory structure proposed
- [ ] Per-file feature mapping written
- [ ] Open questions listed with recommendations
- [ ] Design posted as comment on the GitHub issue
