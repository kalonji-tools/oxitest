//! Application state and event loop for `oxitest inspect`.

use std::collections::HashSet;
use std::sync::mpsc;

use crossterm::event::{self, Event};
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;

use super::graph::{InspectGraph, NodeRef};
use super::input;
use super::nav::{self, Screen, Trail};
use super::overview::OverviewSections;
use super::ui;

// ── RefreshArgs ──────────────────────────────────────────────────────────────

/// Arguments needed to re-run file collection and graph construction
/// when the user presses `r` to refresh.
pub(crate) struct RefreshArgs {
    pub inspect_args: crate::config::cli::InspectArgs,
    pub config: crate::config::Config,
}

// ── InputMode ────────────────────────────────────────────────────────────────

/// Current input mode of the TUI.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum InputMode {
    /// Normal navigation mode.
    Normal,
    /// Search mode — keystrokes append to the query string.
    Search { query: String },
}

// ── ScopeMode ────────────────────────────────────────────────────────────────

/// Whether search scans the current screen's nodes or the entire graph.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ScopeMode {
    /// Search only nodes visible on the current screen.
    Context,
    /// Search all nodes in the graph.
    Global,
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
    /// Total number of searchable candidates (for "N/M matches" display).
    pub(crate) total_candidates: usize,
    /// Whether to search the current screen's context or the entire graph.
    pub(crate) scope_mode: ScopeMode,
}

impl SearchState {
    /// Create a new empty search state.
    pub(crate) fn new() -> Self {
        Self {
            query: String::new(),
            results: Vec::new(),
            selected_idx: 0,
            total_candidates: 0,
            scope_mode: ScopeMode::Context,
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
        self.results.get(self.selected_idx).cloned()
    }
}

// ── Phase2State ───────────────────────────────────────────────────────────────

/// Tracks the progressive loading phase of the inspect graph.
///
/// Phase 1 (instant-tier) data is available immediately from Rust AST
/// extraction.  Phase 2 (fixture/plugin) data requires a Python session
/// and arrives asynchronously via a background thread.
enum Phase2State {
    /// Background thread is active: receiver and start instant are held here.
    Loading {
        rx: mpsc::Receiver<Phase2Data>,
        started: std::time::Instant,
    },
    /// All data has been loaded (or timed out / thread disconnected).
    Complete,
}

// ── Phase2Data ──────────────────────────────────────────────────────────────

/// Payload sent from the background thread once Python-tier data is ready.
pub(crate) struct Phase2Data {
    pub(crate) fixture_entries: Vec<crate::query::resource::QueryEntry>,
    pub(crate) plugin_entries: Vec<crate::query::resource::QueryEntry>,
    pub(crate) fixture_dep_entries: Vec<crate::query::resource::QueryEntry>,
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
    pub(crate) entries: Vec<NodeRef>,
}

impl SessionHistory {
    /// Create a new empty history.
    pub(crate) fn new() -> Self {
        Self {
            entries: Vec::new(),
        }
    }

    /// Record a visit to the given node (prepended, so index 0 is most recent).
    pub(crate) fn push(&mut self, node: NodeRef) {
        self.entries.insert(0, node);
    }

    /// Return the number of entries.
    pub(crate) fn len(&self) -> usize {
        self.entries.len()
    }

