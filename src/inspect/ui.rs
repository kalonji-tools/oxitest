//! Layout and rendering for `oxitest inspect`.

use crossterm::{
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use ratatui::{
    Frame, Terminal,
    backend::CrosstermBackend,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Clear, Paragraph},
};

use std::collections::HashSet;

use super::app::{InputMode, InspectApp, LoadingState, SessionHistory};
use super::detail;
use super::graph::{self, NodeKind};
use super::nav::{HOME_KINDS, NavScreen};

// ── Visible row model for parametrize collapsing ─────────────────────────────

/// A single visible row in the Test NodeList.
///
/// When parametrize collapsing is active, tests sharing a base name
/// (everything before `[`) are grouped.  The group appears as a single
/// collapsed header row or as an expanded sequence of variant rows.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) enum TestRow {
    /// A standalone test (not part of a parametrize group).
    Standalone { index: usize },
    /// A collapsed parametrize group header.
    /// `base_name` is the node_id prefix before `[`.
    /// `indices` lists all graph indices that belong to this group.
    GroupHeader {
        base_name: String,
        indices: Vec<usize>,
    },
    /// An individual variant inside an expanded group.
    Variant { index: usize },
}

/// Build the visible row list for the Test NodeList, accounting for
/// parametrize group collapsing.
///
/// Groups are identified by stripping the `[param_id]` suffix from the
/// `node_id`.  Tests without a `[` bracket are standalone.  A group
/// with `param_count > 1` is collapsed unless its base name is in
/// `expanded_groups`.
pub(super) fn build_test_rows(
    graph: &super::graph::InspectGraph,
    expanded_groups: &HashSet<String>,
) -> Vec<TestRow> {
    let mut rows: Vec<TestRow> = Vec::new();
    let mut seen_groups: HashSet<String> = HashSet::new();

    for (idx, test) in graph.tests.iter().enumerate() {
        if test.param_count > 1 {
            // This test belongs to a parametrize group.
            let base_name = graph::base_test_name(&test.node_id).to_string();

            if !seen_groups.insert(base_name.clone()) {
                // Already processed this group — skip.
                continue;
            }

            let mut all_indices = vec![idx];
            all_indices.extend_from_slice(&test.variants);
            all_indices.sort_unstable();

            // Always emit a group header row (collapsed or expanded).
            rows.push(TestRow::GroupHeader {
                base_name: base_name.clone(),
                indices: all_indices.clone(),
            });

            // If expanded, also emit variant rows below the header.
            if expanded_groups.contains(&base_name) {
                for &variant_idx in &all_indices {
                    rows.push(TestRow::Variant { index: variant_idx });
                }
            }
        } else {
            // Standalone test (not parametrized, or single-variant).
            rows.push(TestRow::Standalone { index: idx });
        }
    }

    rows
}

// ── Terminal lifecycle ───────────────────────────────────────────────────────

/// Set up the terminal for TUI rendering: raw mode, alternate screen, mouse
/// capture.
pub(crate) fn setup_terminal()
-> Result<Terminal<CrosstermBackend<std::io::Stdout>>, Box<dyn std::error::Error>> {
    enable_raw_mode()?;
    let mut stdout = std::io::stdout();
    execute!(
        stdout,
        EnterAlternateScreen,
        crossterm::event::EnableMouseCapture,
    )?;
    let backend = CrosstermBackend::new(stdout);
    let terminal = Terminal::new(backend)?;
    Ok(terminal)
}

/// Restore the terminal to its original state: disable raw mode, leave
/// alternate screen, disable mouse capture.
pub(crate) fn restore_terminal(
    terminal: &mut Terminal<CrosstermBackend<std::io::Stdout>>,
) -> Result<(), Box<dyn std::error::Error>> {
    disable_raw_mode()?;
    execute!(
        terminal.backend_mut(),
        LeaveAlternateScreen,
        crossterm::event::DisableMouseCapture,
    )?;
    terminal.show_cursor()?;
    Ok(())
}

// ── Layout ───────────────────────────────────────────────────────────────────

