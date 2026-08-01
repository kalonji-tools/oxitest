# Use oxitest inspect

!!! abstract "How-to"
    Explore your test suite interactively — browse tests, fixtures, marks,
    conftests, and plugins in a terminal UI without running any tests.

`oxitest inspect` opens a ratatui-based TUI over your project's five built-in
**Inspect Node** kinds:

| Kind | Sigil | What it represents |
|------|-------|--------------------|
| Test | `T` | Collected test functions |
| Fixture | `F` | Registered fixtures from conftests and plugins |
| Mark | `M` | Marks used across test files |
| Conftest | `C` | Conftest files and their fixture ownership |
| Plugin | `P` | Registered plugins and the protocols they implement |

The TUI starts instantly because phase-1 data (tests, marks) is
extracted from the Rust AST before any Python session starts. Phase-2 data
(fixture types, plugin metadata, fixture-derived signals) loads in the
background and appears automatically when ready.

Press `q` to quit. Press `?` to toggle the in-app help overlay.

## Two modes

`oxitest inspect` has two entry modes — the invocation picks which one you
land in:

- **Cartographic** (`oxitest inspect`, no arguments) — lands on the **Overview**
  screen. Use this to understand the shape of an unfamiliar test suite.
- **Diagnostic** (`oxitest inspect <name>`) — jumps directly to **Node Focus**
  on the matched node. Use this when you already know the fixture, test, or
  mark you want to inspect.

If a diagnostic invocation's `<name>` matches multiple nodes, a
**Disambiguation** screen lists all matches; navigate with `j`/`k` and press
`Enter` to focus one. Direct-jump matching is case-insensitive substring.

## Launching with filters

### Open with no filter

```console
$ oxitest inspect
```

