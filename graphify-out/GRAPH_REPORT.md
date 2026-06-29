# Graph Report - oxitest.feat-inspect-detail-views  (2026-06-29)

## Corpus Check
- 344 files · ~309,578 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 127 nodes · 267 edges · 14 communities (8 shown, 6 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS · INFERRED: 1 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `7dc0f44e`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]

## God Nodes (most connected - your core abstractions)
1. `render_detail()` - 21 edges
2. `InspectGraph` - 15 edges
3. `Line` - 12 edges
4. `render_fixture()` - 11 edges
5. `render_test()` - 11 edges
6. `main_layout()` - 10 edges
7. `SearchState` - 9 edges
8. `section_header()` - 9 edges
9. `render_plugin()` - 9 edges
10. `render_helper()` - 9 edges

## Surprising Connections (you probably didn't know these)
- `render_to_string()` --calls--> `draw()`  [INFERRED]
  src/inspect/detail.rs → src/inspect/ui.rs

## Import Cycles
- 1-file cycle: `src/inspect/app.rs -> src/inspect/app.rs`
- 1-file cycle: `src/inspect/detail.rs -> src/inspect/detail.rs`
- 1-file cycle: `src/inspect/mod.rs -> src/inspect/mod.rs`

## Communities (14 total, 6 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.13
Nodes (21): InputMode, InspectApp, new_app_has_empty_search_state(), new_app_starts_in_normal_mode(), search_state_select_next_wraps(), search_state_select_on_empty_is_noop(), search_state_select_prev_wraps(), search_state_selected_returns_current() (+13 more)

### Community 1 - "Community 1"
Cohesion: 0.12
Nodes (19): Frame, build_footer(), build_tree_content(), draw(), draw_help_overlay(), layout_boundary_100_is_two_panes(), layout_boundary_79_is_single_pane(), layout_boundary_80_is_two_panes() (+11 more)

### Community 2 - "Community 2"
Cohesion: 0.37
Nodes (19): BrokenEdge, bool_field(), broken_edge_line(), broken_edges_for(), connection_line(), field_line(), render_conftest(), render_detail() (+11 more)

### Community 3 - "Community 3"
Cohesion: 0.15
Nodes (5): conftest_graph(), render_detail_conftest_shows_fixtures_and_helpers(), render_detail_none_shows_placeholder(), render_detail_test_parametrized_shows_variants(), test_parametrized_graph()

### Community 4 - "Community 4"
Cohesion: 0.44
Nodes (8): Config, build_graph(), run(), InspectArgs, Box, Error, InspectGraph, Result

### Community 5 - "Community 5"
Cohesion: 0.43
Nodes (8): restore_terminal(), setup_terminal(), Box, CrosstermBackend, Error, Result, Stdout, Terminal

### Community 6 - "Community 6"
Cohesion: 0.33
Nodes (6): header_style(), label_style(), sigil_style(), value_style(), warning_style(), Style

### Community 7 - "Community 7"
Cohesion: 0.67
Nodes (3): render_to_string(), InspectApp, String

## Knowledge Gaps
- **12 isolated node(s):** `String`, `Vec`, `Stdout`, `Result`, `Box` (+7 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `render_to_string()` connect `Community 7` to `Community 1`, `Community 3`?**
  _High betweenness centrality (0.247) - this node is a cross-community bridge._
- **Why does `draw()` connect `Community 1` to `Community 7`?**
  _High betweenness centrality (0.243) - this node is a cross-community bridge._
- **What connects `String`, `Vec`, `Stdout` to the rest of the system?**
  _12 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.13227513227513227 - nodes in this community are weakly interconnected._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.1225071225071225 - nodes in this community are weakly interconnected._