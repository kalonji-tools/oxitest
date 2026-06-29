//! Application state and event loop for `oxitest inspect`.

use std::collections::HashSet;
use std::sync::mpsc;

use crossterm::event::{self, Event};
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;

use super::graph::{InspectGraph, NodeRef as GraphNodeRef};
use super::input;
use super::nav::{self, NavStack};
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

// ── LoadingState ─────────────────────────────────────────────────────────────

/// Tracks the progressive loading phase of the inspect graph.
///
/// Phase 1 (instant-tier) data is available immediately from Rust AST
/// extraction.  Phase 2 (fixture/plugin) data requires a Python session
/// and arrives asynchronously via a background thread.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum LoadingState {
    /// Only instant-tier data (tests, marks, helpers) is loaded.
    /// Fixture and plugin counts are still being collected.
    InstantOnly,
    /// All data has been loaded, including fixtures and plugins.
    Complete,
}

// ── Phase2Data ──────────────────────────────────────────────────────────────

/// Payload sent from the background thread once Python-tier data is ready.
pub(crate) struct Phase2Data {
    pub(crate) fixture_entries: Vec<crate::query::resource::QueryEntry>,
    pub(crate) plugin_entries: Vec<crate::query::resource::QueryEntry>,
}

// ── SessionHistory ──────────────────────────────────────────────────────────

/// Append-only list of visited nodes, most recent first.
///
/// Every time the user navigates to a `NodeDetail` screen, the node is
/// pushed onto the front of this list.  Duplicates are allowed — visiting
/// the same node twice produces two entries.  The history is session-only
/// and is lost when the TUI exits.
#[derive(Debug, Clone)]
pub(crate) struct SessionHistory {
    /// Entries in reverse-chronological order (most recent first).
    pub(crate) entries: Vec<GraphNodeRef>,
}

impl SessionHistory {
    /// Create a new empty history.
    pub(crate) fn new() -> Self {
        Self {
            entries: Vec::new(),
        }
    }

    /// Record a visit to the given node (prepended, so index 0 is most recent).
    pub(crate) fn push(&mut self, node: GraphNodeRef) {
        self.entries.insert(0, node);
    }

    /// Return the number of entries.
    pub(crate) fn len(&self) -> usize {
        self.entries.len()
    }

    /// Return the entry at the given index, if in range.
    pub(crate) fn get(&self, index: usize) -> Option<&GraphNodeRef> {
        self.entries.get(index)
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
    /// Stack-based navigation state.
    pub(crate) nav: NavStack,
    /// Session history of visited nodes (most recent first).
    pub(crate) history: SessionHistory,
    /// Current loading phase — determines whether loading indicators are shown.
    pub(crate) loading_state: LoadingState,
    /// Receiver for phase-2 data from the background Python thread.
    /// `None` once the data has been received (or if no background thread
    /// was spawned).
    phase2_rx: Option<mpsc::Receiver<Phase2Data>>,
    /// Base names of parametrize groups that are currently expanded in the
    /// Test NodeList.  A group's base name is the node_id prefix before the
    /// `[param_id]` bracket (e.g. `"tests/test_math.py::test_add"`).
    pub(crate) expanded_groups: HashSet<String>,
}

impl InspectApp {
    /// Create a new `InspectApp` with default state and `LoadingState::Complete`.
    ///
    /// If `name` is provided and a graph is available, resolves it
    /// against the graph for direct-jump navigation:
    /// - 1 match: jumps straight to that node's kind list.
    /// - N matches: opens a disambiguation screen.
    /// - 0 matches: stays on the Home screen.
    ///
    /// Used by tests and by callers that do not need progressive loading.
    #[cfg(test)]
    pub(crate) fn new(graph: Option<InspectGraph>, name: Option<&str>) -> Self {
        let nav = match (&graph, name) {
            (Some(g), Some(n)) => nav::resolve_direct_jump(g, n),
            _ => NavStack::new(),
        };
        Self {
            should_quit: false,
            terminal_width: 0,
            input_mode: InputMode::Normal,
            show_help: false,
            search: SearchState::new(),
            graph,
            nav,
            history: SessionHistory::new(),
            loading_state: LoadingState::Complete,
            phase2_rx: None,
            expanded_groups: HashSet::new(),
        }
    }

