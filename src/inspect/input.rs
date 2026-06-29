//! Key and mouse event handling for `oxitest inspect`.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};

use super::app::{InputMode, InspectApp};
use super::nav::{self, NavScreen};

/// Process a key event and update application state.
pub(crate) fn handle_key(app: &mut InspectApp, key: KeyEvent) {
    match &app.input_mode {
        InputMode::Normal => handle_normal_key(app, key),
        InputMode::Search { .. } => handle_search_key(app, key),
    }
}

/// Process a mouse event and update application state.
pub(crate) fn handle_mouse(app: &mut InspectApp, mouse: MouseEvent) {
    match mouse.kind {
        MouseEventKind::ScrollUp | MouseEventKind::ScrollDown => {
            // Scroll events — no-op until tree navigation is implemented.
        }
        MouseEventKind::Down(MouseButton::Left) => {
            // Click to select — no-op until tree navigation is implemented.
        }
        _ => {}
    }
    // Suppress unused variable warning until navigation is wired up.
    let _ = app;
}

// ── Normal mode ──────────────────────────────────────────────────────────────

fn handle_normal_key(app: &mut InspectApp, key: KeyEvent) {
    // Ctrl+C always quits, regardless of mode.
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
        app.should_quit = true;
        return;
    }

    match key.code {
        // Quit
        KeyCode::Char('q') | KeyCode::Esc => app.should_quit = true,

        // Vertical movement
        KeyCode::Char('j') | KeyCode::Down => nav_cursor_down(app),
        KeyCode::Char('k') | KeyCode::Up => nav_cursor_up(app),

        // Navigate into selected item
        KeyCode::Char('l') | KeyCode::Right | KeyCode::Char(' ') => nav_push(app),

        // Navigate back
        KeyCode::Char('h') | KeyCode::Left | KeyCode::Backspace => {
            app.nav.pop();
        }

        // Enter search mode
        KeyCode::Char('/') => {
            app.search = super::app::SearchState::new();
            app.input_mode = InputMode::Search {
                query: String::new(),
            };
        }

        // Toggle help overlay
        KeyCode::Char('?') => {
            app.show_help = !app.show_help;
        }

        // Toggle source view (no-op until #1117)
        KeyCode::Char('s') => {}

        _ => {}
    }
}

// ── Search mode ──────────────────────────────────────────────────────────────

fn handle_search_key(app: &mut InspectApp, key: KeyEvent) {
    // Ctrl+C always quits, regardless of mode.
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
        app.should_quit = true;
        return;
    }

    match key.code {
        // Exit search mode, clear search state, return to normal
        KeyCode::Esc => {
            app.search = super::app::SearchState::new();
            app.input_mode = InputMode::Normal;
        }
        // Accept search — navigate to selected result (no-op until #1116)
        KeyCode::Enter => {
            // Keep search results visible; just switch to normal mode
            app.input_mode = InputMode::Normal;
        }
        // Navigate results: next
        KeyCode::Down => {
            app.search.select_next();
        }
        // Navigate results: previous
        KeyCode::Up => {
            app.search.select_prev();
        }
        // Delete last character from search query
        KeyCode::Backspace => {
            if let InputMode::Search { query } = &mut app.input_mode {
                query.pop();
                // Sync SearchState query
                app.search.query.clone_from(query);
                // Reset selection when query changes
                app.search.selected_idx = 0;
            }
        }
        // Append character to search query
        KeyCode::Char(c) => {
            if let InputMode::Search { query } = &mut app.input_mode {
                query.push(c);
                // Sync SearchState query
                app.search.query.clone_from(query);
                // Reset selection when query changes
                app.search.selected_idx = 0;
            }
        }
        _ => {}
    }
}

// ── Navigation helpers ───────────────────────────────────────────────────────

/// Move the cursor down on the current navigation screen.
fn nav_cursor_down(app: &mut InspectApp) {
    let max = screen_item_count(app);
    if max == 0 {
        return;
    }
    match app.nav.current_mut() {
        NavScreen::Home { selected } => {
            *selected = (*selected + 1) % max;
        }
        NavScreen::NodeList { selected, .. } => {
            *selected = (*selected + 1) % max;
        }
        NavScreen::Disambiguation { selected, .. } => {
            *selected = (*selected + 1) % max;
        }
        NavScreen::NodeDetail { .. } => {}
    }
}

/// Move the cursor up on the current navigation screen.
fn nav_cursor_up(app: &mut InspectApp) {
    let max = screen_item_count(app);
    if max == 0 {
        return;
    }
    match app.nav.current_mut() {
        NavScreen::Home { selected } => {
            *selected = if *selected == 0 {
                max - 1
            } else {
                *selected - 1
            };
        }
        NavScreen::NodeList { selected, .. } => {
            *selected = if *selected == 0 {
                max - 1
            } else {
                *selected - 1
            };
        }
        NavScreen::Disambiguation { selected, .. } => {
            *selected = if *selected == 0 {
                max - 1
            } else {
                *selected - 1
            };
        }
        NavScreen::NodeDetail { .. } => {}
    }
}

