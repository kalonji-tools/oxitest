//! Key and mouse event handling for `oxitest inspect`.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};

use super::app::{InputMode, InspectApp, ScopeMode, SearchState};
use super::detail;
use super::graph::NodeRef;
use super::nav::Screen;

/// Process a key event and update application state.
pub(crate) fn handle_key(app: &mut InspectApp, key: KeyEvent) {
    // Source view intercept — when active, only source-view keys are handled.
    if app.source_view.is_some() {
        handle_source_view_key(app, key);
        return;
    }
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
        KeyCode::Enter | KeyCode::Char('l') | KeyCode::Right => nav_push(app),

        // Navigate back
        KeyCode::Char('h') | KeyCode::Left | KeyCode::Backspace => {
            app.nav.pop();
            app.scroll_offset = 0;
        }

        // Open history screen
        KeyCode::Char('H') => {
            app.nav.push(Screen::History { selected: 0 });
            app.scroll_offset = 0;
        }

        // Enter search mode
        KeyCode::Char('/') => {
            app.search = SearchState::new();
            app.input_mode = InputMode::Search {
                query: String::new(),
            };
        }

        // Toggle help overlay
        KeyCode::Char('?') => {
            app.show_help = !app.show_help;
        }

        // Refresh graph data from disk
        KeyCode::Char('r') => {
            app.refresh();
        }

        // Source view — read the source file for the focused node
        KeyCode::Char('s') => {
            let node = match app.nav.current() {
                Screen::NodeFocus { node, .. } => Some(node.clone()),
                Screen::Overview { selected } => app.overview_sections.node_ref_at(*selected),
                Screen::History { selected } => app.history.entries.get(*selected).cloned(),
                Screen::Disambiguation {
                    matches, selected, ..
                } => matches.get(*selected).cloned(),
            };
            if let Some(ref n) = node {
                app.enter_source_view(n);
            }
        }

        // Open in $EDITOR — jump to source file at line in an external editor
        KeyCode::Char('e') => {
            let node = match app.nav.current() {
                Screen::NodeFocus { node, .. } => Some(node.clone()),
                Screen::Overview { selected } => app.overview_sections.node_ref_at(*selected),
                Screen::History { selected } => app.history.entries.get(*selected).cloned(),
                Screen::Disambiguation {
                    matches, selected, ..
                } => matches.get(*selected).cloned(),
            };
            if let Some(ref n) = node {
                let graph = match &app.graph {
                    Some(g) => g,
                    None => return,
                };
                if super::source::node_source_location(graph, n).is_some() {
                    app.open_in_editor_request = Some(n.clone());
                } else {
                    app.flash(format!("no source available for {}s", n.kind.label()));
                }
            }
        }

        // Space — no-op (removed from navigate-into)
        KeyCode::Char(' ') => {}

        _ => {}
    }
}

// ── Source view mode ─────────────────────────────────────────────────────────

fn handle_source_view_key(app: &mut InspectApp, key: KeyEvent) {
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
        app.should_quit = true;
        return;
    }
    match key.code {
        KeyCode::Esc | KeyCode::Char('q') => app.exit_source_view(),
        KeyCode::Down | KeyCode::Char('j') => {
            if let Some(ref mut sv) = app.source_view {
                sv.scroll_offset = sv.scroll_offset.saturating_add(1);
            }
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if let Some(ref mut sv) = app.source_view {
                sv.scroll_offset = sv.scroll_offset.saturating_sub(1);
            }
        }
        KeyCode::Char('e') => {
            if let Some(ref sv) = app.source_view {
                app.open_in_editor_request = Some(sv.node_ref.clone());
            }
        }
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
            app.search = SearchState::new();
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
                app.search.query.clone_from(query);
                app.search.selected_idx = 0;
            }
            refresh_search(app);
        }
        // Toggle search scope between Context and Global
        KeyCode::Tab => {
            app.search.scope_mode = match app.search.scope_mode {
                ScopeMode::Context => ScopeMode::Global,
                ScopeMode::Global => ScopeMode::Context,
            };
            refresh_search(app);
        }
        // Append character to search query
        KeyCode::Char(c) => {
            if let InputMode::Search { query } = &mut app.input_mode {
                query.push(c);
                app.search.query.clone_from(query);
                app.search.selected_idx = 0;
            }
            refresh_search(app);
        }
        _ => {}
    }
}