    /// Create a new `InspectApp` with phase-1 graph and a receiver for phase-2 data.
    ///
    /// If `name` is provided, resolves it against the graph for direct-jump
    /// navigation (same logic as `new()`).
    pub(crate) fn with_progressive_loading(
        graph: InspectGraph,
        rx: mpsc::Receiver<Phase2Data>,
        name: Option<&str>,
    ) -> Self {
        let nav = match name {
            Some(n) => nav::resolve_direct_jump(&graph, n),
            None => NavStack::new(),
        };
        Self {
            should_quit: false,
            terminal_width: 0,
            input_mode: InputMode::Normal,
            show_help: false,
            search: SearchState::new(),
            graph: Some(graph),
            nav,
            history: SessionHistory::new(),
            loading_state: LoadingState::InstantOnly,
            phase2_rx: Some(rx),
            expanded_groups: HashSet::new(),
        }
    }

    /// Main event loop: poll for events, dispatch input, and draw.
    pub(crate) fn run(
        &mut self,
        terminal: &mut Terminal<CrosstermBackend<std::io::Stdout>>,
    ) -> Result<(), Box<dyn std::error::Error>> {
        loop {
            // Check for phase-2 data from the background thread.
            self.poll_phase2();

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

    /// Non-blocking check for phase-2 data arrival.
    ///
    /// When the background thread sends fixture and plugin data, this
    /// method merges it into the existing graph and transitions to
    /// `LoadingState::Complete`.
    fn poll_phase2(&mut self) {
        let rx = match &self.phase2_rx {
            Some(rx) => rx,
            None => return,
        };

        match rx.try_recv() {
            Ok(data) => {
                self.merge_phase2(data);
                self.phase2_rx = None;
            }
            Err(mpsc::TryRecvError::Empty) => {
                // Not ready yet — keep waiting.
            }
            Err(mpsc::TryRecvError::Disconnected) => {
                // Background thread finished without sending data (error path).
                // Transition to Complete so loading indicators disappear.
                self.loading_state = LoadingState::Complete;
                self.phase2_rx = None;
            }
        }
    }

    /// Merge phase-2 fixture and plugin data into the existing graph.
    fn merge_phase2(&mut self, data: Phase2Data) {
        use super::graph::builder::GraphBuilder;

        if let Some(existing_graph) = self.graph.take() {
            let mut builder = GraphBuilder::from_graph(existing_graph);
            builder.add_fixture_entries(&data.fixture_entries);
            builder.add_plugin_entries(&data.plugin_entries);
            builder.resolve_edges();
            self.graph = Some(builder.build());
        }

        self.loading_state = LoadingState::Complete;
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_app_starts_in_normal_mode() {
        let app = InspectApp::new(None, None);
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
    fn new_app_has_empty_history() {
        let app = InspectApp::new(None, None);
        assert_eq!(
            app.history.len(),
            0,
            "history should start empty on creation"
        );
    }

    #[test]
    fn new_app_has_empty_search_state() {
        let app = InspectApp::new(None, None);
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

    // ── LoadingState tests ──────────────────────────────────────────────

    #[test]
    fn new_app_defaults_to_complete_loading_state() {
        let app = InspectApp::new(None, None);
        assert_eq!(
            app.loading_state,
            LoadingState::Complete,
            "new() should default to Complete loading state"
        );
    }

    #[test]
    fn progressive_loading_starts_in_instant_only() {
        let graph = InspectGraph::default();
        let (_tx, rx) = mpsc::channel::<Phase2Data>();
        let app = InspectApp::with_progressive_loading(graph, rx, None);
        assert_eq!(
            app.loading_state,
            LoadingState::InstantOnly,
            "with_progressive_loading should start in InstantOnly state"
        );
        assert!(
            app.graph.is_some(),
            "with_progressive_loading should have a graph set"
        );
    }

    #[test]
    fn merge_phase2_transitions_to_complete() {
        use crate::query::resource::QueryEntry;

        let graph = InspectGraph::default();
        let (_tx, rx) = mpsc::channel::<Phase2Data>();
        let mut app = InspectApp::with_progressive_loading(graph, rx, None);

        let data = Phase2Data {
            fixture_entries: vec![QueryEntry {
                fields: [
                    ("name".to_string(), "db".to_string()),
                    ("source".to_string(), "conftest.py".to_string()),
                    ("type".to_string(), "fixture".to_string()),
                    ("scope".to_string(), "function".to_string()),
                    ("autouse".to_string(), "false".to_string()),
                    ("async".to_string(), "false".to_string()),
                    ("description".to_string(), String::new()),
                ]
                .into_iter()
                .collect(),
            }],
            plugin_entries: vec![],
        };

        app.merge_phase2(data);
        assert_eq!(
            app.loading_state,
            LoadingState::Complete,
            "merge_phase2 should transition to Complete"
        );
        assert_eq!(
            app.graph.as_ref().unwrap().fixtures.len(),
            1,
            "merged graph should contain the fixture from phase 2"
        );
    }

    #[test]
    fn poll_phase2_receives_data() {
        use crate::query::resource::QueryEntry;

        let graph = InspectGraph::default();
        let (tx, rx) = mpsc::channel::<Phase2Data>();
        let mut app = InspectApp::with_progressive_loading(graph, rx, None);

        tx.send(Phase2Data {
            fixture_entries: vec![QueryEntry {
                fields: [
                    ("name".to_string(), "cache".to_string()),
                    ("source".to_string(), "<plugin:cache>".to_string()),
                    ("type".to_string(), "fixture".to_string()),
                    ("scope".to_string(), "function".to_string()),
                    ("autouse".to_string(), "false".to_string()),
                    ("async".to_string(), "false".to_string()),
                    ("description".to_string(), String::new()),
                ]
                .into_iter()
                .collect(),
            }],
            plugin_entries: vec![QueryEntry {
                fields: [
                    ("name".to_string(), "cache".to_string()),
                    ("protocol".to_string(), String::new()),
                ]
                .into_iter()
                .collect(),
            }],
        })
        .expect("send should succeed while receiver exists");

        app.poll_phase2();

        assert_eq!(
            app.loading_state,
            LoadingState::Complete,
            "poll_phase2 should transition to Complete after receiving data"
        );
        let graph = app.graph.as_ref().unwrap();
        assert_eq!(
            graph.fixtures.len(),
            1,
            "graph should have one fixture after phase-2 merge"
        );
        assert_eq!(
            graph.plugins.len(),
            1,
            "graph should have one plugin after phase-2 merge"
        );
    }

    #[test]
    fn poll_phase2_handles_disconnected_sender() {
        let graph = InspectGraph::default();
        let (tx, rx) = mpsc::channel::<Phase2Data>();
        let mut app = InspectApp::with_progressive_loading(graph, rx, None);

        // Drop the sender to simulate background thread failure.
        drop(tx);

        app.poll_phase2();

        assert_eq!(
            app.loading_state,
            LoadingState::Complete,
            "poll_phase2 should transition to Complete on Disconnected"
        );
        assert!(
            app.phase2_rx.is_none(),
            "phase2_rx should be cleared after Disconnected"
        );
    }

    #[test]
    fn poll_phase2_noop_when_empty() {
        let graph = InspectGraph::default();
        let (_tx, rx) = mpsc::channel::<Phase2Data>();
        let mut app = InspectApp::with_progressive_loading(graph, rx, None);

        // Sender exists but hasn't sent anything yet.
        app.poll_phase2();

        assert_eq!(
            app.loading_state,
            LoadingState::InstantOnly,
            "poll_phase2 should stay in InstantOnly when channel is empty"
        );
        assert!(
            app.phase2_rx.is_some(),
            "phase2_rx should remain while waiting for data"
        );
    }

    // ── SessionHistory tests ──────────────────────────────────────────

    #[test]
    fn session_history_starts_empty() {
        let history = SessionHistory::new();
        assert_eq!(
            history.len(),
            0,
            "new SessionHistory should have zero entries"
        );
        assert!(
            history.get(0).is_none(),
            "get(0) on empty history should return None"
        );
    }

    #[test]
    fn session_history_push_prepends() {
        use crate::inspect::graph::{NodeKind, NodeRef as GraphNodeRef};

        let mut history = SessionHistory::new();
        let first = GraphNodeRef {
            kind: NodeKind::Test,
            index: 0,
        };
        let second = GraphNodeRef {
            kind: NodeKind::Fixture,
            index: 1,
        };

        history.push(first.clone());
        history.push(second.clone());

        assert_eq!(
            history.len(),
            2,
            "history should contain 2 entries after 2 pushes"
        );
        assert_eq!(
            history.get(0),
            Some(&second),
            "most recent push should be at index 0"
        );
        assert_eq!(
            history.get(1),
            Some(&first),
            "first push should be at index 1"
        );
    }

    #[test]
    fn session_history_allows_duplicates() {
        use crate::inspect::graph::{NodeKind, NodeRef as GraphNodeRef};

        let mut history = SessionHistory::new();
        let node = GraphNodeRef {
            kind: NodeKind::Test,
            index: 0,
        };

        history.push(node.clone());
        history.push(node.clone());

        assert_eq!(
            history.len(),
            2,
            "pushing the same node twice should create 2 entries"
        );
    }
}
