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

use super::app::{InputMode, InspectApp, SessionHistory};
use super::detail;
use super::graph::{self, NodeRef};
use super::nav::Screen;
use super::overview;

// ── Visible row model for parametrize collapsing ─────────────────────────────

/// A single visible row in the Test NodeList.
///
/// When parametrize collapsing is active, tests sharing a base name
/// (everything before `[`) are grouped.  The group appears as a single
/// collapsed header row or as an expanded sequence of variant rows.
#[derive(Debug, Clone, PartialEq, Eq)]
#[allow(dead_code)] // retained for future parametrize collapsing in edge lists
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
#[allow(dead_code)] // retained for future parametrize collapsing in edge lists
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

/// Compute the adaptive two-pane column widths based on terminal width and
/// preview content length.
///
/// Returns `(left_width, right_width)`.  When `width < 80`, the right pane
/// is zero (single-pane mode).
pub(super) fn adaptive_layout(width: u16, preview_line_count: usize) -> (u16, u16) {
    if width < 80 {
        return (width, 0);
    }
    let min_left = (width as f32 * 0.30) as u16;
    let max_left = (width as f32 * 0.55) as u16;
    let min_right = (width as f32 * 0.30) as u16;

    let left = if preview_line_count < 10 {
        // Short preview: give left pane more space
        max_left.min(width - min_right)
    } else if preview_line_count > 20 {
        // Long preview: give right pane ~62%, clamp left to minimum
        let right = (width as f32 * 0.62) as u16;
        (width - right).max(min_left)
    } else {
        // Medium preview: equal split
        width / 2
    };

    (left, width - left)
}

// ── Drawing ──────────────────────────────────────────────────────────────────

/// Render the full TUI frame: breadcrumb header, main panes, footer, and any
/// overlays.
pub(crate) fn draw(frame: &mut Frame<'_>, app: &InspectApp) {
    let size = frame.area();

    // Split into [breadcrumb, main area, footer].
    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1),
            Constraint::Min(3),
            Constraint::Length(1),
        ])
        .split(size);

    let breadcrumb_area = outer[0];
    let main_area = outer[1];
    let footer_area = outer[2];

    // ── Breadcrumb header ────────────────────────────────────────────────
    let breadcrumb = build_breadcrumb(app);
    frame.render_widget(breadcrumb, breadcrumb_area);

    // ── Compute preview content for the right pane ───────────────────────
    let preview_lines = build_preview_content(app);
    let preview_line_count = preview_lines.len();

    // ── Adaptive two-pane layout ─────────────────────────────────────────
    let (left_width, right_width) = adaptive_layout(app.terminal_width, preview_line_count);

    let (left_text, _) = build_left_pane(app);
    let scroll = (app.scroll_offset, 0);

    if right_width > 0 {
        // Two-pane layout
        let panes = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([
                Constraint::Length(left_width),
                Constraint::Length(right_width),
            ])
            .split(main_area);

        // Left pane
        let left_block = Block::default().borders(Borders::ALL);
        let left_content = Paragraph::new(left_text).block(left_block).scroll(scroll);
        frame.render_widget(left_content, panes[0]);

        // Right pane — preview
        let right_block = Block::default().borders(Borders::ALL).title(" Preview ");
        let right_content = Paragraph::new(preview_lines).block(right_block);
        frame.render_widget(right_content, panes[1]);
    } else {
        // Single-pane layout
        let left_block = Block::default().borders(Borders::ALL);
        let left_content = Paragraph::new(left_text).block(left_block).scroll(scroll);
        frame.render_widget(left_content, main_area);
    }

    // ── Footer ───────────────────────────────────────────────────────────
    let footer = build_footer(app);
    frame.render_widget(footer, footer_area);

    // ── Help overlay ─────────────────────────────────────────────────────
    if app.show_help {
        draw_help_overlay(frame, size);
    }
}

/// Build the breadcrumb header line from the trail.
fn build_breadcrumb(app: &InspectApp) -> Paragraph<'static> {
    let crumbs = match &app.graph {
        Some(graph) => app.nav.breadcrumb(graph),
        None => vec![('\u{25CB}', "overview")],
    };
    let text: String = crumbs
        .iter()
        .map(|(sigil, name)| format!("{sigil} {name}"))
        .collect::<Vec<_>>()
        .join(" \u{203A} ");
    Paragraph::new(Line::from(Span::styled(
        format!(" {text}"),
        Style::default().fg(Color::DarkGray),
    )))
}

