//! Application state and event loop for `oxitest inspect`.

use crossterm::event::{self, Event};
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;

use super::graph::InspectGraph;
use super::input;
use super::search::NodeRef;
use super::ui;

// ── InputMode ────────────────────────────────────────────────────────────────

/// Current input mode of the TUI.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum InputMode {
    /// Normal navigation mode.
    Normal,
    /// Search mode — keystrokes append to the query string.
    Search { query: String },
}

// ── SearchState ──────────────────────────────────────────────────────────────

/// Persistent search state, active when `InputMode::Search`.
///
/// Tracks the current query, matched results, and selection cursor
/// within those results.
#[derive(Debug, Clone)]
pub(crate) struct SearchState {
    /// The current search query string.
    pub(crate) query: String,
    /// Node references matching the current query.
    pub(crate) results: Vec<NodeRef>,
    /// Index of the selected result within `results`.
    pub(crate) selected_idx: usize,
    /// Total number of searchable nodes (for "N/M matches" display).
    pub(crate) total_nodes: usize,
}

impl SearchState {
    /// Create a new empty search state.
    pub(crate) fn new() -> Self {
        Self {
            query: String::new(),
            results: Vec::new(),
            selected_idx: 0,
            total_nodes: 0,
        }
    }

    /// Move selection to the next result, wrapping around.
    pub(crate) fn select_next(&mut self) {
        if !self.results.is_empty() {
            self.selected_idx = (self.selected_idx + 1) % self.results.len();
        }
    }

    /// Move selection to the previous result, wrapping around.
    pub(crate) fn select_prev(&mut self) {
        if !self.results.is_empty() {
            self.selected_idx = if self.selected_idx == 0 {
                self.results.len() - 1
            } else {
                self.selected_idx - 1
            };
        }
    }

    /// Return the currently selected node reference, if any.
    #[allow(dead_code)] // consumed once tree navigation (#1116) is wired up
    pub(crate) fn selected(&self) -> Option<NodeRef> {
        self.results.get(self.selected_idx).copied()
    }
}

// ── InspectApp ───────────────────────────────────────────────────────────────

/// Top-level application state for the inspect TUI.
pub(crate) struct InspectApp {
    pub(crate) should_quit: bool,
    pub(crate) terminal_width: u16,
    pub(crate) input_mode: InputMode,
    pub(crate) show_help: bool,
    /// Search state, populated when the user is in search mode.
    pub(crate) search: SearchState,
    /// The inspect graph, if loaded.  `None` while data is still being
    /// collected (or if collection failed).
    pub(crate) graph: Option<InspectGraph>,
}

impl InspectApp {
    /// Create a new `InspectApp` with default state.
    pub(crate) fn new(graph: Option<InspectGraph>) -> Self {
        Self {
            should_quit: false,
            terminal_width: 0,
            input_mode: InputMode::Normal,
            show_help: false,
            search: SearchState::new(),
            graph,
        }
    }

    /// Main event loop: poll for events, dispatch input, and draw.
    pub(crate) fn run(
        &mut self,
        terminal: &mut Terminal<CrosstermBackend<std::io::Stdout>>,
    ) -> Result<(), Box<dyn std::error::Error>> {
        loop {
            // Update terminal width from the current frame size.
            self.terminal_width = terminal.size()?.width;

            terminal.draw(|frame| ui::draw(frame, self))?;

            if self.should_quit {
                break;
            }

            if event::poll(std::time::Duration::from_millis(50))? {
                match event::read()? {
                    Event::Key(key) => input::handle_key(self, key),
                    Event::Mouse(mouse) => input::handle_mouse(self, mouse),
                    _ => {}
                }
            }
        }
        Ok(())
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_app_starts_in_normal_mode() {
        let app = InspectApp::new(None);
        assert_eq!(
            app.input_mode,
            InputMode::Normal,
            "app should start in normal input mode"
        );
        assert!(
            !app.should_quit,
            "app should not be in quit state on creation"
        );
        assert!(!app.show_help, "help overlay should be hidden on creation");
    }

    #[test]
    fn new_app_has_empty_search_state() {
        let app = InspectApp::new(None);
        assert!(
            app.search.query.is_empty(),
            "search query should start empty"
        );
        assert!(
            app.search.results.is_empty(),
            "search results should start empty"
        );
        assert_eq!(
            app.search.selected_idx, 0,
            "search selection index should start at zero"
        );
    }

    #[test]
    fn search_state_select_next_wraps() {
        let mut state = SearchState::new();
        state.results = vec![NodeRef(0), NodeRef(1), NodeRef(2)];
        state.select_next();
        assert_eq!(state.selected_idx, 1, "select_next from 0 should move to 1");
        state.select_next();
        assert_eq!(state.selected_idx, 2, "select_next from 1 should move to 2");
        state.select_next();
        assert_eq!(
            state.selected_idx, 0,
            "select_next from last should wrap to 0"
        );
    }

    #[test]
    fn search_state_select_prev_wraps() {
        let mut state = SearchState::new();
        state.results = vec![NodeRef(0), NodeRef(1), NodeRef(2)];
        state.select_prev();
        assert_eq!(
            state.selected_idx, 2,
            "select_prev from 0 should wrap to last"
        );
        state.select_prev();
        assert_eq!(state.selected_idx, 1, "select_prev from 2 should move to 1");
    }

    #[test]
    fn search_state_select_on_empty_is_noop() {
        let mut state = SearchState::new();
        state.select_next();
        assert_eq!(
            state.selected_idx, 0,
            "select_next on empty results should stay at 0"
        );
        state.select_prev();
        assert_eq!(
            state.selected_idx, 0,
            "select_prev on empty results should stay at 0"
        );
    }

    #[test]
    fn search_state_selected_returns_current() {
        let mut state = SearchState::new();
        assert!(
            state.selected().is_none(),
            "selected on empty results should return None"
        );
        state.results = vec![NodeRef(5), NodeRef(10)];
        assert_eq!(
            state.selected(),
            Some(NodeRef(5)),
            "selected at index 0 should return first result"
        );
        state.select_next();
        assert_eq!(
            state.selected(),
            Some(NodeRef(10)),
            "selected at index 1 should return second result"
        );
    }
}