    /// Return the entry at the given index, if in range.
    pub(crate) fn get(&self, index: usize) -> Option<&NodeRef> {
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
    /// Trail-based navigation state (ADR-0003).
    pub(crate) nav: Trail,
    /// Session history of visited nodes (most recent first).
    pub(crate) history: SessionHistory,
    /// Pre-sorted overview sections derived from the graph.
    pub(crate) overview_sections: OverviewSections,
    /// Flash message displayed briefly after user actions (e.g. "back to overview").
    pub(crate) flash_message: Option<(String, std::time::Instant)>,
    /// Progressive-loading state: holds the background receiver while loading,
    /// transitions to `Complete` once data arrives (or times out / disconnects).
    phase2: Phase2State,
    /// Base names of parametrize groups that are currently expanded in the
    /// Test NodeList.  A group's base name is the node_id prefix before the
    /// `[param_id]` bracket (e.g. `"tests/test_math.py::test_add"`).
    #[allow(dead_code)] // retained for future parametrize collapsing in edge lists
    pub(crate) expanded_groups: HashSet<String>,
    /// Vertical scroll offset for the left pane.
    pub(crate) scroll_offset: u16,
    /// Project root directory for stripping absolute paths in the TUI.
    pub(crate) rootdir: String,
    /// How long to wait for phase-2 (Python-tier) data before giving up.
    phase2_timeout: std::time::Duration,
    /// Arguments needed for manual graph refresh (`r` key).
    /// `None` in test-only construction; `Some` when launched from `run()`.
    refresh_args: Option<RefreshArgs>,
}

impl InspectApp {
    /// Create a new `InspectApp` with default state and `Phase2State::Complete`.
    ///
    /// If `name` is provided and a graph is available, resolves it
    /// against the graph for direct-jump navigation:
    /// - 1 match: jumps straight to that node's detail.
    /// - N matches: opens a disambiguation screen.
    /// - 0 matches: stays on the Overview screen.
    ///
    /// Used by tests and by callers that do not need progressive loading.
    #[cfg(test)]
    pub(crate) fn new(graph: Option<InspectGraph>, name: Option<&str>) -> Self {
        let nav = match (&graph, name) {
            (Some(g), Some(n)) => nav::resolve_direct_jump(g, n),
            _ => Trail::new(),
        };
        let overview_sections = match &graph {
            Some(g) => OverviewSections::from_graph(g),
            None => OverviewSections::default(),
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
            overview_sections,
            flash_message: None,
            phase2: Phase2State::Complete,
            expanded_groups: HashSet::new(),
            scroll_offset: 0,
            rootdir: String::new(),
            phase2_timeout: std::time::Duration::from_secs(30),
            refresh_args: None,
        }
    }