/// Build the left pane content based on the current screen.
///
/// Returns `(lines, cursor_line)` where `cursor_line` is the index of
/// the line that holds the cursor, or `None` when no cursor is visible.
fn build_left_pane(app: &InspectApp) -> (Vec<Line<'static>>, Option<usize>) {
    let is_loading = app.is_loading();

    let graph = match &app.graph {
        Some(g) if !g.is_empty() || is_loading => g,
        _ => return (vec![Line::from("No data loaded")], None),
    };

    match app.nav.current() {
        Screen::Overview { selected } => {
            build_overview_content(&app.overview_sections, *selected, is_loading)
        }
        Screen::NodeFocus { node, selected } => build_node_focus_content(graph, node, *selected),
        Screen::Disambiguation {
            matches, selected, ..
        } => build_disambiguation_content(graph, matches, *selected),
        Screen::History { selected } => build_history_content(graph, &app.history, *selected),
    }
}

/// Update the scroll offset so the cursor is visible in the left pane.
///
/// Called from the event loop before `draw()`, since `draw()` receives
/// an immutable reference to `InspectApp`.
pub(crate) fn update_scroll(app: &mut InspectApp, viewport_height: u16) {
    let (_, cursor_line) = build_left_pane(app);
    let Some(cursor_line) = cursor_line else {
        return;
    };
    let cursor_line = cursor_line as u16;

    // Account for border (1 line top, 1 line bottom)
    let visible = viewport_height.saturating_sub(2);
    if visible == 0 {
        return;
    }
    if cursor_line < app.scroll_offset {
        app.scroll_offset = cursor_line;
    } else if cursor_line >= app.scroll_offset + visible {
        app.scroll_offset = cursor_line - visible + 1;
    }
}

/// Render the Overview screen: sections with titles and selectable items.
///
/// Returns `(lines, cursor_line)` — the rendered lines and the index of
/// the line that holds the cursor, or `None` when no selectable items exist.
fn build_overview_content(
    sections: &overview::OverviewSections,
    selected: usize,
    is_loading: bool,
) -> (Vec<Line<'static>>, Option<usize>) {
    let mut lines = Vec::new();
    let mut cursor = 0;
    let mut cursor_line_idx: Option<usize> = None;

    // Gravity section (fixtures by consumer count)
    if !sections.gravity.is_empty() {
        lines.push(Line::from(Span::styled(
            " Fixture Gravity",
            Style::default().fg(Color::Yellow),
        )));
        for entry in &sections.gravity {
            let text = format!("  F  {} ({} consumers)", entry.name, entry.consumer_count);
            if cursor == selected {
                cursor_line_idx = Some(lines.len());
            }
            lines.push(if cursor == selected {
                Line::from(Span::styled(
                    format!("\u{203A} {text}"),
                    Style::default().fg(Color::Black).bg(Color::Cyan),
                ))
            } else {
                Line::from(format!("  {text}"))
            });
            cursor += 1;
        }
        lines.push(Line::from(""));
    }

    // Marks section
    if !sections.marks.is_empty() {
        lines.push(Line::from(Span::styled(
            " Marks",
            Style::default().fg(Color::Yellow),
        )));
        for entry in &sections.marks {
            let text = format!("  M  {} ({} tests)", entry.name, entry.test_count);
            if cursor == selected {
                cursor_line_idx = Some(lines.len());
            }
            lines.push(if cursor == selected {
                Line::from(Span::styled(
                    format!("\u{203A} {text}"),
                    Style::default().fg(Color::Black).bg(Color::Cyan),
                ))
            } else {
                Line::from(format!("  {text}"))
            });
            cursor += 1;
        }
        lines.push(Line::from(""));
    }

    // Conftests section
    if !sections.conftests.is_empty() {
        lines.push(Line::from(Span::styled(
            " Conftests",
            Style::default().fg(Color::Yellow),
        )));
        for entry in &sections.conftests {
            let text = format!(
                "  C  {} ({} fixtures, {} helpers)",
                entry.path, entry.fixture_count, entry.helper_count
            );
            if cursor == selected {
                cursor_line_idx = Some(lines.len());
            }
            lines.push(if cursor == selected {
                Line::from(Span::styled(
                    format!("\u{203A} {text}"),
                    Style::default().fg(Color::Black).bg(Color::Cyan),
                ))
            } else {
                Line::from(format!("  {text}"))
            });
            cursor += 1;
        }
    }

    // Signals section
    if !sections.signals.is_empty() {
        if !lines.is_empty() {
            lines.push(Line::from(""));
        }
        lines.push(Line::from(Span::styled(
            " Signals",
            Style::default().fg(Color::Yellow),
        )));
        for signal in &sections.signals {
            let text = format!("  \u{26A0}  {}", signal.message);
            if cursor == selected {
                cursor_line_idx = Some(lines.len());
            }
            lines.push(if cursor == selected {
                Line::from(Span::styled(
                    format!("\u{203A} {text}"),
                    Style::default().fg(Color::Black).bg(Color::Cyan),
                ))
            } else {
                Line::from(format!("  {text}"))
            });
            cursor += 1;
        }
    } else if is_loading {
        if !lines.is_empty() {
            lines.push(Line::from(""));
        }
        lines.push(Line::from(Span::styled(
            " Signals  \u{23F3}",
            Style::default().fg(Color::DarkGray),
        )));
    }

    if lines.is_empty() {
        if is_loading {
            lines.push(Line::from(Span::styled(
                " Loading...",
                Style::default().fg(Color::DarkGray),
            )));
        } else {
            lines.push(Line::from(" No data loaded"));
        }
    }

    (lines, cursor_line_idx)
}