/// Re-run the search engine against the current query after a keystroke.
fn refresh_search(app: &mut InspectApp) {
    if let Some(graph) = &app.graph {
        let scope = match app.search.scope_mode {
            ScopeMode::Global => {
                app.search.total_candidates = graph.all_node_refs().len();
                super::search::SearchScope::Global
            }
            ScopeMode::Context => {
                let candidates = context_candidates(app, graph);
                app.search.total_candidates = candidates.len();
                super::search::SearchScope::Context(candidates)
            }
        };
        app.search.results = super::search::search(graph, &app.search.query.clone(), scope);
    }
}

/// Compute the candidate node refs for context-scoped search based on the
/// current screen.
fn context_candidates(app: &InspectApp, graph: &super::graph::InspectGraph) -> Vec<NodeRef> {
    match app.nav.current() {
        Screen::Overview { .. } => (0..app.overview_sections.item_count())
            .filter_map(|i| app.overview_sections.node_ref_at(i))
            .collect(),
        Screen::NodeFocus { node, .. } => {
            let count = detail::selectable_edge_count(graph, node);
            (0..count)
                .filter_map(|i| detail::edge_node_at(graph, node, i))
                .collect()
        }
        Screen::History { .. } => app.history.entries.clone(),
        Screen::Disambiguation { matches, .. } => matches.clone(),
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
        Screen::Overview { selected } => {
            *selected = (*selected + 1) % max;
        }
        Screen::NodeFocus { selected, .. } => {
            *selected = (*selected + 1) % max;
        }
        Screen::Disambiguation { selected, .. } => {
            *selected = (*selected + 1) % max;
        }
        Screen::History { selected } => {
            *selected = (*selected + 1) % max;
        }
    }
}

/// Move the cursor up on the current navigation screen.
fn nav_cursor_up(app: &mut InspectApp) {
    let max = screen_item_count(app);
    if max == 0 {
        return;
    }
    match app.nav.current_mut() {
        Screen::Overview { selected } => {
            *selected = if *selected == 0 {
                max - 1
            } else {
                *selected - 1
            };
        }
        Screen::NodeFocus { selected, .. } => {
            *selected = if *selected == 0 {
                max - 1
            } else {
                *selected - 1
            };
        }
        Screen::Disambiguation { selected, .. } => {
            *selected = if *selected == 0 {
                max - 1
            } else {
                *selected - 1
            };
        }
        Screen::History { selected } => {
            *selected = if *selected == 0 {
                max - 1
            } else {
                *selected - 1
            };
        }
    }
}

