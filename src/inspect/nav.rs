//! Stack-based navigation for `oxitest inspect`.
//!
//! The [`NavStack`] manages the current screen and provides push/pop
//! semantics.  [`NavScreen`] variants represent the possible screens:
//! Home, NodeList, NodeDetail, and Disambiguation.
//!
//! Home is always the bottom of the stack and can never be popped.

use super::graph::{InspectGraph, NodeKind, NodeRef};

// ── Display order ────────────────────────────────────────────────────────────

/// The six node kinds in the order they appear on the Home screen.
pub(crate) const HOME_KINDS: [(NodeKind, &str); 6] = [
    (NodeKind::Test, "Tests"),
    (NodeKind::Fixture, "Fixtures"),
    (NodeKind::Mark, "Marks"),
    (NodeKind::Conftest, "Conftests"),
    (NodeKind::Plugin, "Plugins"),
    (NodeKind::Helper, "Helpers"),
];

// ── NavScreen ────────────────────────────────────────────────────────────────

/// A single screen in the navigation stack.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum NavScreen {
    /// Home screen: lists node kinds with sigils and counts.
    /// `selected` is the cursor index among the *visible* kinds (those
    /// with count > 0).
    Home { selected: usize },

    /// List of all nodes of a specific kind.
    /// `selected` is the cursor index within the list.
    NodeList { kind: NodeKind, selected: usize },

    /// Detail view for a single node (placeholder for #1117).
    NodeDetail { node: NodeRef },

    /// Disambiguation: multiple nodes matched a direct-jump name.
    /// `selected` is the cursor index within `matches`.
    Disambiguation {
        query: String,
        matches: Vec<NodeRef>,
        selected: usize,
    },
}

// ── NavStack ─────────────────────────────────────────────────────────────────

/// A non-empty stack of [`NavScreen`]s.
///
/// The bottom entry is always `Home`; `pop()` refuses to remove it.
#[derive(Debug)]
pub(crate) struct NavStack {
    stack: Vec<NavScreen>,
}

impl NavStack {
    /// Create a new stack starting at the Home screen.
    pub(crate) fn new() -> Self {
        Self {
            stack: vec![NavScreen::Home { selected: 0 }],
        }
    }

    /// Return the current (top-of-stack) screen.
    pub(crate) fn current(&self) -> &NavScreen {
        self.stack
            .last()
            .expect("NavStack invariant violated: stack must never be empty")
    }

    /// Return a mutable reference to the current screen.
    pub(crate) fn current_mut(&mut self) -> &mut NavScreen {
        self.stack
            .last_mut()
            .expect("NavStack invariant violated: stack must never be empty")
    }

    /// Push a new screen onto the stack.
    pub(crate) fn push(&mut self, screen: NavScreen) {
        self.stack.push(screen);
    }

    /// Pop the top screen, unless it is the Home screen.
    ///
    /// Returns `true` if a screen was popped, `false` if already at Home.
    pub(crate) fn pop(&mut self) -> bool {
        if self.stack.len() > 1 {
            self.stack.pop();
            true
        } else {
            false
        }
    }

    /// Return the depth of the stack (1 = Home only).
    #[allow(dead_code)] // useful for breadcrumb rendering (#1117)
    pub(crate) fn depth(&self) -> usize {
        self.stack.len()
    }
}

// ── Direct jump resolution ──────────────────────────────────────────────────

/// Resolve a name against the graph for direct-jump navigation.
///
/// Returns the appropriate initial `NavStack`:
/// - No matches: Home screen (name is ignored).
/// - One match: stack with Home + NodeList for the matching kind, with
///   the cursor on the matched node.
/// - Multiple matches: stack with Home + Disambiguation screen.
pub(crate) fn resolve_direct_jump(graph: &InspectGraph, name: &str) -> NavStack {
    let name_lower = name.to_lowercase();
    let mut matches = Vec::new();

    for &(kind, _) in &HOME_KINDS {
        let count = graph.node_count(kind);
        for index in 0..count {
            let node_ref = NodeRef { kind, index };
            if graph
                .node_name(&node_ref)
                .to_lowercase()
                .contains(&name_lower)
            {
                matches.push(node_ref);
            }
        }
    }

    let mut nav = NavStack::new();

    match matches.len() {
        0 => {
            // No matches — stay on Home.
        }
        1 => {
            let matched = &matches[0];
            // Find which position in the NodeList this node is at.
            let kind = matched.kind;
            let selected = matched.index;
            // Find the Home screen cursor position for this kind.
            let home_idx = visible_kind_index(graph, kind);
            *nav.current_mut() = NavScreen::Home { selected: home_idx };
            nav.push(NavScreen::NodeList { kind, selected });
        }
        _ => {
            nav.push(NavScreen::Disambiguation {
                query: name.to_string(),
                matches,
                selected: 0,
            });
        }
    }

    nav
}