/// Render the NodeFocus screen: node properties + selectable edge list.
///
/// Returns `(lines, cursor_line)` — the rendered lines and the index of
/// the line that holds the cursor within the "Related" section.
fn build_node_focus_content(
    graph: &super::graph::InspectGraph,
    node: &NodeRef,
    selected: usize,
) -> (Vec<Line<'static>>, Option<usize>) {
    // Show detailed properties from the detail module
    let mut lines = detail::render_detail(graph, Some(node));

    // Append selectable edges with cursor indicators
    let edge_count = detail::selectable_edge_count(graph, node);
    let mut cursor_line_idx: Option<usize> = None;
    if edge_count > 0 {
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            "  Related",
            Style::default().fg(Color::Yellow),
        )));
        let related_start = lines.len();
        for i in 0..edge_count {
            if let Some(edge_ref) = detail::edge_node_at(graph, node, i) {
                let sigil = edge_ref.kind.sigil();
                let name = graph.node_name(&edge_ref);
                let text = format!("  {sigil}  {name}");
                if i == selected {
                    cursor_line_idx = Some(related_start + i);
                }
                lines.push(if i == selected {
                    Line::from(Span::styled(
                        format!("\u{203A} {text}"),
                        Style::default().fg(Color::Black).bg(Color::Cyan),
                    ))
                } else {
                    Line::from(format!("  {text}"))
                });
            }
        }
    }

    (lines, cursor_line_idx)
}

/// Render a Disambiguation screen: one line per matching node.
///
/// Returns `(lines, cursor_line)` — one line per match, cursor line
/// equals `selected` (no headers).
fn build_disambiguation_content(
    graph: &super::graph::InspectGraph,
    matches: &[NodeRef],
    selected: usize,
) -> (Vec<Line<'static>>, Option<usize>) {
    let lines: Vec<Line<'static>> = matches
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
        .collect();
    let cursor_line = if matches.is_empty() {
        None
    } else {
        Some(selected)
    };
    (lines, cursor_line)
}

/// Render the History screen: one line per visited node, most recent first.
///
/// Returns `(lines, cursor_line)` — one line per entry, cursor line
/// equals `selected`.  Returns `None` when history is empty.
fn build_history_content(
    graph: &super::graph::InspectGraph,
    history: &SessionHistory,
    selected: usize,
) -> (Vec<Line<'static>>, Option<usize>) {
    if history.len() == 0 {
        return (vec![Line::from(" No history yet")], None);
    }
    let lines: Vec<Line<'static>> = history
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
        .collect();
    (lines, Some(selected))
}

