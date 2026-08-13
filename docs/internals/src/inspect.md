# Inspect TUI

This chapter covers the internals of `oxitest inspect`, a ratatui-based terminal UI for
browsing tests, fixtures, marks, and other collected metadata. The design rationale is
documented in [ADR-0003](../../adr/0003-inspect-two-mode-navigation.md); this chapter
focuses on how the code implements that design.

## Overview

`oxitest inspect` serves two workflows via a single entry point:

- **Cartographic** (`oxitest inspect`, no args) -- lands on an overview screen showing
  curated sections (Fixture Gravity, Marks, Conftests, Signals) that reveal the shape of
  the test suite.
- **Diagnostic** (`oxitest inspect <name>`) -- jumps directly to a specific node or, when
  multiple nodes match, shows a disambiguation screen.

Both modes share the same graph, navigation model, and progressive loading infrastructure.

### Progressive loading

The inspect graph is built in two phases so the TUI can start immediately without waiting
for a Python session:

- **Phase 1 (instant-tier):** Rust AST extraction produces tests and marks
  synchronously before the TUI starts. Startup filters (`--affected`, `-E`, `--lf`) are
  applied during this phase.
- **Phase 2 (Python-tier):** A background thread initializes a `FixtureSession`, collects
  fixture entries, plugin entries, and test-to-fixture dependency edges, then sends the
  data through an `mpsc` channel. The TUI merges it into the graph when it arrives.

Entry point: `src/inspect/mod.rs:run()`. Phase 1: `build_phase1_graph()`. Phase 2:
`spawn_phase2()`.

## App state

`src/inspect/app.rs:InspectApp` is the top-level state container. Its fields fall into
four groups:

### Core state

| Field | Type | Role |
|-------|------|------|
| `graph` | `Option<InspectGraph>` | The inspect graph. `None` only during error paths. |
| `nav` | `Trail` | Trail-based navigation stack (ADR-0003). |
| `should_quit` | `bool` | Set by `q`, `Esc`, or `Ctrl+C` to exit the event loop. |
| `input_mode` | `InputMode` | `Normal` or `Search { query }`. |

### UI state

| Field | Type | Role |
|-------|------|------|
| `terminal_width` | `u16` | Current terminal width, updated each frame. |
| `scroll_offset` | `u16` | Vertical scroll offset for the left pane. |
| `show_help` | `bool` | Whether the `?` help overlay is visible. |
| `flash_message` | `Option<(String, Instant)>` | Auto-clearing flash message (2-second TTL). |
| `overview_sections` | `OverviewSections` | Pre-sorted overview data derived from the graph. |

### Search and history

| Field | Type | Role |
|-------|------|------|
| `search` | `SearchState` | Query string, matched results, selection cursor, scope mode. |
| `history` | `SessionHistory` | Append-only list of visited nodes (most recent first). |

### Loading and refresh

| Field | Type | Role |
|-------|------|------|
| `phase2` | `Phase2State` | `Loading { rx, started }` or `Complete`. |
| `phase2_timeout` | `Duration` | Deadline for phase-2 data (default 30s). |
| `rootdir` | `String` | Project root, used to relativize paths. |
| `refresh_args` | `Option<RefreshArgs>` | Stored `InspectArgs` + `Config` for the `r` key. |

### Source view

| Field | Type | Role |
|-------|------|------|
| `source_view` | `Option<SourceViewState>` | Active when `s` is pressed. Holds file path, rendered lines, scroll offset. |
| `open_in_editor_request` | `Option<NodeRef>` | Set by `e`, consumed in the event loop after `handle_key` returns. |

The editor request is consumed outside `handle_key` because terminal restore/setup must
bracket the editor subprocess, which cannot happen inside the key handler.

### Key enums

- **`InputMode`** -- `Normal` (navigation keys) or `Search { query }` (keystrokes
  append to query).
- **`ScopeMode`** -- `Context` (search current screen's nodes) or `Global` (search all
  nodes). Toggled with `Tab` in search mode.