/// Compute the main pane layout based on terminal width.
///
/// - >= 100 cols: two panes, 38% / 62% split
/// - >= 80 cols:  two panes, 45% / 55% split
/// - < 80 cols:   single pane (left only)
pub(crate) fn main_layout(width: u16, area: Rect) -> Vec<Rect> {
    if width >= 100 {
        Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(38), Constraint::Percentage(62)])
            .split(area)
            .to_vec()
    } else if width >= 80 {
        Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(45), Constraint::Percentage(55)])
            .split(area)
            .to_vec()
    } else {
        vec![area]
    }
}

// ── Drawing ──────────────────────────────────────────────────────────────────

/// Render the full TUI frame: header area, main panes, footer, and any
/// overlays.
pub(crate) fn draw(frame: &mut Frame<'_>, app: &InspectApp) {
    let size = frame.area();

    // Split into [main area, footer].
    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(3), Constraint::Length(1)])
        .split(size);

    let main_area = outer[0];
    let footer_area = outer[1];

    // Main panes
    let panes = main_layout(app.terminal_width, main_area);

    // Left pane — tree browser
    let left_title = pane_title(app);
    let left_block = Block::default()
        .borders(Borders::ALL)
        .title(format!(" {left_title} "));
    let left_text = build_tree_content(app);
    let left_content = Paragraph::new(left_text).block(left_block);
    frame.render_widget(left_content, panes[0]);

    // Right pane — detail view (only if two-pane layout)
    if panes.len() > 1 {
        let right_block = Block::default().borders(Borders::ALL).title(" Detail ");
        let mut detail_lines = match (&app.graph, app.nav.current()) {
            (Some(graph), NavScreen::NodeDetail { node }) => {
                detail::render_detail(graph, Some(node))
            }
            (Some(graph), NavScreen::NodeList { kind, selected }) if *kind == NodeKind::Test => {
                let rows = build_test_rows(graph, &app.expanded_groups);
                match rows.get(*selected) {
                    Some(TestRow::GroupHeader { indices, .. }) => {
                        detail::render_group_detail(graph, indices)
                    }
                    Some(TestRow::Standalone { index } | TestRow::Variant { index }) => {
                        let node_ref = super::graph::NodeRef {
                            kind: NodeKind::Test,
                            index: *index,
                        };
                        detail::render_detail(graph, Some(&node_ref))
                    }
                    None => detail::render_detail(graph, None),
                }
            }
            (Some(graph), NavScreen::History { selected }) => {
                let node = app.history.get(*selected);
                detail::render_detail(graph, node)
            }
            (Some(graph), _) => detail::render_detail(graph, None),
            (None, _) => vec![Line::from("No data loaded")],
        };
        // Append loading indicator when fixture/plugin data is still arriving.
        if app.loading_state == LoadingState::InstantOnly {
            detail_lines.push(Line::from(""));
            detail_lines.push(Line::from(Span::styled(
                "Loading fixture and plugin data...",
                Style::default().fg(Color::DarkGray),
            )));
        }
        let right_content = Paragraph::new(detail_lines).block(right_block);
        frame.render_widget(right_content, panes[1]);
    }

    // Footer
    let footer = build_footer(app);
    frame.render_widget(footer, footer_area);

    // Help overlay
    if app.show_help {
        draw_help_overlay(frame, size);
    }
}

/// Compute the left-pane title based on the current navigation screen.
fn pane_title(app: &InspectApp) -> String {
    match app.nav.current() {
        NavScreen::Home { .. } => "Home".to_string(),
        NavScreen::NodeList { kind, .. } => {
            // Find the display label for this kind.
            HOME_KINDS
                .iter()
                .find(|(k, _)| *k == *kind)
                .map(|(_, label)| label.to_string())
                .unwrap_or_else(|| format!("{kind:?}"))
        }
        NavScreen::NodeDetail { node } => match &app.graph {
            Some(g) => format!("{} {}", node.kind.sigil(), g.node_name(node)),
            None => "Detail".to_string(),
        },
        NavScreen::Disambiguation { query, .. } => {
            format!("Jump: {query}")
        }
        NavScreen::History { .. } => "History".to_string(),
    }
}