/// Build the preview content for the right pane based on current screen and
/// cursor position.
fn build_preview_content(app: &InspectApp) -> Vec<Line<'static>> {
    let graph = match &app.graph {
        Some(g) => g,
        None => return vec![Line::from("No data loaded")],
    };

    let mut lines = match app.nav.current() {
        Screen::Overview { selected } => {
            if let Some(node_ref) = app.overview_sections.node_ref_at(*selected) {
                detail::render_preview(graph, &node_ref)
            } else {
                vec![Line::from("Select an item to preview")]
            }
        }
        Screen::NodeFocus { node, selected } => {
            if let Some(edge_ref) = detail::edge_node_at(graph, node, *selected) {
                detail::render_preview(graph, &edge_ref)
            } else {
                detail::render_preview(graph, node)
            }
        }
        Screen::Disambiguation {
            matches, selected, ..
        } => {
            if let Some(node_ref) = matches.get(*selected) {
                detail::render_preview(graph, node_ref)
            } else {
                vec![Line::from("Select a match to preview")]
            }
        }
        Screen::History { selected } => {
            if let Some(node_ref) = app.history.get(*selected) {
                detail::render_preview(graph, node_ref)
            } else {
                vec![Line::from(" No history yet")]
            }
        }
    };

    // Append loading indicator when fixture/plugin data is still arriving.
    if app.is_loading() {
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            "Loading fixture and plugin data...",
            Style::default().fg(Color::DarkGray),
        )));
    }

    lines
}