- **`Phase2State`** -- `Loading { rx, started }` (background thread active) or `Complete`
  (all data loaded, timed out, or thread disconnected).

## Trail-based navigation

`src/inspect/nav.rs` implements ADR-0003's navigation model.

### Screen enum

Four variants represent the possible screens:

| Variant | Left pane content | Cursor domain |
|---------|-------------------|---------------|
| `Overview { selected }` | Curated overview sections | Flat index across gravity + marks + conftests + signals |
| `NodeFocus { node, selected }` | Full node properties + selectable edge list | Index within the node's edges |
| `Disambiguation { query, matches, selected }` | List of nodes matching a direct-jump name | Index within matches |
| `History { selected }` | Previously visited nodes (most recent first) | Index within history entries |

### Trail

`Trail` is a `Vec<Screen>` with an invariant: `screens[0]` is always
`Screen::Overview { selected: 0 }`. The root can never be popped.

- `push(screen)` -- adds a screen on top.
- `pop()` -- removes the top screen unless at root. Returns `false` at root.
- `current()` / `current_mut()` -- the top screen.
- `breadcrumb(graph)` -- produces `(sigil, label)` pairs for the header.
  `Disambiguation` and `History` are skipped (transient overlay screens).

### Direct jump resolution

`resolve_direct_jump(graph, name)` handles `oxitest inspect <name>`:

- 0 matches: Trail stays at Overview (depth 1).
- 1 match: Trail pushes `NodeFocus` (depth 2).
- N matches: Trail pushes `Disambiguation` (depth 2).

Matching is case-insensitive substring on `graph.node_name()`.

## Graph builder

`src/inspect/graph/` holds the data model and construction logic.

### InspectGraph

Five typed vectors, one per node kind:

| Field | Node struct | Sigil | Identity field |
|-------|-------------|-------|----------------|
| `tests` | `TestNode` | `T` | `node_id` |
| `fixtures` | `FixtureNode` | `F` | `name` |
| `marks` | `MarkNode` | `M` | `name` |
| `conftests` | `ConftestNode` | `C` | `path` |
| `plugins` | `PluginNode` | `P` | `name` |

`NodeRef` is a lightweight handle: `{ kind: NodeKind, index: usize }`. All navigation,
search, and rendering use `NodeRef` to reference nodes without borrowing the graph.

### GraphBuilder

`src/inspect/graph/builder.rs:GraphBuilder` constructs the graph incrementally:

1. **`add_*_entries()`** methods accept `&[QueryEntry]` slices and populate the typed
   vectors. Deduplication is by name via `HashMap` lookup tables.
2. **`resolve_edges()`** wires cross-references after all entries are added:
   - Test-to-mark edges (from the `mark` field in test entries)
   - Fixture-to-conftest edges (by source path)
   - Fixture-to-plugin edges (by `<plugin:name>` source prefix)
   - Parametrize grouping (strip `[param_id]` suffix, set `variants` and `param_count`)
3. **`build()`** consumes the builder and returns the finished `InspectGraph`.

### Progressive merge via `from_graph()`

`GraphBuilder::from_graph(existing)` reconstructs a builder from an existing graph,
repopulating lookup tables. This is the key to progressive loading: phase-1 builds the
initial graph, then `merge_phase2()` calls `from_graph()` to create a new builder, adds
fixture/plugin/dependency entries, re-resolves edges, and replaces the graph.

After merge, navigation is reset to Overview if the current screen is `NodeFocus` or
`Disambiguation`, because node indices may have shifted.

### add_fixture_dep_entries

`add_fixture_dep_entries()` wires test-to-fixture consumer edges from Python-tier data.
It strips the `rootdir` prefix from absolute `test_node_id` values (phase-2 Python data
carries absolute paths; phase-1 relativizes). Edge deduplication uses `AHashSet` for O(1)
lookups.

## Detail rendering

`src/inspect/detail/` renders the right-pane content for focused nodes.

### Module structure

After the split in #1189, the detail module has this layout:

| File | Responsibility |
|------|----------------|
| `detail/mod.rs` | Dispatch: `render_detail()`, `render_preview()`, `collect_selectable_edges()`, `edge_node_at()`, `selectable_edge_count()` |
| `detail/styles.rs` | Shared helpers: `field_line()`, `bool_field()`, `section_header()`, `connection_line()`, `preview_edges()` |
| `detail/test.rs` | `render_test()`, `preview_test()`, `collect_edges()` |
| `detail/fixture.rs` | `render_fixture()`, `preview_fixture()`, `collect_edges()` |
| `detail/mark.rs` | `render_mark()`, `preview_mark()`, `collect_edges()` |
| `detail/conftest.rs` | `render_conftest()`, `preview_conftest()`, `collect_edges()` |
| `detail/plugin.rs` | `render_plugin()`, `preview_plugin()`, `collect_edges()` |

### Three-function pattern

Every per-node-type submodule exports three functions:

1. **`render_*(graph, node_ref) -> Vec<Line>`** -- full detail view. Shows all fields,
   all edge groups, description text.
2. **`preview_*(graph, node_ref) -> Vec<Line>`** -- compact preview for the right pane.
   Shows 2-3 key properties, top 3 edges per group with a "N more" truncation line.
   Omits description and some boolean fields.
3. **`collect_edges(graph, node_ref) -> Vec<NodeRef>`** -- ordered list of selectable
   edge targets for keyboard navigation. The order matches the visual order in
   `render_*`, so the cursor index maps directly to the edge list.

`detail/mod.rs` dispatches to the correct submodule based on `node_ref.kind`.

### Shared styles

`detail/styles.rs` provides rendering primitives used by all per-node submodules:
label/value field lines, section headers, connection lines with sigil prefixes, and
`preview_edges()` which truncates edge lists at a configurable maximum.

## Event loop and input

### InspectApp::run()

`src/inspect/app.rs:InspectApp::run()` is the main loop:

```
loop {
    poll_phase2()           -- non-blocking check for background data
    clear_expired_flash()   -- remove stale flash messages
    update terminal size + scroll offset
    terminal.draw(ui::draw)
    if should_quit: break
    poll for events (50ms timeout)
    match event:
        Key => input::handle_key()
        Mouse => input::handle_mouse()
    handle open_in_editor_request (terminal restore/setup around $EDITOR)
}
```

The 50ms poll timeout keeps the UI responsive while allowing `poll_phase2()` to check
for background data on every iteration.

### poll_phase2()

Non-blocking check on the `mpsc::Receiver`:

- `Ok(data)` -- transitions to `Complete`, calls `merge_phase2()`.
- `Empty` -- stays `Loading`; checks elapsed time against `phase2_timeout`.
- `Disconnected` -- transitions to `Complete` (background thread error path).

### Input dispatch

`src/inspect/input.rs` routes key events through a three-layer dispatch:

1. **Source view intercept** -- when `source_view` is `Some`, only source-view keys
   (`Esc`/`q` to close, `j`/`k` to scroll, `e` to open editor) are handled.
2. **`handle_normal_key()`** -- navigation (`j`/`k`/`h`/`l`/Enter/Backspace), search
   entry (`/`), help toggle (`?`), refresh (`r`), source view (`s`), editor (`e`),
   history (`H`), quit (`q`/`Esc`/`Ctrl+C`).
3. **`handle_search_key()`** -- character append, backspace, `Tab` to toggle scope,
   `Enter` to accept, `Esc` to cancel, arrow keys to navigate results.

### Search

`src/inspect/search.rs` implements a dual-mode search:

- **DSL auto-detection:** if the query contains `(`, `&`, or `|`, attempt to lex/parse
  it as a query DSL expression and evaluate against `node_query_entry()`. Falls back to
  substring on parse failure.
- **Substring fallback:** case-insensitive substring match on `graph.node_name()`.

Search scope is determined by `SearchScope::Global` (all nodes) or
`SearchScope::Context(candidates)`. Context candidates are computed from the current
screen: overview items for Overview, edge nodes for NodeFocus, history entries for
History, match list for Disambiguation.