/// Build the left pane content based on the current navigation screen.
fn build_tree_content(app: &InspectApp) -> Vec<Line<'static>> {
    let is_loading = app.loading_state == LoadingState::InstantOnly;

    let graph = match &app.graph {
        Some(g) if !g.is_empty() || is_loading => g,
        _ => return vec![Line::from("No data loaded")],
    };

    match app.nav.current() {
        NavScreen::Home { selected } => build_home_content(graph, *selected, is_loading),
        NavScreen::NodeList { kind, selected } => {
            build_node_list_content(graph, *kind, *selected, &app.expanded_groups)
        }
        NavScreen::NodeDetail { node } => {
            let name = graph.node_name(node);
            let sigil = node.kind.sigil();
            vec![
                Line::from(format!(" {sigil}  {name}")),
                Line::from(""),
                Line::from(" (see detail pane)"),
            ]
        }
        NavScreen::Disambiguation {
            matches, selected, ..
        } => build_disambiguation_content(graph, matches, *selected),
        NavScreen::History { selected } => build_history_content(graph, &app.history, *selected),
    }
}

/// Render the Home screen: one line per non-empty node kind.
///
/// When in `LoadingState::InstantOnly`, fixture and plugin counts show
/// "loading..." instead of a number.
fn build_home_content(
    graph: &super::graph::InspectGraph,
    selected: usize,
    is_loading: bool,
) -> Vec<Line<'static>> {
    HOME_KINDS
        .iter()
        .filter(|(kind, _)| {
            // Always show fixture/plugin rows while loading
            if is_loading && is_python_tier(*kind) {
                return true;
            }
            graph.node_count(*kind) > 0
        })
        .enumerate()
        .map(|(idx, (kind, label))| {
            let sigil = kind.sigil();
            if is_loading && is_python_tier(*kind) {
                let text = format!(" {sigil}  {label} (");
                let spans = vec![
                    Span::raw(text),
                    Span::styled("loading...", Style::default().fg(Color::DarkGray)),
                    Span::raw(")"),
                ];
                if idx == selected {
                    Line::from(spans).style(Style::default().fg(Color::Black).bg(Color::Cyan))
                } else {
                    Line::from(spans)
                }
            } else {
                let count = graph.node_count(*kind);
                let text = format!(" {sigil}  {label} ({count})");
                if idx == selected {
                    Line::from(Span::styled(
                        text,
                        Style::default().fg(Color::Black).bg(Color::Cyan),
                    ))
                } else {
                    Line::from(text)
                }
            }
        })
        .collect()
}

/// Render a NodeList screen: one line per node of the given kind.
///
/// For `NodeKind::Test`, parametrized tests are grouped and collapsed
/// by default.  Other kinds render a flat list.
fn build_node_list_content(
    graph: &super::graph::InspectGraph,
    kind: NodeKind,
    selected: usize,
    expanded_groups: &HashSet<String>,
) -> Vec<Line<'static>> {
    if kind == NodeKind::Test {
        return build_test_list_content(graph, selected, expanded_groups);
    }

    let count = graph.node_count(kind);
    let sigil = kind.sigil();
    (0..count)
        .map(|idx| {
            let node_ref = super::graph::NodeRef { kind, index: idx };
            let name = graph.node_name(&node_ref);
            let text = format!(" {sigil}  {name}");
            if idx == selected {
                Line::from(Span::styled(
                    text,
                    Style::default().fg(Color::Black).bg(Color::Cyan),
                ))
            } else {
                Line::from(text)
            }
        })
        .collect()
}