/// Push into the currently selected item on the navigation stack.
fn nav_push(app: &mut InspectApp) {
    let graph = match &app.graph {
        Some(g) => g,
        None => return,
    };

    match app.nav.current().clone() {
        NavScreen::Home { selected } => {
            if let Some(kind) = nav::visible_kind_at(graph, selected) {
                app.nav.push(NavScreen::NodeList { kind, selected: 0 });
            }
        }
        NavScreen::NodeList { kind, selected } => {
            let count = graph.node_count(kind);
            if selected < count {
                let node = super::graph::NodeRef {
                    kind,
                    index: selected,
                };
                app.nav.push(NavScreen::NodeDetail { node });
            }
        }
        NavScreen::Disambiguation {
            matches, selected, ..
        } => {
            if let Some(node) = matches.get(selected) {
                app.nav.push(NavScreen::NodeDetail { node: node.clone() });
            }
        }
        NavScreen::NodeDetail { .. } => {
            // No further drill-down from detail (yet).
        }
    }
}

/// Return the number of selectable items on the current screen.
fn screen_item_count(app: &InspectApp) -> usize {
    let graph = match &app.graph {
        Some(g) => g,
        None => return 0,
    };

    match app.nav.current() {
        NavScreen::Home { .. } => nav::visible_kind_count(graph),
        NavScreen::NodeList { kind, .. } => graph.node_count(*kind),
        NavScreen::Disambiguation { matches, .. } => matches.len(),
        NavScreen::NodeDetail { .. } => 0,
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::{KeyEventKind, KeyEventState};

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent {
            code,
            modifiers: KeyModifiers::NONE,
            kind: KeyEventKind::Press,
            state: KeyEventState::NONE,
        }
    }

    #[test]
    fn input_key_q_sets_quit() {
        let mut app = InspectApp::new(None, None);
        handle_key(&mut app, key(KeyCode::Char('q')));
        assert!(
            app.should_quit,
            "pressing 'q' in normal mode should set should_quit"
        );
    }

    #[test]
    fn input_key_esc_sets_quit_in_normal() {
        let mut app = InspectApp::new(None, None);
        handle_key(&mut app, key(KeyCode::Esc));
        assert!(
            app.should_quit,
            "pressing Esc in normal mode should set should_quit"
        );
    }

    #[test]
    fn input_key_slash_enters_search() {
        let mut app = InspectApp::new(None, None);
        handle_key(&mut app, key(KeyCode::Char('/')));
        assert_eq!(
            app.input_mode,
            InputMode::Search {
                query: String::new()
            },
            "pressing '/' should transition to search mode with empty query"
        );
    }

    #[test]
    fn input_key_esc_exits_search() {
        let mut app = InspectApp::new(None, None);
        app.input_mode = InputMode::Search {
            query: "foo".to_string(),
        };
        handle_key(&mut app, key(KeyCode::Esc));
        assert_eq!(
            app.input_mode,
            InputMode::Normal,
            "pressing Esc in search mode should return to normal mode"
        );
        assert!(
            !app.should_quit,
            "Esc in search mode should not quit the app"
        );
    }

    #[test]
    fn input_search_mode_appends_chars() {
        let mut app = InspectApp::new(None, None);
        app.input_mode = InputMode::Search {
            query: String::new(),
        };
        handle_key(&mut app, key(KeyCode::Char('a')));
        handle_key(&mut app, key(KeyCode::Char('b')));
        assert_eq!(
            app.input_mode,
            InputMode::Search {
                query: "ab".to_string()
            },
            "typing in search mode should append characters to the query"
        );
    }

    #[test]
    fn input_search_mode_backspace_removes_char() {
        let mut app = InspectApp::new(None, None);
        app.input_mode = InputMode::Search {
            query: "abc".to_string(),
        };
        handle_key(&mut app, key(KeyCode::Backspace));
        assert_eq!(
            app.input_mode,
            InputMode::Search {
                query: "ab".to_string()
            },
            "backspace in search mode should remove the last character"
        );
    }

    #[test]
    fn input_question_mark_toggles_help() {
        let mut app = InspectApp::new(None, None);
        assert!(!app.show_help, "help should start hidden");
        handle_key(&mut app, key(KeyCode::Char('?')));
        assert!(app.show_help, "pressing '?' should show help overlay");
        handle_key(&mut app, key(KeyCode::Char('?')));
        assert!(
            !app.show_help,
            "pressing '?' again should hide help overlay"
        );
    }

    #[test]
    fn input_ctrl_c_quits_from_any_mode() {
        let mut app = InspectApp::new(None, None);
        app.input_mode = InputMode::Search {
            query: "test".to_string(),
        };
        let ctrl_c = KeyEvent {
            code: KeyCode::Char('c'),
            modifiers: KeyModifiers::CONTROL,
            kind: KeyEventKind::Press,
            state: KeyEventState::NONE,
        };
        handle_key(&mut app, ctrl_c);
        assert!(app.should_quit, "Ctrl+C should quit from search mode");
    }

    #[test]
    fn input_search_esc_clears_search_state() {
        let mut app = InspectApp::new(None, None);
        app.input_mode = InputMode::Search {
            query: "test".to_string(),
        };
        app.search.query = "test".to_string();
        app.search.results = vec![
            super::super::search::NodeRef(0),
            super::super::search::NodeRef(1),
        ];
        handle_key(&mut app, key(KeyCode::Esc));
        assert_eq!(
            app.input_mode,
            InputMode::Normal,
            "Esc should return to normal mode"
        );
        assert!(
            app.search.query.is_empty(),
            "Esc should clear the search query"
        );
        assert!(
            app.search.results.is_empty(),
            "Esc should clear the search results"
        );
    }

    #[test]
    fn input_search_syncs_query_to_search_state() {
        let mut app = InspectApp::new(None, None);
        app.input_mode = InputMode::Search {
            query: String::new(),
        };
        handle_key(&mut app, key(KeyCode::Char('a')));
        handle_key(&mut app, key(KeyCode::Char('b')));
        assert_eq!(
            app.search.query, "ab",
            "typing in search mode should sync query to SearchState"
        );
    }

    #[test]
    fn input_search_backspace_syncs_query() {
        let mut app = InspectApp::new(None, None);
        app.input_mode = InputMode::Search {
            query: "abc".to_string(),
        };
        app.search.query = "abc".to_string();
        handle_key(&mut app, key(KeyCode::Backspace));
        assert_eq!(
            app.search.query, "ab",
            "backspace should sync shortened query to SearchState"
        );
    }

    #[test]
    fn input_search_down_selects_next() {
        let mut app = InspectApp::new(None, None);
        app.input_mode = InputMode::Search {
            query: "test".to_string(),
        };
        app.search.results = vec![
            super::super::search::NodeRef(0),
            super::super::search::NodeRef(1),
        ];
        handle_key(&mut app, key(KeyCode::Down));
        assert_eq!(
            app.search.selected_idx, 1,
            "Down arrow in search mode should move selection forward"
        );
    }

    #[test]
    fn input_search_up_selects_prev() {
        let mut app = InspectApp::new(None, None);
        app.input_mode = InputMode::Search {
            query: "test".to_string(),
        };
        app.search.results = vec![
            super::super::search::NodeRef(0),
            super::super::search::NodeRef(1),
            super::super::search::NodeRef(2),
        ];
        app.search.selected_idx = 1;
        handle_key(&mut app, key(KeyCode::Up));
        assert_eq!(
            app.search.selected_idx, 0,
            "Up arrow in search mode should move selection backward"
        );
    }

    #[test]
    fn input_search_enter_keeps_results() {
        let mut app = InspectApp::new(None, None);
        app.input_mode = InputMode::Search {
            query: "test".to_string(),
        };
        app.search.query = "test".to_string();
        app.search.results = vec![super::super::search::NodeRef(0)];
        handle_key(&mut app, key(KeyCode::Enter));
        assert_eq!(
            app.input_mode,
            InputMode::Normal,
            "Enter should return to normal mode"
        );
        assert!(
            !app.search.results.is_empty(),
            "Enter should keep search results visible"
        );
    }

    #[test]
    fn input_slash_resets_search_state() {
        let mut app = InspectApp::new(None, None);
        app.search.query = "old".to_string();
        app.search.results = vec![super::super::search::NodeRef(0)];
        handle_key(&mut app, key(KeyCode::Char('/')));
        assert!(app.search.query.is_empty(), "'/' should reset search query");
        assert!(
            app.search.results.is_empty(),
            "'/' should reset search results"
        );
    }

    #[test]
    fn space_on_home_navigates_to_node_list() {
        use crate::inspect::graph::nodes::TestNode;
        use crate::inspect::graph::{InspectGraph, NodeKind};
        use crate::inspect::nav::NavScreen;

        let mut graph = InspectGraph::default();
        graph.tests.push(TestNode {
            node_id: "t.py::test_a".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });

        let mut app = InspectApp::new(Some(graph), None);
        // Home screen, cursor at 0 — first visible kind should be Test
        handle_key(&mut app, key(KeyCode::Char(' ')));

        match app.nav.current() {
            NavScreen::NodeList { kind, selected } => {
                assert_eq!(
                    *kind,
                    NodeKind::Test,
                    "space on Home should navigate into the first non-empty kind"
                );
                assert_eq!(*selected, 0, "NodeList should start with cursor at 0");
            }
            other => panic!(
                "expected NodeList after Space on Home, got {:?}",
                std::mem::discriminant(other)
            ),
        }
    }

    #[test]
    fn space_without_graph_is_noop() {
        use crate::inspect::nav::NavScreen;

        let mut app = InspectApp::new(None, None);
        handle_key(&mut app, key(KeyCode::Char(' ')));

        assert!(
            matches!(app.nav.current(), NavScreen::Home { .. }),
            "space without graph should stay on Home"
        );
    }
}