/// Build the footer bar with context-sensitive keybinding hints.
fn build_footer(app: &InspectApp) -> Paragraph<'static> {
    let spans = match &app.input_mode {
        InputMode::Normal => {
            // If search results are active (Enter was pressed), show match count
            if !app.search.results.is_empty() {
                let count = app.search.results.len();
                let total = app.search.total_candidates;
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
                // Contextual hints based on current screen
                let mut hints = vec![
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
                    Span::raw(" Back  "),
                    Span::styled("H", Style::default().fg(Color::Yellow)),
                    Span::raw(" History"),
                ];
                // Add loading indicator on the right side
                if app.is_loading() {
                    hints.push(Span::raw("  "));
                    hints.push(Span::styled(
                        "loading...",
                        Style::default().fg(Color::DarkGray),
                    ));
                }
                hints
            }
        }
        InputMode::Search { query } => {
            use super::app::ScopeMode;
            let count = app.search.results.len();
            let total = app.search.total_candidates;
            let match_info = if query.is_empty() {
                String::new()
            } else {
                format!("  {count}/{total} matches")
            };
            let scope_hint = match app.search.scope_mode {
                ScopeMode::Context => "global",
                ScopeMode::Global => "context",
            };
            vec![
                Span::styled(" /", Style::default().fg(Color::Yellow)),
                Span::raw(query.to_string()),
                Span::raw(match_info),
                Span::raw("  "),
                Span::styled("Tab", Style::default().fg(Color::Yellow)),
                Span::raw(format!(" {scope_hint}  ")),
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
        Line::from(" Enter / l   Navigate into"),
        Line::from(" h / Left    Back"),
        Line::from(" H           History"),
        Line::from(" Backspace   Back"),
        Line::from(" /           Search"),
        Line::from(" ?           Toggle this help"),
        Line::from(" r           Refresh data"),
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
    fn adaptive_layout_wide_two_panes() {
        let (left, right) = adaptive_layout(120, 15);
        assert!(
            right > 0,
            "terminal width >= 80 should produce a two-pane layout"
        );
        assert_eq!(left + right, 120, "left + right should equal total width");
    }

    #[test]
    fn adaptive_layout_narrow_single_pane() {
        let (left, right) = adaptive_layout(60, 15);
        assert_eq!(
            right, 0,
            "terminal width < 80 should produce a single-pane layout"
        );
        assert_eq!(left, 60, "single-pane left should equal total width");
    }

    #[test]
    fn adaptive_layout_boundary_80() {
        let (left, right) = adaptive_layout(80, 15);
        assert!(
            right > 0,
            "terminal width exactly 80 should produce a two-pane layout"
        );
        assert_eq!(left + right, 80, "left + right should equal total width");
    }

    #[test]
    fn adaptive_layout_boundary_79_single_pane() {
        let (_left, right) = adaptive_layout(79, 15);
        assert_eq!(
            right, 0,
            "terminal width 79 should produce a single-pane layout"
        );
    }

    #[test]
    fn adaptive_layout_short_preview_widens_left() {
        let (left_short, _) = adaptive_layout(120, 5);
        let (left_long, _) = adaptive_layout(120, 25);
        assert!(
            left_short >= left_long,
            "short preview (< 10 lines) should widen the left pane"
        );
    }

    #[test]
    fn adaptive_layout_long_preview_widens_right() {
        let (_, right_long) = adaptive_layout(120, 25);
        let (_, right_mid) = adaptive_layout(120, 15);
        assert!(
            right_long >= right_mid,
            "long preview (> 20 lines) should widen the right pane"
        );
    }

    #[test]
    fn adaptive_layout_width_invariant() {
        for width in [80, 100, 120, 160, 200] {
            for preview_lines in [0, 5, 9, 10, 15, 20, 21, 30, 50] {
                let (left, right) = adaptive_layout(width, preview_lines);
                assert_eq!(
                    left + right,
                    width,
                    "left + right must equal width for width={width}, preview={preview_lines}"
                );
                assert!(
                    left > 0,
                    "left pane must have positive width for width={width}, preview={preview_lines}"
                );
                assert!(
                    right > 0,
                    "right pane must have positive width for width={width}, preview={preview_lines}"
                );
            }
        }
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

    // ── update_scroll tests ───────────────────────────────────────────────

    use crate::inspect::graph::nodes::MarkNode;

    /// Build a graph with many marks so the overview generates enough lines
    /// to exceed a small viewport.
    fn many_marks_graph() -> InspectGraph {
        let mut graph = InspectGraph::default();
        for i in 0..20 {
            graph.marks.push(MarkNode {
                name: format!("mark_{i}"),
                used_by: vec![],
            });
        }
        graph
    }

    #[test]
    fn update_scroll_cursor_below_viewport() {
        let graph = many_marks_graph();
        let mut app = InspectApp::new(Some(graph), None);
        // Move cursor to the last overview item (mark_19 at index 19).
        if let Screen::Overview { selected } = app.nav.current_mut() {
            *selected = 19;
        }
        // Small viewport: 8 lines total, 6 visible after border subtraction.
        update_scroll(&mut app, 8);
        assert!(
            app.scroll_offset > 0,
            "cursor on the last of 20 marks should push scroll_offset above zero \
             when the viewport is only 8 lines tall"
        );
    }

    #[test]
    fn update_scroll_cursor_above_viewport() {
        let graph = many_marks_graph();
        let mut app = InspectApp::new(Some(graph), None);
        // Simulate a high scroll offset as if user had scrolled far down.
        app.scroll_offset = 15;
        // Cursor at first item (selected = 0).
        if let Screen::Overview { selected } = app.nav.current_mut() {
            *selected = 0;
        }
        update_scroll(&mut app, 8);
        // The first selectable item sits at line 1 (after the "Marks" section
        // header at line 0), so scroll_offset snaps to cursor_line = 1.
        assert!(
            app.scroll_offset <= 1,
            "cursor at the first selectable item should bring scroll_offset \
             down from 15 to at most the cursor line (line 1 after section header)"
        );
    }

    #[test]
    fn update_scroll_cursor_visible_no_change() {
        let graph = many_marks_graph();
        let mut app = InspectApp::new(Some(graph), None);
        app.scroll_offset = 0;
        // Cursor at first item — already visible.
        if let Screen::Overview { selected } = app.nav.current_mut() {
            *selected = 0;
        }
        update_scroll(&mut app, 30);
        assert_eq!(
            app.scroll_offset, 0,
            "cursor within the visible viewport should not change scroll_offset"
        );
    }

    #[test]
    fn update_scroll_zero_viewport() {
        let graph = many_marks_graph();
        let mut app = InspectApp::new(Some(graph), None);
        let original_offset = app.scroll_offset;
        // viewport_height of 0 and 1 both yield visible=0 after saturating_sub(2).
        update_scroll(&mut app, 0);
        assert_eq!(
            app.scroll_offset, original_offset,
            "viewport_height=0 should not panic and should leave scroll_offset unchanged"
        );
        update_scroll(&mut app, 1);
        assert_eq!(
            app.scroll_offset, original_offset,
            "viewport_height=1 should not panic and should leave scroll_offset unchanged"
        );
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

    use crate::inspect::graph::nodes::{MarkNode, TestNode};
    use crate::inspect::graph::{InspectGraph, NodeKind};

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
    fn snap_overview_cursor_on_second() {
        let graph = snapshot_graph();
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 80;
        if let Screen::Overview { selected } = app.nav.current_mut() {
            *selected = 1;
        }
        assert_snapshot!("overview_cursor_second", render_to_string(&app, 80, 12));
    }

    #[test]
    fn snap_node_focus() {
        let graph = snapshot_graph();
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 80;
        app.nav.push(Screen::NodeFocus {
            node: crate::inspect::graph::NodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            selected: 0,
        });
        assert_snapshot!("node_focus", render_to_string(&app, 80, 12));
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
        app.nav.push(Screen::History { selected: 0 });
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
        app.nav.push(Screen::History { selected: 0 });
        assert_snapshot!("history_screen_empty", render_to_string(&app, 120, 24));
    }
}