/// Render the Test NodeList with parametrize group collapsing.
fn build_test_list_content(
    graph: &super::graph::InspectGraph,
    selected: usize,
    expanded_groups: &HashSet<String>,
) -> Vec<Line<'static>> {
    let rows = build_test_rows(graph, expanded_groups);

    rows.iter()
        .enumerate()
        .map(|(row_idx, row)| {
            let is_selected = row_idx == selected;
            match row {
                TestRow::Standalone { index } => {
                    let name = graph.node_name(&super::graph::NodeRef {
                        kind: NodeKind::Test,
                        index: *index,
                    });
                    let text = format!(" T  {name}");
                    if is_selected {
                        Line::from(Span::styled(
                            text,
                            Style::default().fg(Color::Black).bg(Color::Cyan),
                        ))
                    } else {
                        Line::from(text)
                    }
                }
                TestRow::GroupHeader { base_name, indices } => {
                    let count = indices.len();
                    let text = format!(" T  {base_name} ({count} variants)");
                    if is_selected {
                        Line::from(Span::styled(
                            text,
                            Style::default().fg(Color::Black).bg(Color::Cyan),
                        ))
                    } else {
                        Line::from(text)
                    }
                }
                TestRow::Variant { index } => {
                    let name = graph.node_name(&super::graph::NodeRef {
                        kind: NodeKind::Test,
                        index: *index,
                    });
                    // Extract just the param_id portion for display.
                    let display = if let Some(bracket_pos) = name.rfind('[') {
                        &name[bracket_pos..]
                    } else {
                        name
                    };
                    let text = format!("     T  {display}");
                    if is_selected {
                        Line::from(Span::styled(
                            text,
                            Style::default().fg(Color::Black).bg(Color::Cyan),
                        ))
                    } else {
                        Line::from(text)
                    }
                }
            }
        })
        .collect()
}

/// Render a Disambiguation screen: one line per matching node.
fn build_disambiguation_content(
    graph: &super::graph::InspectGraph,
    matches: &[super::graph::NodeRef],
    selected: usize,
) -> Vec<Line<'static>> {
    matches
        .iter()
        .enumerate()
        .map(|(idx, node_ref)| {
            let sigil = node_ref.kind.sigil();
            let name = graph.node_name(node_ref);
            let text = format!(" {sigil}  {name}");
            if idx == selected {
                Line::from(Span::styled(
                    text,
                    Style::default().fg(Color::Black).bg(Color::Cyan),
                ))
            } else {
                Line::from(text)
            }
        })
        .collect()
}

/// Render the History screen: one line per visited node, most recent first.
fn build_history_content(
    graph: &super::graph::InspectGraph,
    history: &SessionHistory,
    selected: usize,
) -> Vec<Line<'static>> {
    if history.len() == 0 {
        return vec![Line::from(" No history yet")];
    }
    history
        .entries
        .iter()
        .enumerate()
        .map(|(idx, node_ref)| {
            let sigil = node_ref.kind.sigil();
            let name = graph.node_name(node_ref);
            let text = format!(" {sigil}  {name}");
            if idx == selected {
                Line::from(Span::styled(
                    text,
                    Style::default().fg(Color::Black).bg(Color::Cyan),
                ))
            } else {
                Line::from(text)
            }
        })
        .collect()
}

/// Returns `true` for node kinds that require the Python session (phase 2).
fn is_python_tier(kind: NodeKind) -> bool {
    matches!(kind, NodeKind::Fixture | NodeKind::Plugin)
}