/// Return the index of `kind` among the visible (non-empty) kinds on Home.
fn visible_kind_index(graph: &InspectGraph, kind: NodeKind) -> usize {
    HOME_KINDS
        .iter()
        .filter(|(k, _)| graph.node_count(*k) > 0)
        .position(|(k, _)| *k == kind)
        .unwrap_or(0)
}

/// Return the number of visible (non-empty) kinds on Home.
pub(crate) fn visible_kind_count(graph: &InspectGraph) -> usize {
    HOME_KINDS
        .iter()
        .filter(|(k, _)| graph.node_count(*k) > 0)
        .count()
}

/// Return the `NodeKind` at the given visible index on Home.
///
/// Returns `None` if `index` is out of range.
pub(crate) fn visible_kind_at(graph: &InspectGraph, index: usize) -> Option<NodeKind> {
    HOME_KINDS
        .iter()
        .filter(|(k, _)| graph.node_count(*k) > 0)
        .nth(index)
        .map(|(k, _)| *k)
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::inspect::graph::nodes::{HelperNode, MarkNode, TestNode};

    /// Build a graph with known node counts for testing.
    fn test_graph() -> InspectGraph {
        let mut graph = InspectGraph::default();
        graph.tests.push(TestNode {
            node_id: "test_foo.py::test_bar".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph.tests.push(TestNode {
            node_id: "test_foo.py::test_baz".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph.marks.push(MarkNode {
            name: "slow".to_string(),
            used_by: vec![],
        });
        graph.helpers.push(HelperNode {
            name: "make_db".to_string(),
            signature: "make_db()".to_string(),
            docstring: None,
            source: "tests/conftest.py".to_string(),
            conftest_idx: 0,
        });
        graph
    }

    // ── NavStack basics ─────────────────────────────────────────────────

    #[test]
    fn new_stack_starts_at_home() {
        let nav = NavStack::new();
        assert_eq!(
            *nav.current(),
            NavScreen::Home { selected: 0 },
            "new NavStack should start at Home with selected = 0"
        );
    }

    #[test]
    fn push_changes_current_screen() {
        let mut nav = NavStack::new();
        nav.push(NavScreen::NodeList {
            kind: NodeKind::Test,
            selected: 0,
        });
        assert_eq!(
            *nav.current(),
            NavScreen::NodeList {
                kind: NodeKind::Test,
                selected: 0
            },
            "after push, current should be the pushed screen"
        );
    }

    #[test]
    fn pop_returns_to_previous_screen() {
        let mut nav = NavStack::new();
        nav.push(NavScreen::NodeList {
            kind: NodeKind::Test,
            selected: 0,
        });
        let popped = nav.pop();
        assert!(popped, "pop should return true when not at Home");
        assert_eq!(
            *nav.current(),
            NavScreen::Home { selected: 0 },
            "after pop, current should return to Home"
        );
    }

    #[test]
    fn pop_at_home_returns_false() {
        let mut nav = NavStack::new();
        let popped = nav.pop();
        assert!(
            !popped,
            "pop at Home should return false because Home cannot be removed"
        );
        assert_eq!(
            *nav.current(),
            NavScreen::Home { selected: 0 },
            "stack should remain at Home after failed pop"
        );
    }

    #[test]
    fn depth_tracks_stack_size() {
        let mut nav = NavStack::new();
        assert_eq!(
            nav.depth(),
            1,
            "initial stack depth should be 1 (Home only)"
        );
        nav.push(NavScreen::NodeList {
            kind: NodeKind::Test,
            selected: 0,
        });
        assert_eq!(nav.depth(), 2, "stack depth should be 2 after one push");
        nav.pop();
        assert_eq!(nav.depth(), 1, "stack depth should return to 1 after pop");
    }

    #[test]
    fn current_mut_allows_cursor_update() {
        let mut nav = NavStack::new();
        if let NavScreen::Home { selected } = nav.current_mut() {
            *selected = 3;
        }
        assert_eq!(
            *nav.current(),
            NavScreen::Home { selected: 3 },
            "current_mut should allow updating the cursor position"
        );
    }

    // ── Visible kind helpers ────────────────────────────────────────────

    #[test]
    fn visible_kind_count_skips_empty_kinds() {
        let graph = test_graph();
        // test_graph has: 2 tests, 1 mark, 1 helper = 3 visible kinds
        assert_eq!(
            visible_kind_count(&graph),
            3,
            "graph with tests, marks, helpers should have 3 visible kinds"
        );
    }

    #[test]
    fn visible_kind_at_returns_correct_kind() {
        let graph = test_graph();
        // Order: Test, Mark, Helper (Fixture/Conftest/Plugin are empty)
        assert_eq!(
            visible_kind_at(&graph, 0),
            Some(NodeKind::Test),
            "index 0 should be Test (first non-empty kind in display order)"
        );
        assert_eq!(
            visible_kind_at(&graph, 1),
            Some(NodeKind::Mark),
            "index 1 should be Mark (second non-empty kind)"
        );
        assert_eq!(
            visible_kind_at(&graph, 2),
            Some(NodeKind::Helper),
            "index 2 should be Helper (third non-empty kind)"
        );
        assert_eq!(
            visible_kind_at(&graph, 3),
            None,
            "index 3 should be None (out of range)"
        );
    }

    #[test]
    fn visible_kind_count_empty_graph() {
        let graph = InspectGraph::default();
        assert_eq!(
            visible_kind_count(&graph),
            0,
            "empty graph should have 0 visible kinds"
        );
    }

    // ── Direct jump ─────────────────────────────────────────────────────

    #[test]
    fn direct_jump_no_match_stays_home() {
        let graph = test_graph();
        let nav = resolve_direct_jump(&graph, "nonexistent");
        assert_eq!(
            *nav.current(),
            NavScreen::Home { selected: 0 },
            "unmatched direct jump should stay on Home"
        );
        assert_eq!(
            nav.depth(),
            1,
            "unmatched direct jump should not push any screen"
        );
    }

    #[test]
    fn direct_jump_single_match_pushes_node_list() {
        let graph = test_graph();
        let nav = resolve_direct_jump(&graph, "test_bar");
        assert_eq!(
            nav.depth(),
            2,
            "single-match direct jump should push NodeList on top of Home"
        );
        match nav.current() {
            NavScreen::NodeList { kind, selected } => {
                assert_eq!(
                    *kind,
                    NodeKind::Test,
                    "matched node is a Test, so NodeList kind should be Test"
                );
                assert_eq!(*selected, 0, "test_bar is at index 0 in the test list");
            }
            other => panic!(
                "expected NodeList screen for single-match jump, got {:?}",
                other
            ),
        }
    }

    #[test]
    fn direct_jump_multiple_matches_pushes_disambiguation() {
        let graph = test_graph();
        // "test_" matches both test_bar and test_baz
        let nav = resolve_direct_jump(&graph, "test_");
        assert_eq!(
            nav.depth(),
            2,
            "multi-match direct jump should push Disambiguation on top of Home"
        );
        match nav.current() {
            NavScreen::Disambiguation {
                query,
                matches,
                selected,
            } => {
                assert_eq!(
                    query, "test_",
                    "disambiguation query should preserve the original name"
                );
                assert_eq!(matches.len(), 2, "both test nodes should match 'test_'");
                assert_eq!(*selected, 0, "disambiguation cursor should start at 0");
            }
            other => panic!(
                "expected Disambiguation screen for multi-match jump, got {:?}",
                other
            ),
        }
    }

    #[test]
    fn direct_jump_case_insensitive() {
        let graph = test_graph();
        let nav = resolve_direct_jump(&graph, "SLOW");
        assert_eq!(
            nav.depth(),
            2,
            "case-insensitive match should still find the mark"
        );
        match nav.current() {
            NavScreen::NodeList { kind, .. } => {
                assert_eq!(
                    *kind,
                    NodeKind::Mark,
                    "SLOW should match the 'slow' mark node"
                );
            }
            other => panic!(
                "expected NodeList for case-insensitive match, got {:?}",
                other
            ),
        }
    }
}
