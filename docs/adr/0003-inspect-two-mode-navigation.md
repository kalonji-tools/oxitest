# ADR-0003: Two-mode inspect navigation with progressive disclosure

**Status:** Accepted
**Date:** 2026-06-30

The initial inspect TUI was a generic graph browser with a hierarchical navigation model: Home (menu of 6 node kinds) -> NodeList (all nodes of one kind) -> NodeDetail (single node). This model didn't serve either of the two real user workflows — understanding an unfamiliar test suite (cartographic) or diagnosing a specific fixture/test issue (diagnostic). The mandatory intermediate list screen added friction without value, and the home screen showed counts that `oxitest query --count` already provides. We replaced it with a two-mode model driven by progressive disclosure.

## Considered Options

1. **Keep the hierarchical browser, improve the detail views.** Would address content quality but not the navigation problem. Users still have to navigate through a list to reach any node, and the home screen still offers no insight into the shape of the test suite. More features (bookmarks, search scopes, source view) would layer complexity onto a foundation that doesn't match how developers actually approach their test infrastructure.

2. **Two-mode navigation: Overview (cartographic) and Node Focus (diagnostic).** Entry mode determined by invocation — `oxitest inspect` lands on the cartographic overview showing curated sections (Fixture Gravity, Marks, Conftests, Signals); `oxitest inspect <name>` jumps straight to diagnostic Node Focus. Navigation between nodes is edge-following rather than hierarchy-traversing. Four-tier progressive disclosure (at-a-glance in edge list, preview pane on cursor, navigate-in for full node, source view for code) replaces the binary list-or-detail model.

3. **Separate subcommands for each mode.** `oxitest map` for cartographic, `oxitest inspect` for diagnostic. Would make the modes explicit but splits the graph infrastructure across two UX surfaces. Users would need to learn two tools and wouldn't naturally transition from "I'm exploring" to "I found something interesting, let me dig in."

## Decision

Option 2. The two-mode model serves both workflows with a single entry point, and the progressive disclosure reduces the friction of graph exploration.

### Modes

- **Cartographic** (`oxitest inspect`, no args): Overview landing screen showing curated sections that reveal the shape and hotspots of the test suite. Entry point for understanding unfamiliar test infrastructure.
- **Diagnostic** (`oxitest inspect <name>`): Direct jump to a specific node. Entry point for fixture wiring diagnosis, blast radius assessment, and refactor planning.

### Layout

- **Two-pane, tiled adaptive**: left pane is the selectable item list (overview sections or node edges), right pane is the preview of the cursor-selected item. Split ratio adapts to preview content length — short preview gives left pane more space, long preview grows the right pane.
- **Header**: breadcrumb trail showing the navigation path (`overview > F db_session > F db_engine`). Truncates from the left with `...` when too long.
- **Footer**: contextual keybinding hints on the left (change based on current screen/mode), live status on the right (loading indicator, node counts, search match counts).
- **Narrow terminals** (< 80 cols): preview pane disappears, single-pane experience. Graceful degradation — two-tier disclosure (glance -> navigate in) instead of three.

### Progressive Disclosure (four tiers)

1. **At a glance** — one line in the edge list: sigil + name + one contextual metadata (marks for tests, scope for fixtures, fixture count for conftests).
2. **Preview** — right pane shows key properties and top edges of the cursor-selected item. Updates automatically as cursor moves.
3. **Navigate in** (`Enter`) — the node becomes your focus. Left pane shows full properties + all edge groups. Right pane previews whatever edge your cursor is on. This IS the detail view — no separate detail mode.
4. **Source** — `s` shows read-only syntax-highlighted source in-TUI. `e` opens in `$EDITOR`/`$VISUAL` at the correct line (inspect suspends, resumes on editor exit). Only available for nodes with source code (fixtures, tests, helpers). Hint hidden from footer when unavailable; flash message if pressed on unsupported node.

### Navigation

- **Edge-following**: from any focused node, edges are listed and followable. `Enter` navigates to the target node, building a trail. `Back` pops the trail. No mandatory intermediate list screens.
- **Flat cursor**: arrow keys move freely across all sections/edge groups. Sections are visual grouping only — no tab-between-sections mode.
- **History** (`H`): session journal of all visited nodes (including ones backed out of). Separate from the breadcrumb trail, which only shows the current path.
- **Refresh** (`r`): manual graph refresh. No automatic refresh on $EDITOR return. Covers both post-edit and external changes.

### Overview Sections (fixed order)

1. **Fixture Gravity** — fixtures ranked by consumer count (phase 2).
2. **Marks** — all marks with test counts (phase 1).
3. **Conftests** — conftest files with fixture/helper counts (phase 1 for helpers, phase 2 for fixtures).
4. **Signals** — graph-derived diagnostics (phase 2): unused fixtures, unused helpers, broken edges, high fan-in fixtures, deep dependency chains, scope mismatches.

