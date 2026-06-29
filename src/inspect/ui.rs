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

use super::app::{InputMode, InspectApp};

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

    // Left pane — tree browser (placeholder)
    let left_block = Block::default().borders(Borders::ALL).title(" Tree ");
    let left_content = Paragraph::new("No data loaded").block(left_block);
    frame.render_widget(left_content, panes[0]);

    // Right pane — detail view (placeholder, only if two-pane layout)
    if panes.len() > 1 {
        let right_block = Block::default().borders(Borders::ALL).title(" Detail ");
        let right_content = Paragraph::new("oxitest inspect").block(right_block);
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
                    Span::raw(" Back"),
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
        Line::from(" h / Left    Back"),
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
        let mut app = InspectApp::new();
        app.terminal_width = 120;
        assert_snapshot!("wide_layout_two_panes", render_to_string(&app, 120, 24));
    }

    #[test]
    fn snap_narrow_layout_renders_adjusted_split() {
        let mut app = InspectApp::new();
        app.terminal_width = 90;
        assert_snapshot!(
            "narrow_layout_adjusted_split",
            render_to_string(&app, 90, 24)
        );
    }

    #[test]
    fn snap_single_pane_layout() {
        let mut app = InspectApp::new();
        app.terminal_width = 60;
        assert_snapshot!("single_pane_layout", render_to_string(&app, 60, 24));
    }

    // ── Footer snapshots ─────────────────────────────────────────────────

    #[test]
    fn snap_footer_normal_mode() {
        let mut app = InspectApp::new();
        app.terminal_width = 80;
        // Height must be >= 4 so footer row is visible (Min(3) main + Length(1) footer).
        assert_snapshot!("footer_normal_mode", render_to_string(&app, 80, 4));
    }

    #[test]
    fn snap_footer_search_mode() {
        let mut app = InspectApp::new();
        app.terminal_width = 80;
        app.input_mode = InputMode::Search {
            query: String::new(),
        };
        assert_snapshot!("footer_search_mode", render_to_string(&app, 80, 4));
    }

    #[test]
    fn snap_search_query_displayed() {
        let mut app = InspectApp::new();
        app.terminal_width = 80;
        app.input_mode = InputMode::Search {
            query: "test_foo".to_string(),
        };
        assert_snapshot!("search_query_displayed", render_to_string(&app, 80, 4));
    }

    // ── Help overlay snapshot ────────────────────────────────────────────

    #[test]
    fn snap_help_overlay_visible() {
        let mut app = InspectApp::new();
        app.terminal_width = 120;
        app.show_help = true;
        assert_snapshot!("help_overlay_visible", render_to_string(&app, 120, 24));
    }
}
