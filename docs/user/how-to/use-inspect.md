# Use oxitest inspect

!!! abstract "How-to"
    Explore your test suite interactively — browse tests, fixtures, marks,
    conftests, plugins, and helpers in a terminal UI without running any tests.

## Overview

`oxitest inspect` opens a ratatui-based TUI that lets you navigate all six node
kinds in your project:

| Kind | Sigil | What it represents |
|------|-------|--------------------|
| Test | `T` | Collected test functions |
| Fixture | `F` | Registered fixtures from conftests and plugins |
| Mark | `M` | Marks used across test files |
| Conftest | `C` | Conftest files and their fixture/helper ownership |
| Plugin | `P` | Registered plugins and the protocols they implement |
| Helper | `H` | Conftest helper namespaces |

The TUI starts instantly because instant-tier data (tests, marks, helpers) is
extracted from the Rust AST before any Python session starts. Fixtures and
plugins load in the background and appear automatically when ready.

Press `q` or `Esc` to quit. Press `?` to toggle the in-app help overlay.

## Launching with filters

### Open with no filter

```console
$ oxitest inspect
```

Opens the Home screen listing all non-empty node kinds with their counts.

### Jump directly to a node

```console
$ oxitest inspect db_session
```

If exactly one node name contains `db_session`, the TUI opens on the Test or
Fixture list with the cursor on that node. If multiple nodes match, a
disambiguation screen lists all matches.

### Filter by DSL expression

```console
$ oxitest inspect -E 'mark(slow)'
```

Only tests matching the query DSL expression are loaded into the graph. Fixture
and conftest data is still fully loaded — only the test set is narrowed.

### Show only previously-failed tests

```console
$ oxitest inspect --lf
```

Loads the test cache and limits the test graph to tests that failed in the last
run. Useful for focusing on regressions.

### Limit to affected tests

```console
$ oxitest inspect --affected=main
```

Narrows the test files to those affected by git changes relative to `main`
before building the graph. Use bare `--affected` to use the `affected_base`
config value.

### Combine filters

```console
$ oxitest inspect -E 'mark(slow)' --affected=HEAD
```

Filters are applied in order: `--affected` narrows test files first, then `-E`
filters the surviving entries, then `--lf` (if given) applies last.

## Navigation model

The TUI uses a stack-based navigation model. There are four screens:

```
Home → NodeList → NodeDetail
         ↑
    Disambiguation (when a direct-jump name matches multiple nodes)
```

- **Home** — lists the six node kinds with sigil, display name, and count.
  Only kinds with at least one node are shown.
- **NodeList** — lists all nodes of the selected kind. Parametrized tests are
  collapsed by default (see [Parametrized test collapsing](#parametrized-test-collapsing)).
- **NodeDetail** — shows fields and connections for a single node.
- **Disambiguation** — shown when a `NAME` argument matches multiple nodes;
  navigate and press `Space` to select one.

Navigate forward with `Space`, `l`, or the right arrow key. Go back with
`Backspace` or the left arrow key. `Backspace` at the Home screen has no
effect (Home is always the bottom of the stack).

## Home screen

The Home screen shows the six node kinds in a fixed display order:

1. Tests
2. Fixtures
3. Marks
4. Conftests
5. Plugins
6. Helpers

Kinds with zero nodes are hidden. Use `j`/`k` or the arrow keys to move the
cursor, then press `Space` to enter that kind's list.

## Searching

Press `/` to enter search mode. The footer changes to show the search prompt.

- **Substring match** — typing plain text filters nodes whose names contain the
  query (case-insensitive). For example, `db` matches `db_session` and
  `db_cleanup`.
- **DSL auto-detection** — if the query contains `(`, `&`, or `|`, oxitest
  attempts to parse it as a query DSL expression (e.g. `mark(slow)`,
  `name(~login) & async()`). If the parse fails, the query falls back to
  substring matching.
- **Empty query** — no results are shown; the list is not modified until you
  type.

Press `Esc` to exit search mode and clear the query. Press `Enter` to accept
the current results and return to normal navigation mode (results remain
visible).

### Search DSL reference

The same predicates available in `oxitest run -E` and `oxitest query -E` work
in the inspect search box:

| Predicate | Matches when… |
|-----------|---------------|
| `name(pat)` | Node name contains `pat` (substring, case-insensitive) |
| `mark(name)` | Test has the given mark |
| `async()` | Test or fixture is an async function |
| `source(pat)` | File path contains `pat` |
| `uses(name)` | Test uses fixture `name`, or fixture depends on `name` |

Combine with `&` (and), `|` (or), and `!` (not):

```text
mark(slow) & !source(legacy)
async() | name(integration)
```

## Parametrized test collapsing

In the Tests node list, parametrized test variants are collapsed into a single
group header by default. A group header shows the base function name and the
total variant count.

- Press `Space` on a group header to **expand** it and reveal the individual
  variants.
- Press `Space` again on the header to **collapse** the group.
- Press `Space` on a variant row to open its `NodeDetail` view.

Non-parametrized (standalone) tests are always shown as individual rows.

Example (collapsed):

```
  test_add  (3 variants)
  test_solo
```

Example (expanded):

```
▸ test_add  (3 variants)
    test_add[1+2]
    test_add[3+4]
    test_add[5+6]
  test_solo
```

## Session history

`oxitest inspect` records every node you open in a NodeDetail view during the
session.

Press `h` from any screen to open the **History** screen, which lists visited
nodes in reverse chronological order (most recent first). Navigate with `j`/`k`
and press `Space` to re-open a node's detail view. Press `Backspace` or the
left arrow key to close the History screen without navigating.

## Common workflows

### Find which fixtures a test uses

1. `oxitest inspect` — open the TUI.
2. Navigate to Tests (`Space`), find the test with `/db_test`.
3. Press `Space` on the test to open its detail view.
4. The detail view lists the fixture dependencies and their types.

### Check what marks are used in the project

1. `oxitest inspect` — open the TUI.
2. At the Home screen, navigate to Marks (`j`/`k`), press `Space`.
3. Browse the mark list. Press `Space` on a mark to see which tests use it.

### Explore the fixture dependency chain

1. `oxitest inspect` — open the TUI.
2. Navigate to Fixtures, press `Space`.
3. Find the fixture with `/db_session`, press `Space` to open its detail.
4. The detail view shows which fixtures it depends on and which tests consume it.

### Focus on a slow subset before debugging

```console
$ oxitest inspect -E 'mark(slow)' --lf
```

Opens inspect showing only slow tests that failed in the last run — useful for
prioritising which tests to debug next.

### Re-open a node visited earlier

Press `h` from any screen to open History, navigate to the node, and press
`Space` to re-open its detail view.

## See also

- [CLI reference — `oxitest inspect`](../reference/cli.md#oxitest-inspect) — flag reference
- [Filter tests](filter-tests.md) — query DSL syntax and predicates
- [Use the test cache](use-test-cache.md) — how `--lf` and `--ff` use the cache
- [Run affected tests](run-affected-tests.md) — how `--affected` determines impact