Sections requiring phase-2 data show a loading indicator until the background Python session delivers fixture/plugin metadata. Marks and Conftests (helper counts) are available immediately.

### Search

- `/` enters search mode, scoped to current context (e.g., filters a node's consumers).
- `Tab` in search mode toggles between context-scoped and global search.
- DSL auto-detection: queries containing `(`, `&`, `|`, `!` attempt DSL parse, fallback to substring.
- Footer shows match count on the right (e.g., `12 of 47 tests`).

### Pluggability

- **Extension nodes** (v1): plugins provide structured key-value data. Rust graph holds an `Extension` variant. Generic detail renderer for unknown node kinds.
- **InspectSectionProvider** (v1): plugins contribute additional overview sections.
- **InspectFieldProvider** (v1): plugins add extra fields/edges to existing node kinds.
- **InspectViewProvider** (deferred): full view replacement. Design goal but not v1 scope.

### Keybindings

| Key | Action | Mode |
|-----|--------|------|
| `Up` / `k` | Move cursor up | Normal |
| `Down` / `j` | Move cursor down | Normal |
| `Enter` / `l` / `Right` | Navigate into / follow edge | Normal |
| `Left` / `h` / `Backspace` | Back (pop trail) | Normal |
| `/` | Enter search (context-scoped) | Normal |
| `?` | Toggle help overlay | Normal |
| `H` | Open history | Normal |
| `s` | Source view (in-TUI) | Normal |
| `e` | Open in $EDITOR | Normal |
| `r` | Refresh graph data | Normal |
| `q` | Quit | Normal |
| `Ctrl+C` | Force quit | Always |
| `Esc` | Clear search / close overlay | Context |
| `Enter` | Accept search selection | Search |
| `Up` / `Down` | Navigate search results | Search |
| `Backspace` | Delete character | Search |
| `Tab` | Toggle context/global scope | Search |

## Consequences

- The Home -> NodeList -> NodeDetail navigation stack is replaced. NodeList as a mandatory intermediate screen is removed — lists become a plugin-providable view, not a core navigation layer.
- The `nav.rs` navigation stack simplifies from five screen types (Home, NodeList, NodeDetail, Disambiguation, History) to a trail of node references with Overview as the root.
- Preview pane rendering is new work — node views need a compact variant for the preview (fewer fields, truncated edge lists) and an adaptive split calculation.
- Overview sections require new graph queries (fan-out ranking, orphan detection, chain depth, scope mismatch detection) that don't exist yet.
- The extension node mechanism requires a new `Extension` variant in the graph node types and a generic detail renderer.
- Source view requires syntax highlighting in-TUI (e.g., `syntect` crate) and process suspension/resumption for $EDITOR integration.
- Phase 2 remains Python-dependent — fixture type resolution (`get_type_hints`), decorator parameter extraction, and plugin introspection all require Python runtime. The two-phase progressive loading architecture is the right design for this constraint.

## Amendments

### Amendment 1 — the fixture model this ADR describes was replaced (2026-08-11)

**Issue:** [#1722](https://github.com/kalonji-tools/oxitest/issues/1722). Amends the Overview Sections list and records where the autouse view landed. The Decision itself stands: two modes, the trail, the preview pane, and the progressive-loading split are all unchanged.

Three statements above describe a fixture model that [#1720](https://github.com/kalonji-tools/oxitest/issues/1720) and [#1788](https://github.com/kalonji-tools/oxitest/issues/1788) retired. They are left as written, per this repository's convention that an ADR keeps its original words and is corrected by amendment.

1. **Overview section 3 is "Declarations", not "Conftests".** `conftest.py` is no longer a fixture home; a declaration is a `__fixtures__.py`, an `__init__.py`, or an inline declaration in a test module (ADR-0009 Rule 5). The node kind is `Declaration` and its sigil is `D`.
2. **"unused helpers" is not a signal.** The helper concept was retired entirely by #1788; there are no helpers to be unused.
3. **The section is fed by a call the surface has to make.** `FixtureSession::new` builds an empty session, so `inspect` saw only builtins until #1722 had `spawn_phase2` call `register_declaration_homes_for_files`. Any future surface that builds its own session inherits this obligation.

**The autouse view is a section, not a screen.** ADR-0009 Rule 7 cites `oxitest inspect` as its answer to the invisibility objection against autouse. That view shipped as an `Autouse (applies here)` section inside the Test node's `NodeFocus` detail, rather than as a sixth screen kind. `Screen` therefore keeps its four variants and the keybinding table above is unchanged — the alternative would have spent a new screen, a new key and a breadcrumb entry on what is one list.

**The keybinding table is accurate and `CONTEXT.md` was the document that had drifted**, missing `s` and `e`; #1722 repaired it there.