### Source view and $EDITOR

`src/inspect/source.rs` provides:

- `node_source_location(graph, node)` -- extracts file path and line number. Returns
  `None` for marks, plugins, and conftests (no viewable source).
- `read_source_lines(path)` -- reads the file and renders lines with line-number gutters.
- `open_in_editor(path, line)` -- launches `$VISUAL`, `$EDITOR`, or `vi` with `+line`.

The `s` key enters source view (full-screen overlay replacing the two-pane layout). The
`e` key sets `open_in_editor_request`, which the event loop consumes after `handle_key`
returns -- this allows terminal restore before spawning the editor and re-setup after it
exits.

## Layout and rendering

`src/inspect/ui.rs` handles terminal lifecycle and frame drawing.

### Adaptive two-pane layout

`adaptive_layout(width, preview_line_count)` computes left/right pane widths:

- `width < 80`: single-pane mode (right pane width = 0).
- Short preview (< 10 lines): left pane gets more space (up to 55%).
- Long preview (> 20 lines): right pane gets up to 62%.
- Medium preview: equal split.

### Frame structure

`draw()` splits the terminal into three vertical regions:

1. **Breadcrumb header** (1 row) -- trail path as `sigil name > sigil name`.
2. **Main area** (flexible) -- two-pane or single-pane depending on terminal width.
3. **Footer** (1 row) -- contextual keybinding hints, search query/match counts, loading
   indicator.

The left pane content is built by screen-specific functions (`build_overview_content`,
`build_node_focus_content`, `build_disambiguation_content`, `build_history_content`). The
right pane shows `render_preview()` for the cursor-selected item.

### Scroll management

`update_scroll()` is called before `draw()` (since `draw` receives an immutable app
reference). It computes the cursor's line index from `build_left_pane()` and adjusts
`scroll_offset` to keep the cursor visible within the viewport.

## Overview sections

`src/inspect/overview.rs:OverviewSections` holds pre-sorted data for the overview landing
screen. It is rebuilt whenever the graph changes (initial load, phase-2 merge, refresh).

Four sections in fixed order:

1. **Fixture Gravity** -- fixtures with >0 consumers, sorted descending by consumer count.
   Only available after phase 2.
2. **Marks** -- all marks, sorted descending by test count. Available from phase 1.
3. **Conftests** -- all conftests, sorted descending by fixture count. Available from
   phase 2.
4. **Signals** -- graph-derived diagnostics from `detect_signals()`. Only populated when
   fixtures are present (phase 2).

Flat cursor indexing: `item_count()` is the sum of all section lengths.
`node_ref_at(index)` maps a flat cursor index to the correct section and returns the
corresponding `NodeRef`.

## Signals

`src/inspect/signals.rs` detects graph anomalies after the graph is fully built.

### SignalKind enum

| Variant | Condition |
|---------|-----------|
| `UnusedFixtures` | Conftest-defined fixtures with no consumers and `autouse = false`. Builtins and plugin fixtures are excluded. |
| `HighFanIn` | Fixtures consumed by >50% of all tests (threshold: `tests.len() / 2`). Skipped when fewer than 2 tests. |
| `DeepChains` | Reserved -- requires fixture-to-fixture dependency edges (not yet captured). |
| `ScopeMismatches` | Reserved -- requires scope-aware edge traversal (not yet implemented). |

### Signal struct

Each `Signal` has a `kind`, a human-readable `message` (shown in the overview panel), and
an `affected: Vec<NodeRef>` listing the implicated nodes. Navigating into a signal with
Enter follows the affected nodes: single-affected jumps to `NodeFocus`, multi-affected
opens `Disambiguation`.

### detect_signals()

Called once after the graph is fully built (by `OverviewSections::from_graph()`). Returns
an empty `Vec` immediately when the graph has no fixtures -- there is nothing meaningful to
diagnose before phase-2 data arrives. Detectors run in a fixed order so signals appear
consistently regardless of graph construction order.