/// Push into the currently selected item on the navigation stack.
fn nav_push(app: &mut InspectApp) {
    app.scroll_offset = 0;
    let graph = match &app.graph {
        Some(g) => g,
        None => return,
    };

    match app.nav.current().clone() {
        Screen::Overview { selected } => {
            let g = app.overview_sections.gravity.len();
            let m = app.overview_sections.marks.len();
            let c = app.overview_sections.conftests.len();
            if selected >= g + m + c {
                // Signal range: navigate to affected node(s)
                let sig_idx = selected - g - m - c;
                if let Some(signal) = app.overview_sections.signals.get(sig_idx) {
                    match signal.affected.len() {
                        0 => {}
                        1 => {
                            let node = signal.affected[0].clone();
                            app.history.push(node.clone());
                            app.nav.push(Screen::NodeFocus { node, selected: 0 });
                        }
                        _ => {
                            app.nav.push(Screen::Disambiguation {
                                query: signal.message.clone(),
                                matches: signal.affected.clone(),
                                selected: 0,
                            });
                        }
                    }
                }
            } else if let Some(node_ref) = app.overview_sections.node_ref_at(selected) {
                app.history.push(node_ref.clone());
                app.nav.push(Screen::NodeFocus {
                    node: node_ref,
                    selected: 0,
                });
            }
        }
        Screen::NodeFocus { node, selected } => {
            if let Some(target) = detail::edge_node_at(graph, &node, selected) {
                app.history.push(target.clone());
                app.nav.push(Screen::NodeFocus {
                    node: target,
                    selected: 0,
                });
            }
        }
        Screen::Disambiguation {
            matches, selected, ..
        } => {
            if let Some(node) = matches.get(selected) {
                app.history.push(node.clone());
                app.nav.push(Screen::NodeFocus {
                    node: node.clone(),
                    selected: 0,
                });
            }
        }
        Screen::History { selected } => {
            if let Some(node) = app.history.get(selected).cloned() {
                app.history.push(node.clone());
                app.nav.push(Screen::NodeFocus { node, selected: 0 });
            }
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
        Screen::Overview { .. } => app.overview_sections.item_count(),
        Screen::NodeFocus { node, .. } => detail::selectable_edge_count(graph, node),
        Screen::Disambiguation { matches, .. } => matches.len(),
        Screen::History { .. } => app.history.len(),
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
            super::super::graph::NodeRef {
                kind: super::super::graph::NodeKind::Test,
                index: 0,
            },
            super::super::graph::NodeRef {
                kind: super::super::graph::NodeKind::Test,
                index: 1,
            },
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
            super::super::graph::NodeRef {
                kind: super::super::graph::NodeKind::Test,
                index: 0,
            },
            super::super::graph::NodeRef {
                kind: super::super::graph::NodeKind::Test,
                index: 1,
            },
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
            super::super::graph::NodeRef {
                kind: super::super::graph::NodeKind::Test,
                index: 0,
            },
            super::super::graph::NodeRef {
                kind: super::super::graph::NodeKind::Test,
                index: 1,
            },
            super::super::graph::NodeRef {
                kind: super::super::graph::NodeKind::Test,
                index: 2,
            },
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
        app.search.results = vec![super::super::graph::NodeRef {
            kind: super::super::graph::NodeKind::Test,
            index: 0,
        }];
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
        app.search.results = vec![super::super::graph::NodeRef {
            kind: super::super::graph::NodeKind::Test,
            index: 0,
        }];
        handle_key(&mut app, key(KeyCode::Char('/')));
        assert!(app.search.query.is_empty(), "'/' should reset search query");
        assert!(
            app.search.results.is_empty(),
            "'/' should reset search results"
        );
    }

    #[test]
    fn space_on_overview_is_noop() {
        use crate::inspect::nav::Screen;

        let mut app = InspectApp::new(None, None);
        handle_key(&mut app, key(KeyCode::Char(' ')));

        assert!(
            matches!(app.nav.current(), Screen::Overview { .. }),
            "space should be a no-op and stay on Overview"
        );
    }

    #[test]
    fn r_key_triggers_refresh_noop_without_args() {
        // InspectApp::new() sets refresh_args = None.
        // Pressing 'r' calls refresh(), which returns immediately when
        // refresh_args is None — no panic, no state change.
        let mut app = InspectApp::new(None, None);
        let initial_mode = app.input_mode.clone();
        let initial_quit = app.should_quit;

        handle_key(&mut app, key(KeyCode::Char('r')));

        assert_eq!(
            app.input_mode, initial_mode,
            "pressing 'r' without refresh_args should leave input_mode unchanged"
        );
        assert_eq!(
            app.should_quit, initial_quit,
            "pressing 'r' without refresh_args should not set should_quit"
        );
    }

    // ── History key tests ──────────────────────────────────────────────

    #[test]
    fn capital_h_opens_history_screen() {
        use crate::inspect::nav::Screen;

        let mut app = InspectApp::new(None, None);
        handle_key(&mut app, key(KeyCode::Char('H')));

        assert!(
            matches!(app.nav.current(), Screen::History { selected: 0 }),
            "pressing 'H' should push History screen with selected=0"
        );
    }

    #[test]
    fn h_key_pops_back() {
        use crate::inspect::graph::{NodeKind, NodeRef};
        use crate::inspect::nav::Screen;

        let mut app = InspectApp::new(None, None);
        // Push a screen so pop has something to pop
        app.nav.push(Screen::NodeFocus {
            node: NodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            selected: 0,
        });
        handle_key(&mut app, key(KeyCode::Char('h')));

        assert!(
            matches!(app.nav.current(), Screen::Overview { .. }),
            "pressing 'h' should pop back to Overview (h is now back)"
        );
    }

    #[test]
    fn backspace_pops_without_opening_history() {
        use crate::inspect::graph::{NodeKind, NodeRef};
        use crate::inspect::nav::Screen;

        let mut app = InspectApp::new(None, None);
        // Push a screen so pop has something to pop
        app.nav.push(Screen::NodeFocus {
            node: NodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            selected: 0,
        });
        handle_key(&mut app, key(KeyCode::Backspace));

        assert!(
            matches!(app.nav.current(), Screen::Overview { .. }),
            "Backspace should pop back to Overview, not open History"
        );
    }

    #[test]
    fn left_arrow_pops_without_opening_history() {
        use crate::inspect::graph::{NodeKind, NodeRef};
        use crate::inspect::nav::Screen;

        let mut app = InspectApp::new(None, None);
        app.nav.push(Screen::NodeFocus {
            node: NodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            selected: 0,
        });
        handle_key(&mut app, key(KeyCode::Left));

        assert!(
            matches!(app.nav.current(), Screen::Overview { .. }),
            "Left arrow should pop back to Overview, not open History"
        );
    }

    #[test]
    fn enter_on_history_navigates_to_node_focus() {
        use crate::inspect::graph::nodes::TestNode;
        use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef as GraphNodeRef};
        use crate::inspect::nav::Screen;

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
        // Pre-populate history with one entry
        app.history.push(GraphNodeRef {
            kind: NodeKind::Test,
            index: 0,
        });
        // Open history screen
        app.nav.push(Screen::History { selected: 0 });
        // Press Enter to navigate to the selected history entry
        handle_key(&mut app, key(KeyCode::Enter));

        assert!(
            matches!(app.nav.current(), Screen::NodeFocus { .. }),
            "Enter on History should navigate to NodeFocus"
        );
        assert_eq!(
            app.history.len(),
            2,
            "navigating from History should add another history entry"
        );
    }

    #[test]
    fn cursor_moves_on_history_screen() {
        use crate::inspect::graph::{NodeKind, NodeRef as GraphNodeRef};
        use crate::inspect::nav::Screen;

        let mut graph = crate::inspect::graph::InspectGraph::default();
        graph.tests.push(crate::inspect::graph::nodes::TestNode {
            node_id: "t.py::test_a".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });

        let mut app = InspectApp::new(Some(graph), None);
        app.history.push(GraphNodeRef {
            kind: NodeKind::Test,
            index: 0,
        });
        app.history.push(GraphNodeRef {
            kind: NodeKind::Test,
            index: 0,
        });
        app.nav.push(Screen::History { selected: 0 });

        handle_key(&mut app, key(KeyCode::Char('j')));
        match app.nav.current() {
            Screen::History { selected } => {
                assert_eq!(*selected, 1, "j should move cursor down on History screen");
            }
            other => unreachable!("expected History screen, got {:?}", other),
        }

        handle_key(&mut app, key(KeyCode::Char('k')));
        match app.nav.current() {
            Screen::History { selected } => {
                assert_eq!(*selected, 0, "k should move cursor up on History screen");
            }
            other => unreachable!("expected History screen, got {:?}", other),
        }
    }

    // ── Search integration tests ───────────────────────────────────────

    /// Build a graph with three test nodes for search integration tests.
    fn three_test_graph() -> crate::inspect::graph::InspectGraph {
        use crate::inspect::graph::nodes::TestNode;
        let mut graph = crate::inspect::graph::InspectGraph::default();
        for name in [
            "tests/test_login.py::test_login",
            "tests/test_logout.py::test_logout",
            "tests/test_signup.py::test_signup",
        ] {
            graph.tests.push(TestNode {
                node_id: name.to_string(),
                is_async: false,
                param_id: None,
                param_count: 0,
                variants: vec![],
                fixture_deps: vec![],
                marks: vec![],
            });
        }
        graph
    }

    #[test]
    fn search_populates_results_on_keypress() {
        // Typing chars that match exactly one node should yield a single result.
        // This validates that handle_search_key() calls search() after Char(c).
        use crate::inspect::app::ScopeMode;
        let graph = three_test_graph();
        let mut app = InspectApp::new(Some(graph), None);
        app.input_mode = InputMode::Search {
            query: String::new(),
        };
        // Use Global scope since three_test_graph() has only test nodes, which
        // do not appear in overview sections (gravity/marks/conftests are empty).
        app.search.scope_mode = ScopeMode::Global;
        // Type "login" — should match only test_login
        for c in "login".chars() {
            handle_key(&mut app, key(KeyCode::Char(c)));
        }
        assert_eq!(
            app.search.results.len(),
            1,
            "typing 'login' should match exactly one test node (test_login); \
             search() must be called on every Char keypress or results stay empty"
        );
    }

    #[test]
    fn search_clears_results_on_no_match() {
        // Typing chars that match nothing should produce an empty result set.
        // This validates that search() runs and filters correctly via input.
        let graph = three_test_graph();
        let mut app = InspectApp::new(Some(graph), None);
        app.input_mode = InputMode::Search {
            query: String::new(),
        };
        // Type "zzz" — should match nothing
        for c in "zzz".chars() {
            handle_key(&mut app, key(KeyCode::Char(c)));
        }
        assert!(
            app.search.results.is_empty(),
            "typing 'zzz' should match no nodes; search() must filter correctly \
             so the UI does not show stale results when the query has no match"
        );
    }

    #[test]
    fn search_total_candidates_set_on_graph_load() {
        // with_progressive_loading() must seed total_candidates from the graph so
        // the "N/M matches" display has a denominator on first render.
        use crate::inspect::app::{InspectApp as App, Phase2Data};
        use std::sync::mpsc;

        let graph = three_test_graph();
        let (_tx, rx) = mpsc::channel::<Phase2Data>();
        let app = App::with_progressive_loading(
            graph,
            rx,
            None,
            std::time::Duration::from_secs(30),
            "",
            None,
        );
        assert!(
            app.search.total_candidates > 0,
            "total_candidates must be set when the graph is loaded via with_progressive_loading(); \
             a zero value means the '0/N matches' display will always show zero denominator"
        );
    }

    // ── Tab scope toggle tests ──────────────────────────────────────────

    #[test]
    fn tab_toggles_scope_mode_in_search() {
        use crate::inspect::app::ScopeMode;

        let graph = three_test_graph();
        let mut app = InspectApp::new(Some(graph), None);
        app.input_mode = InputMode::Search {
            query: String::new(),
        };
        assert_eq!(
            app.search.scope_mode,
            ScopeMode::Context,
            "search scope should default to Context"
        );

        handle_key(&mut app, key(KeyCode::Tab));
        assert_eq!(
            app.search.scope_mode,
            ScopeMode::Global,
            "Tab should toggle scope from Context to Global"
        );

        handle_key(&mut app, key(KeyCode::Tab));
        assert_eq!(
            app.search.scope_mode,
            ScopeMode::Context,
            "Tab again should toggle scope back to Context"
        );
    }

    #[test]
    fn tab_refreshes_search_results() {
        use crate::inspect::app::ScopeMode;

        let graph = three_test_graph();
        let mut app = InspectApp::new(Some(graph), None);
        app.input_mode = InputMode::Search {
            query: String::new(),
        };
        // Type a query that matches
        for c in "login".chars() {
            handle_key(&mut app, key(KeyCode::Char(c)));
        }
        let context_count = app.search.results.len();

        // Toggle to Global — may produce different results
        handle_key(&mut app, key(KeyCode::Tab));
        assert_eq!(
            app.search.scope_mode,
            ScopeMode::Global,
            "scope should be Global after Tab"
        );
        // Both context and global should find "login" since it's a test node
        assert!(
            !app.search.results.is_empty(),
            "Global search for 'login' should still find matches — got empty; \
             context had {context_count} results"
        );
    }

    #[test]
    fn context_search_on_overview_uses_overview_items() {
        use crate::inspect::app::ScopeMode;

        let graph = three_test_graph();
        let mut app = InspectApp::new(Some(graph), None);
        app.input_mode = InputMode::Search {
            query: String::new(),
        };
        // Ensure we're in context mode on Overview
        assert_eq!(
            app.search.scope_mode,
            ScopeMode::Context,
            "default scope should be Context"
        );

        // Type a query — context candidates come from overview_sections
        for c in "login".chars() {
            handle_key(&mut app, key(KeyCode::Char(c)));
        }
        // Overview has no marks/gravity/conftests for this graph, so context
        // candidates are empty → no results in context mode.
        let context_results = app.search.results.len();

        // Toggle to Global
        handle_key(&mut app, key(KeyCode::Tab));
        let global_results = app.search.results.len();

        assert!(
            global_results >= context_results,
            "Global scope should find at least as many results as Context scope — \
             global={global_results}, context={context_results}"
        );
    }

    #[test]
    fn context_search_on_node_focus_uses_edge_nodes() {
        // Context-scoped search while focused on a fixture node must restrict
        // candidates to that fixture's selectable edges (consumers + owner),
        // not the entire graph.  Here: one fixture "db" consumed by one test
        // "test_login", plus a conftest owner.  Searching "login" in Context
        // mode must find the consumer test; searching "db" must find nothing
        // (the fixture itself is not a selectable edge of itself).
        use crate::inspect::app::ScopeMode;
        use crate::inspect::graph::nodes::{ConftestNode, FixtureNode, TestNode};
        use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef as GraphNodeRef};
        use crate::inspect::nav::Screen;

        let mut graph = InspectGraph::default();
        // fixture at index 0 with one consumer (test index 0) and conftest at index 0
        graph.fixtures.push(FixtureNode {
            name: "db".to_string(),
            binding_type: "fixture".to_string(),
            scope: "session".to_string(),
            autouse: false,
            source: "conftest.py".to_string(),
            is_async: false,
            description: String::new(),
            consumers: vec![GraphNodeRef {
                kind: NodeKind::Test,
                index: 0,
            }],
            conftest_idx: Some(0),
            plugin_idx: None,
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_auth.py::test_login".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![0],
            marks: vec![],
        });
        graph.conftests.push(ConftestNode {
            path: "conftest.py".to_string(),
            fixtures: vec![0],
            helpers: vec![],
        });

        let fixture_node_ref = GraphNodeRef {
            kind: NodeKind::Fixture,
            index: 0,
        };
        let mut app = InspectApp::new(Some(graph), None);
        // Push NodeFocus for the "db" fixture
        app.nav.push(Screen::NodeFocus {
            node: fixture_node_ref,
            selected: 0,
        });

        // Enter search mode and stay in Context scope (the default)
        app.input_mode = InputMode::Search {
            query: String::new(),
        };
        assert_eq!(
            app.search.scope_mode,
            ScopeMode::Context,
            "scope should default to Context"
        );

        // Type "login" — only the consumer test matches; the fixture itself is not an edge
        for c in "login".chars() {
            handle_key(&mut app, key(KeyCode::Char(c)));
        }
        assert_eq!(
            app.search.results.len(),
            1,
            "context search on NodeFocus should find the consumer test 'test_login'; \
             got {} results — scope must be limited to the fixture's edge nodes, \
             not the whole graph",
            app.search.results.len()
        );
        assert_eq!(
            app.search.results[0],
            GraphNodeRef {
                kind: NodeKind::Test,
                index: 0
            },
            "the single result should be the consumer test node"
        );

        // Searching for "db" should NOT match: the fixture itself is not in its own edge list
        for _ in 0.."login".len() {
            handle_key(&mut app, key(KeyCode::Backspace));
        }
        for c in "db".chars() {
            handle_key(&mut app, key(KeyCode::Char(c)));
        }
        assert!(
            app.search.results.is_empty(),
            "context search on NodeFocus for 'db' should not match the focused fixture itself — \
             the fixture is not among its own selectable edges"
        );
    }

    #[test]
    fn context_search_on_history_uses_history_entries() {
        // Context-scoped search on the History screen must restrict candidates
        // to the nodes recorded in session history, not the whole graph.
        // Graph has two tests; only one is in history.  Searching "logout"
        // in Context mode must return empty because "logout" is not in history.
        // Searching "login" must find the history entry.
        use crate::inspect::app::ScopeMode;
        use crate::inspect::graph::nodes::TestNode;
        use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef as GraphNodeRef};
        use crate::inspect::nav::Screen;

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

        let mut app = InspectApp::new(Some(graph), None);
        // Only push test_login (index 0) into history — test_logout stays out
        app.history.push(GraphNodeRef {
            kind: NodeKind::Test,
            index: 0,
        });
        // Open the History screen
        app.nav.push(Screen::History { selected: 0 });

        app.input_mode = InputMode::Search {
            query: String::new(),
        };
        assert_eq!(
            app.search.scope_mode,
            ScopeMode::Context,
            "scope should default to Context"
        );

        // "login" is in history → should match
        for c in "login".chars() {
            handle_key(&mut app, key(KeyCode::Char(c)));
        }
        assert_eq!(
            app.search.results.len(),
            1,
            "context search on History should find 'test_login' which is in history; \
             got {} results",
            app.search.results.len()
        );
        assert_eq!(
            app.search.results[0],
            GraphNodeRef {
                kind: NodeKind::Test,
                index: 0
            },
            "the result should be the history entry for test_login"
        );

        // "logout" is NOT in history → no results despite being in the graph
        for _ in 0.."login".len() {
            handle_key(&mut app, key(KeyCode::Backspace));
        }
        for c in "logout".chars() {
            handle_key(&mut app, key(KeyCode::Char(c)));
        }
        assert!(
            app.search.results.is_empty(),
            "context search on History for 'logout' must return empty — 'test_logout' \
             is in the graph but not in session history, so Context scope must exclude it"
        );
    }

    #[test]
    fn context_search_on_disambiguation_uses_matches() {
        // Context-scoped search on a Disambiguation screen must restrict
        // candidates to the match list stored in that screen variant, not
        // the whole graph.  Graph has three tests; only two are in the
        // disambiguation list.  Searching "signup" in Context mode must
        // return empty because "signup" is not in the match list.
        use crate::inspect::app::ScopeMode;
        use crate::inspect::graph::nodes::TestNode;
        use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef as GraphNodeRef};
        use crate::inspect::nav::Screen;

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
            node_id: "tests/test_auth.py::test_signup".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });

        // Disambiguation contains only test_login and test_logout (indices 0 and 1)
        let matches = vec![
            GraphNodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            GraphNodeRef {
                kind: NodeKind::Test,
                index: 1,
            },
        ];
        let mut app = InspectApp::new(Some(graph), None);
        app.nav.push(Screen::Disambiguation {
            query: "auth test".to_string(),
            matches,
            selected: 0,
        });

        app.input_mode = InputMode::Search {
            query: String::new(),
        };
        assert_eq!(
            app.search.scope_mode,
            ScopeMode::Context,
            "scope should default to Context"
        );

        // "login" is in the match list → should match
        for c in "login".chars() {
            handle_key(&mut app, key(KeyCode::Char(c)));
        }
        assert_eq!(
            app.search.results.len(),
            1,
            "context search on Disambiguation should find 'test_login' which is in matches; \
             got {} results",
            app.search.results.len()
        );
        assert_eq!(
            app.search.results[0],
            GraphNodeRef {
                kind: NodeKind::Test,
                index: 0
            },
            "the result should be the test_login match"
        );

        // "signup" is in the graph but NOT in the disambiguation match list → no results
        for _ in 0.."login".len() {
            handle_key(&mut app, key(KeyCode::Backspace));
        }
        for c in "signup".chars() {
            handle_key(&mut app, key(KeyCode::Char(c)));
        }
        assert!(
            app.search.results.is_empty(),
            "context search on Disambiguation for 'signup' must return empty — \
             'test_signup' is in the graph but not in the Disambiguation match list, \
             so Context scope must exclude it"
        );
    }

    // ── Source view key tests ──────────────────────────────────────────

    #[test]
    fn s_key_enters_source_view_for_fixture() {
        use crate::inspect::graph::nodes::{ConftestNode, FixtureNode};
        use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef as GraphNodeRef};
        use crate::inspect::nav::Screen;

        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "db".to_string(),
            binding_type: String::new(),
            scope: "function".to_string(),
            autouse: false,
            source: "tests/conftest.py".to_string(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            conftest_idx: Some(0),
            plugin_idx: None,
        });
        graph.conftests.push(ConftestNode {
            path: "tests/conftest.py".to_string(),
            fixtures: vec![0],
            helpers: vec![],
        });

        let mut app = InspectApp::new(Some(graph), None);
        app.nav.push(Screen::NodeFocus {
            node: GraphNodeRef {
                kind: NodeKind::Fixture,
                index: 0,
            },
            selected: 0,
        });

        handle_key(&mut app, key(KeyCode::Char('s')));
        assert!(
            app.source_view.is_some(),
            "pressing 's' on a fixture with source should enter source view"
        );
        assert_eq!(
            app.source_view.as_ref().unwrap().path,
            "tests/conftest.py",
            "source view path should match the fixture's source field"
        );
    }

    #[test]
    fn s_key_flashes_on_mark() {
        use crate::inspect::graph::nodes::MarkNode;
        use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef as GraphNodeRef};
        use crate::inspect::nav::Screen;

        let mut graph = InspectGraph::default();
        graph.marks.push(MarkNode {
            name: "slow".to_string(),
            used_by: vec![],
        });

        let mut app = InspectApp::new(Some(graph), None);
        app.nav.push(Screen::NodeFocus {
            node: GraphNodeRef {
                kind: NodeKind::Mark,
                index: 0,
            },
            selected: 0,
        });

        handle_key(&mut app, key(KeyCode::Char('s')));
        assert!(
            app.source_view.is_none(),
            "pressing 's' on a mark should not enter source view — marks have no source"
        );
        assert!(
            app.flash_message.is_some(),
            "pressing 's' on a mark should flash a 'no source available' message"
        );
    }

    #[test]
    fn esc_exits_source_view() {
        use crate::inspect::graph::nodes::TestNode;
        use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef as GraphNodeRef};
        use crate::inspect::nav::Screen;

        let mut graph = InspectGraph::default();
        graph.tests.push(TestNode {
            node_id: "tests/test_foo.py::test_bar".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });

        let mut app = InspectApp::new(Some(graph), None);
        app.nav.push(Screen::NodeFocus {
            node: GraphNodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            selected: 0,
        });

        // Enter source view
        handle_key(&mut app, key(KeyCode::Char('s')));
        assert!(
            app.source_view.is_some(),
            "source view should be active after pressing 's' on a test"
        );

        // Exit source view with Esc
        handle_key(&mut app, key(KeyCode::Esc));
        assert!(
            app.source_view.is_none(),
            "pressing Esc should exit source view without quitting the app"
        );
        assert!(
            !app.should_quit,
            "Esc in source view should close the overlay, not quit the app"
        );
    }

    #[test]
    fn e_key_sets_editor_request() {
        use crate::inspect::graph::nodes::{ConftestNode, FixtureNode};
        use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef as GraphNodeRef};
        use crate::inspect::nav::Screen;

        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "db".to_string(),
            binding_type: String::new(),
            scope: "function".to_string(),
            autouse: false,
            source: "tests/conftest.py".to_string(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            conftest_idx: Some(0),
            plugin_idx: None,
        });
        graph.conftests.push(ConftestNode {
            path: "tests/conftest.py".to_string(),
            fixtures: vec![0],
            helpers: vec![],
        });

        let mut app = InspectApp::new(Some(graph), None);
        app.nav.push(Screen::NodeFocus {
            node: GraphNodeRef {
                kind: NodeKind::Fixture,
                index: 0,
            },
            selected: 0,
        });

        handle_key(&mut app, key(KeyCode::Char('e')));
        assert!(
            app.open_in_editor_request.is_some(),
            "pressing 'e' on a fixture with source should set open_in_editor_request"
        );
        assert_eq!(
            app.open_in_editor_request.as_ref().unwrap().kind,
            NodeKind::Fixture,
            "editor request should reference the focused fixture node"
        );
    }

    #[test]
    fn e_key_flashes_on_plugin() {
        use crate::inspect::graph::nodes::PluginNode;
        use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef as GraphNodeRef};
        use crate::inspect::nav::Screen;

        let mut graph = InspectGraph::default();
        graph.plugins.push(PluginNode {
            name: "capture".to_string(),
            protocols: vec![],
            fixtures: vec![],
        });

        let mut app = InspectApp::new(Some(graph), None);
        app.nav.push(Screen::NodeFocus {
            node: GraphNodeRef {
                kind: NodeKind::Plugin,
                index: 0,
            },
            selected: 0,
        });

        handle_key(&mut app, key(KeyCode::Char('e')));
        assert!(
            app.open_in_editor_request.is_none(),
            "pressing 'e' on a plugin should not set editor request — plugins have no source"
        );
        assert!(
            app.flash_message.is_some(),
            "pressing 'e' on a plugin should flash a 'no source available' message"
        );
    }

    #[test]
    fn q_exits_source_view_without_quitting() {
        use crate::inspect::graph::nodes::TestNode;
        use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef as GraphNodeRef};
        use crate::inspect::nav::Screen;

        let mut graph = InspectGraph::default();
        graph.tests.push(TestNode {
            node_id: "tests/test_foo.py::test_bar".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });

        let mut app = InspectApp::new(Some(graph), None);
        app.nav.push(Screen::NodeFocus {
            node: GraphNodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            selected: 0,
        });

        // Enter source view
        handle_key(&mut app, key(KeyCode::Char('s')));
        assert!(
            app.source_view.is_some(),
            "source view should be active after pressing 's'"
        );

        // Press 'q' to exit source view (not quit app)
        handle_key(&mut app, key(KeyCode::Char('q')));
        assert!(
            app.source_view.is_none(),
            "'q' in source view should close the overlay"
        );
        assert!(
            !app.should_quit,
            "'q' in source view should not quit the app — it only closes the overlay"
        );
    }

    #[test]
    fn source_view_scroll_keys() {
        use crate::inspect::graph::nodes::TestNode;
        use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef as GraphNodeRef};
        use crate::inspect::nav::Screen;

        let mut graph = InspectGraph::default();
        graph.tests.push(TestNode {
            node_id: "tests/test_foo.py::test_bar".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });

        let mut app = InspectApp::new(Some(graph), None);
        app.nav.push(Screen::NodeFocus {
            node: GraphNodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            selected: 0,
        });

        // Enter source view
        handle_key(&mut app, key(KeyCode::Char('s')));

        // Scroll down
        handle_key(&mut app, key(KeyCode::Char('j')));
        assert_eq!(
            app.source_view.as_ref().unwrap().scroll_offset,
            1,
            "j in source view should scroll down by 1"
        );

        // Scroll up
        handle_key(&mut app, key(KeyCode::Char('k')));
        assert_eq!(
            app.source_view.as_ref().unwrap().scroll_offset,
            0,
            "k in source view should scroll up by 1"
        );

        // Scroll up at 0 should stay at 0 (saturating)
        handle_key(&mut app, key(KeyCode::Char('k')));
        assert_eq!(
            app.source_view.as_ref().unwrap().scroll_offset,
            0,
            "k at offset 0 should stay at 0 (saturating subtract)"
        );
    }

    #[test]
    fn e_key_in_source_view_sets_editor_request() {
        use crate::inspect::graph::nodes::TestNode;
        use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef as GraphNodeRef};
        use crate::inspect::nav::Screen;

        let mut graph = InspectGraph::default();
        graph.tests.push(TestNode {
            node_id: "tests/test_foo.py::test_bar".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });

        let mut app = InspectApp::new(Some(graph), None);
        app.nav.push(Screen::NodeFocus {
            node: GraphNodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            selected: 0,
        });

        // Enter source view, then press 'e'
        handle_key(&mut app, key(KeyCode::Char('s')));
        handle_key(&mut app, key(KeyCode::Char('e')));
        assert!(
            app.open_in_editor_request.is_some(),
            "'e' in source view should set open_in_editor_request for the viewed node"
        );
    }
}