Opens the Overview showing the four cartographic sections (see
[Overview](#overview-cartographic-mode) below).

### Jump directly to a node

```console
$ oxitest inspect db_session
```

If exactly one node name contains `db_session`, the TUI opens on Node Focus
for that node. If multiple nodes match, the Disambiguation screen appears.

### Filter by DSL expression

```console
$ oxitest inspect -E 'mark(slow)'
```

Only tests matching the query DSL expression are loaded into the graph.
Fixture and conftest data is still fully loaded — only the test set is
narrowed.

### Show only previously-failed tests

```console
$ oxitest inspect --lf
```

Loads the test cache and limits the test graph to tests that failed in the
last run. Useful for focusing on regressions.

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

## Layout

Both Overview and Node Focus use the same two-pane layout:

- **Left pane** — the selectable item list (Overview sections, or edges of the
  focused node). This is where the cursor lives.
- **Right pane** — the **Preview** of whatever item the cursor is on. It
  updates automatically as the cursor moves; no click, tab, or enter is needed
  to preview.
- **Header** — a breadcrumb of the current navigation path, e.g.
  `overview > F db_session > F db_engine`. It truncates from the left with
  `...` when the path is too long.
- **Footer** — contextual keybinding hints on the left, live status on the
  right (loading indicator, node counts, search match counts).

The split ratio adapts to preview length: a short preview leaves more room for
the item list; a long preview grows the right pane.

## Overview (cartographic mode)

The Overview is the landing screen for `oxitest inspect` with no arguments.
It shows four fixed **Sections** in this order:

1. **Fixture Gravity** — fixtures ranked by consumer count. Reveals which
   fixtures the test suite leans on most. *(phase 2)*
2. **Marks** — every registered mark with the number of tests that carry it.
   *(phase 1)*
3. **Conftests** — every `conftest.py` with its fixture count. *(phase 2)*
4. **Signals** — graph-derived diagnostics: unused fixtures,
   broken edges, high-fan-in fixtures, deep dependency chains, and scope
   mismatches. *(phase 2)*

Phase-1 data is available immediately from the Rust AST scan. Phase-2 data
appears once the background Python session finishes fixture and plugin
introspection — until then, phase-2 sections show a loading indicator in the
footer.

The cursor moves flat across all four sections with `j`/`k` or the arrow keys
— sections are visual grouping, not a tab-between mode. Press `Enter` on any
item to navigate into its Node Focus.

## Node Focus (diagnostic mode)

Node Focus is the detail view for a single Inspect Node — it replaces the old
list-plus-detail split with one screen that shows both.

- **Left pane** — the node's full properties plus its outgoing **Edge**
  groups (e.g., a fixture's consumers; a test's fixture dependencies).
- **Right pane** — the Preview of whichever edge target the cursor is on.

Node Focus uses **edge-following** navigation: press `Enter` (or `l`/`Right`)
on an edge to focus the target node, building a trail. Press `Backspace` (or
`h`/`Left`) to pop the trail and return to the previous focus. There are no
mandatory intermediate list screens — you move directly from one node to
another along its edges.

### Source view

Node Focus exposes the underlying source for nodes that have code (Fixture,
Test):

- **`s`** — show the node's source in-TUI, syntax-highlighted, read-only.
- **`e`** — open the node's source in `$EDITOR` (or `$VISUAL`) at the correct
  line. Inspect suspends while the editor is open and resumes when the editor
  exits.

Source-view keys are hidden from the footer for nodes without source, and
pressing them flashes a "not available" message.

## Preview pane

The Preview shows a compact summary of the cursor-selected item — key
properties and its top edges. It updates automatically as the cursor moves,
so you can browse the whole item list without navigating in. This is the
second tier of the four-tier progressive disclosure:

1. **At a glance** — one line in the left pane (sigil, name, one contextual
   piece of metadata).
2. **Preview** — the right pane summary.
3. **Navigate in** — `Enter` makes the item the focused node.
4. **Source** — `s` (in-TUI) or `e` (`$EDITOR`), for nodes with code.

On terminals narrower than 80 columns the Preview disappears and inspect
falls back to two-tier disclosure (at-a-glance → navigate in).

## Searching

Press `/` to enter search mode. The footer changes to show the search prompt.

- **Substring match** — plain text filters nodes whose names contain the
  query (case-insensitive). For example, `db` matches `db_session` and
  `db_cleanup`.
- **DSL auto-detection** — if the query contains `(`, `&`, `|`, or `!`,
  oxitest attempts to parse it as a query DSL expression (e.g. `mark(slow)`,
  `name(login) & async()`). If the parse fails, the query falls back to
  substring matching.
- **Empty query** — no results are shown; the list is not modified until you
  type.

Search is context-scoped by default: results are limited to the nodes visible
on the current screen (Overview items, or the focused node's edges). Press
`Tab` in search mode to toggle **ScopeMode** between **Context** and
**Global** — Global searches every node in the graph regardless of the
current screen.

Press `Enter` to accept the current results and return to normal navigation
mode (results remain visible). Press `Esc` to clear the search and return to
normal mode.

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

## Session history

`oxitest inspect` records every node you open in Node Focus during the
session, in visit order.

Press `H` (capital) from any screen to open the **History** screen, which
lists visited nodes in reverse chronological order (most recent first).
Navigate with `j`/`k` and press `Enter` to re-focus a node. Press
`Backspace` (or `h`/`Left`) to close History without navigating.

## Refresh

Press `r` at any time to trigger a manual refresh: inspect re-runs file
collection, rebuilds the graph, and re-applies your startup filters (`-E`,
`--affected`, `--lf`). Use this after editing test files (either via `e` or
externally) to pick up the changes without restarting the TUI.

## Parametrized test collapsing

Wherever parametrized test variants appear in a list (Overview signals,
edge groups on a Test's parent, etc.), variants are collapsed into a single
group header by default. A group header shows the base function name and the
total variant count.

- Press `Enter` on a group header to **expand** it and reveal the individual
  variants.
- Press `Enter` again on the header to **collapse** the group.
- Press `Enter` on a variant row to focus its Node Focus.

Non-parametrized (standalone) tests are always shown as individual rows.

Example (collapsed):

```text
  test_add  (3 variants)
  test_solo
```

Example (expanded):

```text
▸ test_add  (3 variants)
    test_add[1+2]
    test_add[3+4]
    test_add[5+6]
  test_solo
```

## Common workflows

### Find which fixtures a test uses

```console
$ oxitest inspect test_creates_user
```

Diagnostic jump lands on Node Focus for the test. Its edge groups list the
fixtures it consumes; move the cursor to preview each fixture, or press
`Enter` to follow the edge and inspect the fixture in turn.

### Check what marks are used in the project

```console
$ oxitest inspect
```

The Overview's **Marks** section lists every mark with its test count.
Move the cursor to a mark to preview its tests, or press `Enter` to focus
the mark node and see the full list.

### Explore the fixture dependency chain

```console
$ oxitest inspect db_session
```

Node Focus opens on the fixture. Its edges list both the fixtures it depends
on and the tests that consume it. Press `Enter` on any edge to walk the
chain; `Backspace` pops back along your trail.

### Focus on a slow subset before debugging

```console
$ oxitest inspect -E 'mark(slow)' --lf
```

Opens inspect showing only slow tests that failed in the last run — useful
for prioritising which tests to debug next.

## Keybindings reference

**Normal mode:**

| Key | Action |
|-----|--------|
| `Up` / `k` | Move cursor up |
| `Down` / `j` | Move cursor down |
| `Enter` / `l` / `Right` | Navigate into / follow edge |
| `Left` / `h` / `Backspace` | Back (pop trail) |
| `/` | Enter search (context-scoped) |
| `?` | Toggle help overlay |
| `H` | Open history |
| `s` | Source view (in-TUI) |
| `e` | Open source in `$EDITOR` / `$VISUAL` |
| `r` | Refresh graph data |
| `q` | Quit |
| `Ctrl+C` | Force quit |
| `Esc` | Clear search / close overlay |

**Search mode:**

| Key | Action |
|-----|--------|
| Characters | Append to query |
| `Backspace` | Delete last character |
| `Up` / `Down` | Navigate search results |
| `Tab` | Toggle ScopeMode (Context ↔ Global) |
| `Enter` | Accept results, return to normal mode |
| `Esc` | Clear search, return to normal mode |

## See also

- [ADR-0003 — Two-mode inspect navigation](../../adr/0003-inspect-two-mode-navigation.md) — design rationale for the two-mode model
- [CLI reference — `oxitest inspect`](../reference/cli.md#oxitest-inspect) — flag reference
- [Filter tests](filter-tests.md) — query DSL syntax and predicates
- [Use the test cache](use-test-cache.md) — how `--lf` and `--ff` use the cache
- [Run affected tests](run-affected-tests.md) — how `--affected` determines impact