    /// Create a new `InspectApp` with phase-1 graph and a receiver for phase-2 data.
    ///
    /// `timeout` controls how long the app waits for the background Python
    /// session before giving up and proceeding with instant-tier data only.
    ///
    /// If `name` is provided, resolves it against the graph for direct-jump
    /// navigation (same logic as `new()`).
    pub(crate) fn with_progressive_loading(
        graph: InspectGraph,
        rx: mpsc::Receiver<Phase2Data>,
        name: Option<&str>,
        timeout: std::time::Duration,
        rootdir: &str,
        refresh_args: Option<RefreshArgs>,
    ) -> Self {
        let nav = match name {
            Some(n) => nav::resolve_direct_jump(&graph, n),
            None => Trail::new(),
        };
        let total_candidates = graph.all_node_refs().len();
        let mut search = SearchState::new();
        search.total_candidates = total_candidates;
        let overview_sections = OverviewSections::from_graph(&graph);
        Self {
            should_quit: false,
            terminal_width: 0,
            input_mode: InputMode::Normal,
            show_help: false,
            search,
            graph: Some(graph),
            nav,
            history: SessionHistory::new(),
            overview_sections,
            flash_message: None,
            phase2: Phase2State::Loading {
                rx,
                started: std::time::Instant::now(),
            },
            expanded_groups: HashSet::new(),
            scroll_offset: 0,
            rootdir: rootdir.to_string(),
            phase2_timeout: timeout,
            refresh_args,
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
            self.clear_expired_flash();

            // Update terminal dimensions and scroll offset before drawing.
            let term_size = terminal.size()?;
            self.terminal_width = term_size.width;
            // Main area height = total - breadcrumb (1) - footer (1).
            let viewport_height = term_size.height.saturating_sub(2);
            ui::update_scroll(self, viewport_height);

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
    /// `Phase2State::Complete`.
    fn poll_phase2(&mut self) {
        // Take ownership to avoid borrow conflict between rx.try_recv() and
        // merge_phase2(&mut self).  If we're already Complete, return early.
        let Phase2State::Loading { ref rx, started } = self.phase2 else {
            return;
        };
        match rx.try_recv() {
            Ok(data) => {
                self.phase2 = Phase2State::Complete;
                self.merge_phase2(data);
            }
            Err(mpsc::TryRecvError::Empty) => {
                if started.elapsed() > self.phase2_timeout {
                    tracing::warn!(
                        timeout_secs = self.phase2_timeout.as_secs(),
                        "phase-2 loading timed out; proceeding with instant-tier data only"
                    );
                    self.phase2 = Phase2State::Complete;
                }
            }
            Err(mpsc::TryRecvError::Disconnected) => {
                // Background thread finished without sending data (error path).
                // Transition to Complete so loading indicators disappear.
                self.phase2 = Phase2State::Complete;
            }
        }
    }

    /// Returns `true` while phase-2 (fixture/plugin) data is still loading.
    pub(crate) fn is_loading(&self) -> bool {
        matches!(self.phase2, Phase2State::Loading { .. })
    }

    /// Merge phase-2 fixture and plugin data into the existing graph.
    fn merge_phase2(&mut self, data: Phase2Data) {
        use super::graph::builder::GraphBuilder;

        if let Some(existing_graph) = self.graph.take() {
            let mut builder = GraphBuilder::from_graph(existing_graph);
            builder.add_fixture_entries(&data.fixture_entries);
            builder.add_plugin_entries(&data.plugin_entries);
            builder.add_fixture_dep_entries(&data.fixture_dep_entries, &self.rootdir);
            builder.resolve_edges();
            let mut new_graph = builder.build();
            // Normalize paths from phase-2 (Python bridge sends absolute paths).
            new_graph.relativize_paths(&self.rootdir);
            // Update total_candidates to reflect the now-complete graph so the
            // "N/M matches" display accounts for fixture and plugin nodes.
            self.search.total_candidates = new_graph.all_node_refs().len();
            // Rebuild overview sections from the now-complete graph.
            self.overview_sections = OverviewSections::from_graph(&new_graph);
            self.graph = Some(new_graph);
        }

        // Reset nav to Overview if current screen holds potentially stale NodeRefs.
        // Phase-2 adds fixtures/plugins — Disambiguation and NodeFocus screens
        // may reference fixture/plugin indices that shifted during rebuild.
        match self.nav.current() {
            Screen::Disambiguation { .. } | Screen::NodeFocus { .. } => {
                self.nav = Trail::new();
            }
            _ => {}
        }
    }

    /// Set a flash message that auto-clears after 2 seconds.
    pub(crate) fn flash(&mut self, message: impl Into<String>) {
        self.flash_message = Some((message.into(), std::time::Instant::now()));
    }

    /// Clear the flash message if it has expired (> 2 seconds).
    pub(crate) fn clear_expired_flash(&mut self) {
        if let Some((_, instant)) = &self.flash_message
            && instant.elapsed() > std::time::Duration::from_secs(2)
        {
            self.flash_message = None;
        }
    }

    /// Returns `true` while phase-2 data is still loading (alias for `is_loading`).
    #[allow(dead_code)] // convenience alias for future callers
    pub(crate) fn is_phase2_loading(&self) -> bool {
        self.is_loading()
    }

    /// Re-collect files, rebuild the phase-1 graph, and spawn a new phase-2
    /// background thread.  Called when the user presses `r`.
    ///
    /// Startup filters (`-E`, `--affected`, `--lf`) are re-applied because
    /// `build_phase1_graph` reads them directly from the stored `InspectArgs`.
    /// Any runtime state (scroll position, nav trail) is pruned via
    /// `prune_stale_trail` to avoid stale node-index panics after a refresh.
    pub(crate) fn refresh(&mut self) {
        let Some(ref rargs) = self.refresh_args else {
            return;
        };

        // Re-collect files.
        let Ok((test_files, conftest_files)) = crate::collector::collect_files(&rargs.config)
        else {
            self.flash("Refresh failed: file collection error");
            return;
        };

        // Clone test files for phase 2 before phase 1 consumes the original.
        let test_files_for_phase2 = test_files.clone();

        // Rebuild phase-1 graph.
        let Ok(mut graph) = super::build_phase1_graph(
            &rargs.inspect_args,
            &rargs.config,
            test_files,
            &conftest_files,
        ) else {
            self.flash("Refresh failed: graph build error");
            return;
        };
        graph.relativize_paths(&self.rootdir);

        // Spawn phase-2 in background.
        let (tx, rx) = std::sync::mpsc::channel();
        super::spawn_phase2(
            conftest_files,
            test_files_for_phase2,
            rargs.config.features.plugins.clone(),
            rargs.config.features.plugin_settings.clone(),
            tx,
        );

        // Swap graph and reset dependent state.
        self.search.total_candidates = graph.all_node_refs().len();
        self.overview_sections = super::overview::OverviewSections::from_graph(&graph);
        self.graph = Some(graph);
        self.phase2 = Phase2State::Loading {
            rx,
            started: std::time::Instant::now(),
        };

        self.prune_stale_trail();
        self.flash("Refreshed");
    }

    /// Pop trail screens that reference nodes beyond the new graph's bounds.
    ///
    /// After a refresh the graph may have fewer nodes than before.  Any
    /// `NodeFocus` screen whose `node.index` exceeds the new node count for
    /// that kind is stale and must be removed to prevent panics.
    fn prune_stale_trail(&mut self) {
        let graph = match &self.graph {
            Some(g) => g,
            None => return,
        };
        // Pop NodeFocus screens that reference nodes beyond the graph's bounds.
        while let Screen::NodeFocus { ref node, .. } = *self.nav.current() {
            if node.index < graph.node_count(node.kind) {
                break; // Node still exists
            }
            if !self.nav.pop() {
                break; // At overview root
            }
        }
        self.scroll_offset = 0;
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
        use super::super::graph::NodeKind;
        let mut state = SearchState::new();
        state.results = vec![
            NodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            NodeRef {
                kind: NodeKind::Test,
                index: 1,
            },
            NodeRef {
                kind: NodeKind::Test,
                index: 2,
            },
        ];
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
        use super::super::graph::NodeKind;
        let mut state = SearchState::new();
        state.results = vec![
            NodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            NodeRef {
                kind: NodeKind::Test,
                index: 1,
            },
            NodeRef {
                kind: NodeKind::Test,
                index: 2,
            },
        ];
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
        use super::super::graph::NodeKind;
        let node_a = NodeRef {
            kind: NodeKind::Test,
            index: 5,
        };
        let node_b = NodeRef {
            kind: NodeKind::Fixture,
            index: 10,
        };
        let mut state = SearchState::new();
        assert!(
            state.selected().is_none(),
            "selected on empty results should return None"
        );
        state.results = vec![node_a.clone(), node_b.clone()];
        assert_eq!(
            state.selected(),
            Some(node_a.clone()),
            "selected at index 0 should return first result"
        );
        state.select_next();
        assert_eq!(
            state.selected(),
            Some(node_b.clone()),
            "selected at index 1 should return second result"
        );
    }

    // ── LoadingState tests ──────────────────────────────────────────────

    #[test]
    fn new_app_defaults_to_complete_loading_state() {
        let app = InspectApp::new(None, None);
        assert!(
            !app.is_loading(),
            "new() should default to Complete loading state (is_loading must be false)"
        );
    }

    #[test]
    fn progressive_loading_starts_in_instant_only() {
        let graph = InspectGraph::default();
        let (_tx, rx) = mpsc::channel::<Phase2Data>();
        let app = InspectApp::with_progressive_loading(
            graph,
            rx,
            None,
            std::time::Duration::from_secs(30),
            "",
            None,
        );
        assert!(
            app.is_loading(),
            "with_progressive_loading should start in Loading state (is_loading must be true)"
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
        let mut app = InspectApp::with_progressive_loading(
            graph,
            rx,
            None,
            std::time::Duration::from_secs(30),
            "",
            None,
        );

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
            fixture_dep_entries: vec![],
        };

        // merge_phase2 is called after phase2 is already set to Complete by poll_phase2;
        // call it directly here as a unit test, so set phase2 = Complete first.
        app.phase2 = Phase2State::Complete;
        app.merge_phase2(data);
        assert!(
            !app.is_loading(),
            "after merge_phase2 the app must not be in loading state"
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
        let mut app = InspectApp::with_progressive_loading(
            graph,
            rx,
            None,
            std::time::Duration::from_secs(30),
            "",
            None,
        );

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
            fixture_dep_entries: vec![],
        })
        .expect("send should succeed while receiver exists");

        app.poll_phase2();

        assert!(
            !app.is_loading(),
            "poll_phase2 should transition to Complete (is_loading false) after receiving data"
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
        let mut app = InspectApp::with_progressive_loading(
            graph,
            rx,
            None,
            std::time::Duration::from_secs(30),
            "",
            None,
        );

        // Drop the sender to simulate background thread failure.
        drop(tx);

        app.poll_phase2();

        assert!(
            !app.is_loading(),
            "poll_phase2 should transition to Complete (is_loading false) on Disconnected"
        );
        assert!(
            matches!(app.phase2, Phase2State::Complete),
            "phase2 must be Complete after Disconnected so no further polls are attempted"
        );
    }

    #[test]
    fn poll_phase2_noop_when_empty() {
        let graph = InspectGraph::default();
        let (_tx, rx) = mpsc::channel::<Phase2Data>();
        let mut app = InspectApp::with_progressive_loading(
            graph,
            rx,
            None,
            std::time::Duration::from_secs(30),
            "",
            None,
        );

        // Sender exists but hasn't sent anything yet.
        app.poll_phase2();

        assert!(
            app.is_loading(),
            "poll_phase2 should stay Loading (is_loading true) when channel is empty"
        );
        assert!(
            matches!(app.phase2, Phase2State::Loading { .. }),
            "phase2 must remain Loading while waiting for data"
        );
    }

    #[test]
    fn poll_phase2_times_out_after_deadline() {
        let graph = InspectGraph::default();
        let (_tx, rx) = mpsc::channel::<Phase2Data>();
        let mut app = InspectApp::with_progressive_loading(
            graph,
            rx,
            None,
            std::time::Duration::from_secs(30),
            "",
            None,
        );

        // Backdate the start time to 31 seconds ago to simulate an elapsed timeout.
        let backdated = std::time::Instant::now()
            .checked_sub(std::time::Duration::from_secs(31))
            .expect("system clock must support subtracting 31s");
        // Replace the Loading variant with the backdated started instant.
        let old = std::mem::replace(&mut app.phase2, Phase2State::Complete);
        let Phase2State::Loading { rx: old_rx, .. } = old else {
            panic!("app should be in Loading state at this point in the test");
        };
        app.phase2 = Phase2State::Loading {
            rx: old_rx,
            started: backdated,
        };

        app.poll_phase2();

        assert!(
            !app.is_loading(),
            "poll_phase2 must transition to Complete (is_loading false) once the 30s deadline \
             is exceeded so loading indicators disappear and the UI is not stuck indefinitely"
        );
        assert!(
            matches!(app.phase2, Phase2State::Complete),
            "phase2 must be Complete after timeout so no further polls are attempted"
        );
    }

    #[test]
    fn poll_phase2_no_timeout_before_deadline() {
        let graph = InspectGraph::default();
        let (_tx, rx) = mpsc::channel::<Phase2Data>();
        let mut app = InspectApp::with_progressive_loading(
            graph,
            rx,
            None,
            std::time::Duration::from_secs(30),
            "",
            None,
        );

        // started is set to Instant::now() by with_progressive_loading,
        // so elapsed() will be far below 30s — the timeout must NOT trigger.
        app.poll_phase2();

        assert!(
            app.is_loading(),
            "poll_phase2 must stay Loading (is_loading true) before the 30s deadline so the \
             UI continues waiting for phase-2 data rather than discarding it early"
        );
        assert!(
            matches!(app.phase2, Phase2State::Loading { .. }),
            "phase2 must remain Loading before the deadline so the channel is not prematurely dropped"
        );
    }

    #[test]
    fn merge_phase2_resets_nav_when_on_detail_screen() {
        // Create app with progressive loading, push a NodeFocus screen,
        // then merge phase-2 data. Nav should reset to Overview.
        use super::super::graph::{NodeKind, NodeRef};
        use super::super::nav::Screen;

        let graph = InspectGraph::default();
        let (_tx, rx) = mpsc::channel::<Phase2Data>();
        let mut app = InspectApp::with_progressive_loading(
            graph,
            rx,
            None,
            std::time::Duration::from_secs(30),
            "",
            None,
        );

        // Push a NodeFocus screen (simulating user navigated to a node)
        app.nav.push(Screen::NodeFocus {
            node: NodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            selected: 0,
        });

        // Simulate phase-2 data arrival
        app.phase2 = Phase2State::Complete; // mirror what poll_phase2 does
        app.merge_phase2(Phase2Data {
            fixture_entries: vec![],
            plugin_entries: vec![],
            fixture_dep_entries: vec![],
        });

        assert!(
            matches!(app.nav.current(), Screen::Overview { .. }),
            "nav should reset to Overview after phase-2 merge when on NodeFocus — \
             stale NodeRef indices could cause panics if the user navigates"
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

    // ── prune_stale_trail tests ──────────────────────────────────────

    #[test]
    fn prune_stale_trail_pops_invalid_nodes() {
        use super::super::graph::nodes::TestNode;
        use super::super::graph::{NodeKind, NodeRef};
        use super::super::nav::Screen;

        // Build a graph with 6 test nodes.
        let mut graph = InspectGraph::default();
        for i in 0..6 {
            graph.tests.push(TestNode {
                node_id: format!("t.py::test_{i}"),
                is_async: false,
                param_id: None,
                param_count: 0,
                variants: vec![],
                fixture_deps: vec![],
                marks: vec![],
            });
        }

        let mut app = InspectApp::new(Some(graph), None);

        // Push NodeFocus for index 5 (valid in the 6-node graph).
        app.nav.push(Screen::NodeFocus {
            node: NodeRef {
                kind: NodeKind::Test,
                index: 5,
            },
            selected: 0,
        });

        // Replace graph with one that has only 3 test nodes.
        let mut small_graph = InspectGraph::default();
        for i in 0..3 {
            small_graph.tests.push(TestNode {
                node_id: format!("t.py::test_{i}"),
                is_async: false,
                param_id: None,
                param_count: 0,
                variants: vec![],
                fixture_deps: vec![],
                marks: vec![],
            });
        }
        app.graph = Some(small_graph);

        app.prune_stale_trail();

        assert!(
            matches!(app.nav.current(), Screen::Overview { .. }),
            "prune_stale_trail should pop NodeFocus with index 5 when graph has only 3 nodes — \
             stale indices would cause panics on navigation"
        );
    }

    #[test]
    fn prune_stale_trail_keeps_valid_nodes() {
        use super::super::graph::nodes::TestNode;
        use super::super::graph::{NodeKind, NodeRef};
        use super::super::nav::Screen;

        // Build a graph with 3 test nodes.
        let mut graph = InspectGraph::default();
        for i in 0..3 {
            graph.tests.push(TestNode {
                node_id: format!("t.py::test_{i}"),
                is_async: false,
                param_id: None,
                param_count: 0,
                variants: vec![],
                fixture_deps: vec![],
                marks: vec![],
            });
        }

        let mut app = InspectApp::new(Some(graph), None);

        // Push NodeFocus for index 0 (still valid after graph replacement).
        app.nav.push(Screen::NodeFocus {
            node: NodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            selected: 0,
        });

        // Replace graph with another that still has index 0.
        let mut new_graph = InspectGraph::default();
        for i in 0..2 {
            new_graph.tests.push(TestNode {
                node_id: format!("t.py::test_{i}"),
                is_async: false,
                param_id: None,
                param_count: 0,
                variants: vec![],
                fixture_deps: vec![],
                marks: vec![],
            });
        }
        app.graph = Some(new_graph);

        app.prune_stale_trail();

        assert!(
            matches!(app.nav.current(), Screen::NodeFocus { .. }),
            "prune_stale_trail should keep NodeFocus with index 0 when graph still has that node — \
             valid screens should not be discarded"
        );
    }
}