/// Build the footer bar with context-sensitive keybinding hints.
fn build_footer(app: &InspectApp) -> Paragraph<'static> {
    let spans = match &app.input_mode {
        InputMode::Normal => {
            // If search results are active (Enter was pressed), show match count
            if !app.search.results.is_empty() {
                let count = app.search.results.len();
                let total = app.search.total_nodes;
                vec![
                    Span::styled(" q", Style::default().fg(Color::Yellow)),
                    Span::raw(" Quit  "),
                    Span::styled("/", Style::default().fg(Color::Yellow)),
                    Span::raw(" Search  "),
                    Span::styled("?", Style::default().fg(Color::Yellow)),
                    Span::raw(" Help  "),
                    Span::styled("j/k", Style::default().fg(Color::Yellow)),
                    Span::raw(" Navigate  "),
                    Span::raw(format!("{count}/{total} matches")),
                ]
            } else {
                vec![
                    Span::styled(" q", Style::default().fg(Color::Yellow)),
                    Span::raw(" Quit  "),
                    Span::styled("/", Style::default().fg(Color::Yellow)),
                    Span::raw(" Search  "),
                    Span::styled("?", Style::default().fg(Color::Yellow)),
                    Span::raw(" Help  "),
                    Span::styled("j/k", Style::default().fg(Color::Yellow)),
                    Span::raw(" Navigate  "),
                    Span::styled("l", Style::default().fg(Color::Yellow)),
                    Span::raw(" Enter  "),
                    Span::styled("h", Style::default().fg(Color::Yellow)),
                    Span::raw(" History"),
                ]
            }
        }
        InputMode::Search { query } => {
            let count = app.search.results.len();
            let total = app.search.total_nodes;
            let match_info = if query.is_empty() {
                String::new()
            } else {
                format!("  {count}/{total} matches")
            };
            vec![
                Span::styled(" /", Style::default().fg(Color::Yellow)),
                Span::raw(query.to_string()),
                Span::raw(match_info),
                Span::raw("  "),
                Span::styled("Esc", Style::default().fg(Color::Yellow)),
                Span::raw(" Cancel  "),
                Span::styled("Enter", Style::default().fg(Color::Yellow)),
                Span::raw(" Accept  "),
                Span::styled("\u{2191}/\u{2193}", Style::default().fg(Color::Yellow)),
                Span::raw(" Navigate"),
            ]
        }
    };
    Paragraph::new(Line::from(spans)).style(Style::default().bg(Color::DarkGray))
}

/// Draw a centered help overlay listing all keybindings.
fn draw_help_overlay(frame: &mut Frame<'_>, area: Rect) {
    let help_text = vec![
        Line::from(Span::styled(
            " Keybindings ",
            Style::default().fg(Color::Yellow),
        )),
        Line::from(""),
        Line::from(" q / Esc     Quit"),
        Line::from(" j / Down    Move down"),
        Line::from(" k / Up      Move up"),
        Line::from(" l / Right   Navigate into"),
        Line::from(" h           History"),
        Line::from(" Left        Back"),
        Line::from(" Space       Navigate into"),
        Line::from(" Backspace   Back"),
        Line::from(" /           Search"),
        Line::from(" ?           Toggle this help"),
        Line::from(" s           Toggle source view"),
        Line::from(""),
        Line::from(Span::styled(
            " Press ? to close ",
            Style::default().fg(Color::DarkGray),
        )),
    ];

    let help_height: u16 = help_text.len() as u16 + 2; // +2 for borders
    let help_width: u16 = 40;

    // Center the overlay
    let x = area.width.saturating_sub(help_width) / 2;
    let y = area.height.saturating_sub(help_height) / 2;

    let overlay_area = Rect::new(
        area.x + x,
        area.y + y,
        help_width.min(area.width),
        help_height.min(area.height),
    );

    let help_block = Block::default()
        .borders(Borders::ALL)
        .title(" Help ")
        .style(Style::default().bg(Color::Black));
    let help_paragraph = Paragraph::new(help_text).block(help_block);

    frame.render_widget(Clear, overlay_area);
    frame.render_widget(help_paragraph, overlay_area);
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn layout_wide_two_panes() {
        let area = Rect::new(0, 0, 120, 40);
        let panes = main_layout(120, area);
        assert_eq!(
            panes.len(),
            2,
            "terminal width >= 100 should produce a two-pane layout"
        );
    }

    #[test]
    fn layout_medium_two_panes() {
        let area = Rect::new(0, 0, 90, 40);
        let panes = main_layout(90, area);
        assert_eq!(
            panes.len(),
            2,
            "terminal width >= 80 but < 100 should produce a two-pane layout"
        );
    }

    #[test]
    fn layout_narrow_single_pane() {
        let area = Rect::new(0, 0, 60, 40);
        let panes = main_layout(60, area);
        assert_eq!(
            panes.len(),
            1,
            "terminal width < 80 should produce a single-pane layout"
        );
    }

    #[test]
    fn layout_boundary_80_is_two_panes() {
        let area = Rect::new(0, 0, 80, 40);
        let panes = main_layout(80, area);
        assert_eq!(
            panes.len(),
            2,
            "terminal width exactly 80 should produce a two-pane layout"
        );
    }

    #[test]
    fn layout_boundary_100_is_two_panes() {
        let area = Rect::new(0, 0, 100, 40);
        let panes = main_layout(100, area);
        assert_eq!(
            panes.len(),
            2,
            "terminal width exactly 100 should produce a two-pane layout"
        );
    }

    #[test]
    fn layout_boundary_79_is_single_pane() {
        let area = Rect::new(0, 0, 79, 40);
        let panes = main_layout(79, area);
        assert_eq!(
            panes.len(),
            1,
            "terminal width 79 should produce a single-pane layout"
        );
    }

    // ── build_test_rows tests ─────────────────────────────────────────────

    use crate::inspect::graph::InspectGraph;
    use crate::inspect::graph::nodes::TestNode;

    /// Build a graph with parametrized and standalone tests.
    fn parametrized_graph() -> InspectGraph {
        let mut graph = InspectGraph::default();
        graph.tests.push(TestNode {
            node_id: "tests/test_math.py::test_add[1+2]".to_string(),
            is_async: false,
            param_id: Some("1+2".to_string()),
            param_count: 3,
            variants: vec![1, 2],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_math.py::test_add[3+4]".to_string(),
            is_async: false,
            param_id: Some("3+4".to_string()),
            param_count: 3,
            variants: vec![0, 2],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_math.py::test_add[5+6]".to_string(),
            is_async: false,
            param_id: Some("5+6".to_string()),
            param_count: 3,
            variants: vec![0, 1],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_math.py::test_solo".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph
    }

    #[test]
    fn test_rows_collapsed_groups_single_row() {
        let graph = parametrized_graph();
        let expanded = HashSet::new();
        let rows = build_test_rows(&graph, &expanded);
        assert_eq!(
            rows.len(),
            2,
            "3 parametrized variants collapsed + 1 standalone = 2 visible rows"
        );
        match &rows[0] {
            TestRow::GroupHeader { base_name, indices } => {
                assert_eq!(
                    base_name, "tests/test_math.py::test_add",
                    "group header should show the base name"
                );
                assert_eq!(
                    indices.len(),
                    3,
                    "group header should list all 3 variant indices"
                );
            }
            other => panic!("expected GroupHeader, got {other:?}"),
        }
        assert!(
            matches!(rows[1], TestRow::Standalone { index: 3 }),
            "second row should be the standalone test at index 3"
        );
    }

    #[test]
    fn test_rows_expanded_group_shows_header_and_variants() {
        let graph = parametrized_graph();
        let mut expanded = HashSet::new();
        expanded.insert("tests/test_math.py::test_add".to_string());
        let rows = build_test_rows(&graph, &expanded);
        // 1 group header + 3 variants + 1 standalone = 5
        assert_eq!(
            rows.len(),
            5,
            "expanded group should show header + 3 variants + 1 standalone = 5 rows"
        );
        assert!(
            matches!(&rows[0], TestRow::GroupHeader { .. }),
            "first row should be the group header"
        );
        assert!(
            matches!(rows[1], TestRow::Variant { index: 0 }),
            "second row should be variant at index 0"
        );
        assert!(
            matches!(rows[2], TestRow::Variant { index: 1 }),
            "third row should be variant at index 1"
        );
        assert!(
            matches!(rows[3], TestRow::Variant { index: 2 }),
            "fourth row should be variant at index 2"
        );
        assert!(
            matches!(rows[4], TestRow::Standalone { index: 3 }),
            "fifth row should be the standalone test"
        );
    }

    #[test]
    fn test_rows_standalone_only() {
        let mut graph = InspectGraph::default();
        graph.tests.push(TestNode {
            node_id: "tests/test_a.py::test_one".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_b.py::test_two".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });
        let rows = build_test_rows(&graph, &HashSet::new());
        assert_eq!(rows.len(), 2, "two standalone tests should produce 2 rows");
        assert!(
            matches!(rows[0], TestRow::Standalone { index: 0 }),
            "first row should be standalone at index 0"
        );
        assert!(
            matches!(rows[1], TestRow::Standalone { index: 1 }),
            "second row should be standalone at index 1"
        );
    }

    #[test]
    fn test_rows_empty_graph() {
        let graph = InspectGraph::default();
        let rows = build_test_rows(&graph, &HashSet::new());
        assert!(rows.is_empty(), "empty graph should produce no test rows");
    }
}

#[cfg(test)]
mod snapshot_tests {
    use super::*;
    use insta::assert_snapshot;
    use ratatui::{Terminal, backend::TestBackend};

    /// Helper: create a `TestBackend` terminal of the given size, render the
    /// app, and return the buffer as a string for snapshot comparison.
    fn render_to_string(app: &InspectApp, width: u16, height: u16) -> String {
        let backend = TestBackend::new(width, height);
        let mut terminal =
            Terminal::new(backend).expect("TestBackend terminal creation should not fail");
        terminal
            .draw(|f| draw(f, app))
            .expect("drawing should not fail");
        terminal.backend().to_string()
    }

    // ── Layout snapshots ─────────────────────────────────────────────────

    #[test]
    fn snap_wide_layout_renders_two_panes() {
        let mut app = InspectApp::new(None, None);
        app.terminal_width = 120;
        assert_snapshot!("wide_layout_two_panes", render_to_string(&app, 120, 24));
    }

    #[test]
    fn snap_narrow_layout_renders_adjusted_split() {
        let mut app = InspectApp::new(None, None);
        app.terminal_width = 90;
        assert_snapshot!(
            "narrow_layout_adjusted_split",
            render_to_string(&app, 90, 24)
        );
    }

    #[test]
    fn snap_single_pane_layout() {
        let mut app = InspectApp::new(None, None);
        app.terminal_width = 60;
        assert_snapshot!("single_pane_layout", render_to_string(&app, 60, 24));
    }

    // ── Footer snapshots ─────────────────────────────────────────────────

    #[test]
    fn snap_footer_normal_mode() {
        let mut app = InspectApp::new(None, None);
        app.terminal_width = 80;
        // Height must be >= 4 so footer row is visible (Min(3) main + Length(1) footer).
        assert_snapshot!("footer_normal_mode", render_to_string(&app, 80, 4));
    }

    #[test]
    fn snap_footer_search_mode() {
        let mut app = InspectApp::new(None, None);
        app.terminal_width = 80;
        app.input_mode = InputMode::Search {
            query: String::new(),
        };
        assert_snapshot!("footer_search_mode", render_to_string(&app, 80, 4));
    }

    #[test]
    fn snap_search_query_displayed() {
        let mut app = InspectApp::new(None, None);
        app.terminal_width = 80;
        app.input_mode = InputMode::Search {
            query: "test_foo".to_string(),
        };
        assert_snapshot!("search_query_displayed", render_to_string(&app, 80, 4));
    }

    // ── Help overlay snapshot ────────────────────────────────────────────

    #[test]
    fn snap_help_overlay_visible() {
        let mut app = InspectApp::new(None, None);
        app.terminal_width = 120;
        app.show_help = true;
        assert_snapshot!("help_overlay_visible", render_to_string(&app, 120, 24));
    }

    // ── Navigation screen snapshots ─────────────────────────────────────

    use crate::inspect::graph::InspectGraph;
    use crate::inspect::graph::nodes::{MarkNode, TestNode};

    /// Build a graph with 3 tests and 1 mark for snapshot tests.
    fn snapshot_graph() -> InspectGraph {
        let mut graph = InspectGraph::default();
        graph.tests.push(TestNode {
            node_id: "tests/test_auth.py::test_login".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_auth.py::test_logout".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_db.py::test_connect".to_string(),
            is_async: true,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph.marks.push(MarkNode {
            name: "slow".to_string(),
            used_by: vec![0],
        });
        graph
    }

    #[test]
    fn snap_home_screen_with_graph() {
        let graph = snapshot_graph();
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 80;
        assert_snapshot!("home_screen_with_graph", render_to_string(&app, 80, 12));
    }

    #[test]
    fn snap_home_screen_cursor_on_second() {
        let graph = snapshot_graph();
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 80;
        if let NavScreen::Home { selected } = app.nav.current_mut() {
            *selected = 1;
        }
        assert_snapshot!("home_screen_cursor_second", render_to_string(&app, 80, 12));
    }

    #[test]
    fn snap_node_list_tests() {
        let graph = snapshot_graph();
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 80;
        app.nav.push(super::NavScreen::NodeList {
            kind: NodeKind::Test,
            selected: 0,
        });
        assert_snapshot!("node_list_tests", render_to_string(&app, 80, 12));
    }

    #[test]
    fn snap_node_list_cursor_moved() {
        let graph = snapshot_graph();
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 80;
        app.nav.push(super::NavScreen::NodeList {
            kind: NodeKind::Test,
            selected: 2,
        });
        assert_snapshot!("node_list_cursor_moved", render_to_string(&app, 80, 12));
    }

    // ── Parametrize collapse snapshots ───────────────────────────────────

    /// Build a graph with parametrized tests for collapsing snapshots.
    fn parametrized_graph() -> InspectGraph {
        let mut graph = InspectGraph::default();
        graph.tests.push(TestNode {
            node_id: "tests/test_math.py::test_add[1+2]".to_string(),
            is_async: false,
            param_id: Some("1+2".to_string()),
            param_count: 3,
            variants: vec![1, 2],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_math.py::test_add[3+4]".to_string(),
            is_async: false,
            param_id: Some("3+4".to_string()),
            param_count: 3,
            variants: vec![0, 2],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_math.py::test_add[5+6]".to_string(),
            is_async: false,
            param_id: Some("5+6".to_string()),
            param_count: 3,
            variants: vec![0, 1],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph
    }

    #[test]
    fn snap_node_list_parametrized_collapsed() {
        let graph = parametrized_graph();
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 80;
        // No groups expanded — the 3-variant group shows as a single collapsed row.
        app.nav.push(super::NavScreen::NodeList {
            kind: NodeKind::Test,
            selected: 0,
        });
        assert_snapshot!(
            "node_list_parametrized_collapsed",
            render_to_string(&app, 80, 12)
        );
    }

    #[test]
    fn snap_node_list_parametrized_expanded() {
        let graph = parametrized_graph();
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 80;
        // Expand the parametrize group so variants appear below the header.
        app.expanded_groups
            .insert("tests/test_math.py::test_add".to_string());
        app.nav.push(super::NavScreen::NodeList {
            kind: NodeKind::Test,
            selected: 0,
        });
        assert_snapshot!(
            "node_list_parametrized_expanded",
            render_to_string(&app, 80, 12)
        );
    }

    // ── History screen snapshots ─────────────────────────────────────────

    use crate::inspect::graph::NodeRef as GraphNodeRef;

    #[test]
    fn snap_history_screen_with_entries() {
        let graph = snapshot_graph();
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 120;
        // Push three history entries (most recent first after push()).
        app.history.push(GraphNodeRef {
            kind: NodeKind::Mark,
            index: 0,
        });
        app.history.push(GraphNodeRef {
            kind: NodeKind::Test,
            index: 1,
        });
        app.history.push(GraphNodeRef {
            kind: NodeKind::Test,
            index: 0,
        });
        // Navigate to History screen with cursor on first entry.
        app.nav.push(super::NavScreen::History { selected: 0 });
        assert_snapshot!(
            "history_screen_with_entries",
            render_to_string(&app, 120, 24)
        );
    }

    #[test]
    fn snap_history_screen_empty() {
        let graph = snapshot_graph();
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 120;
        // No history pushes — history is empty.
        app.nav.push(super::NavScreen::History { selected: 0 });
        assert_snapshot!("history_screen_empty", render_to_string(&app, 120, 24));
    }
}
