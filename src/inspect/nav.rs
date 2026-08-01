//! Trail-based navigation for `oxitest inspect` (ADR-0003).
//!
//! [`Trail`] manages the current screen with a flat list of [`Screen`]s.
//! The root is always `Screen::Overview` and can never be popped.

use super::graph::{InspectGraph, NodeRef};

// ── Screen ───────────────────────────────────────────────────────────────────

/// A single screen in the [`Trail`] navigation model (ADR-0003).
///
/// - `Overview` shows all node kinds grouped together with counts and sigils.
/// - `NodeFocus` shows the detail view for a single node.
/// - `Disambiguation` is shown when multiple nodes match a direct-jump name.
/// - `History` lists previously visited nodes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Screen {
    /// Overview screen: all node kinds shown together with counts and sigils.
    /// `selected` is the cursor index within the flat list of all nodes.
    Overview { selected: usize },

    /// Detail view for a single node.
    /// `selected` is the cursor index within the node's related-items list.
    NodeFocus { node: NodeRef, selected: usize },

    /// Disambiguation: multiple nodes matched a direct-jump name.
    /// `selected` is the cursor index within `matches`.
    Disambiguation {
        query: String,
        matches: Vec<NodeRef>,
        selected: usize,
    },

    /// History screen: lists previously visited nodes, most recent first.
    /// `selected` is the cursor index within the history entries.
    History { selected: usize },
}

// ── Trail ────────────────────────────────────────────────────────────────────

/// A navigation trail for `oxitest inspect` (ADR-0003).
///
/// Invariant: `screens[0]` is always `Screen::Overview { selected: 0 }`.
/// This invariant is structural — `pop()` refuses to remove the root, so
/// callers never need to handle an empty trail.
#[derive(Debug)]
pub(crate) struct Trail {
    screens: Vec<Screen>,
}

impl Trail {
    /// Create a new trail starting at the Overview screen.
    pub(crate) fn new() -> Self {
        Self {
            screens: vec![Screen::Overview { selected: 0 }],
        }
    }

    /// Return the current (top-of-trail) screen.
    pub(crate) fn current(&self) -> &Screen {
        // SAFETY: invariant guarantees screens is non-empty.
        self.screens
            .last()
            .expect("Trail screens must be non-empty")
    }

    /// Return a mutable reference to the current screen.
    pub(crate) fn current_mut(&mut self) -> &mut Screen {
        // SAFETY: invariant guarantees screens is non-empty.
        self.screens
            .last_mut()
            .expect("Trail screens must be non-empty")
    }

    /// Push a new screen onto the trail.
    pub(crate) fn push(&mut self, screen: Screen) {
        self.screens.push(screen);
    }

    /// Pop the top screen, unless it is the root Overview screen.
    ///
    /// Returns `true` if a screen was popped, `false` if already at root.
    pub(crate) fn pop(&mut self) -> bool {
        if self.screens.len() <= 1 {
            false
        } else {
            self.screens.pop();
            true
        }
    }

    /// Return the depth of the trail (1 = Overview only).
    #[allow(dead_code)] // used in nav tests
    pub(crate) fn depth(&self) -> usize {
        self.screens.len()
    }

    /// Return a breadcrumb path as `(sigil, label)` pairs.
    ///
    /// `Overview` contributes `('○', "overview")`.
    /// `NodeFocus` contributes the node's sigil and name from the graph.
    /// `Disambiguation` and `History` are skipped (no meaningful breadcrumb
    /// entry — they are transient overlay screens).
    pub(crate) fn breadcrumb<'a>(&'a self, graph: &'a InspectGraph) -> Vec<(char, &'a str)> {
        self.screens
            .iter()
            .filter_map(|screen| match screen {
                Screen::Overview { .. } => Some(('○', "overview")),
                Screen::NodeFocus { node, .. } => Some((node.kind.sigil(), graph.node_name(node))),
                Screen::Disambiguation { .. } | Screen::History { .. } => None,
            })
            .collect()
    }
}

// ── Direct jump resolution ────────────────────────────────────────────────────

/// Resolve a name against the graph for direct-jump navigation.
///
/// - 0 matches → Overview only (depth 1).
/// - 1 match   → Overview + NodeFocus on the matched node (depth 2).
/// - N matches → Overview + Disambiguation screen (depth 2).
pub(crate) fn resolve_direct_jump(graph: &InspectGraph, name: &str) -> Trail {
    let name_lower = name.to_lowercase();
    let matches: Vec<NodeRef> = graph
        .all_node_refs()
        .into_iter()
        .filter(|r| graph.node_name(r).to_lowercase().contains(&name_lower))
        .collect();

    let mut trail = Trail::new();

    match matches.len() {
        0 => {
            // No matches — stay on Overview.
        }
        1 => {
            trail.push(Screen::NodeFocus {
                node: matches.into_iter().next().expect("len == 1"),
                selected: 0,
            });
        }
        _ => {
            trail.push(Screen::Disambiguation {
                query: name.to_string(),
                matches,
                selected: 0,
            });
        }
    }

    trail
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::inspect::graph::{
        NodeKind,
        nodes::{MarkNode, TestNode},
    };

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
        graph
    }

    // ── Trail basics ─────────────────────────────────────────────────────

    #[test]
    fn trail_starts_at_overview() {
        let trail = Trail::new();
        assert_eq!(
            *trail.current(),
            Screen::Overview { selected: 0 },
            "new Trail should start at Overview with selected = 0"
        );
        assert_eq!(
            trail.depth(),
            1,
            "new Trail should have depth 1 (Overview only)"
        );
    }

    #[test]
    fn trail_push_changes_current_screen() {
        let mut trail = Trail::new();
        let node = NodeRef {
            kind: NodeKind::Test,
            index: 0,
        };
        trail.push(Screen::NodeFocus {
            node: node.clone(),
            selected: 0,
        });
        assert_eq!(
            *trail.current(),
            Screen::NodeFocus {
                node: node.clone(),
                selected: 0
            },
            "after push, current should be the pushed screen"
        );
        assert_eq!(trail.depth(), 2, "depth should be 2 after one push");
    }

    #[test]
    fn trail_pop_returns_to_overview() {
        let mut trail = Trail::new();
        trail.push(Screen::NodeFocus {
            node: NodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            selected: 0,
        });
        let popped = trail.pop();
        assert!(popped, "pop should return true when not at Overview root");
        assert_eq!(
            *trail.current(),
            Screen::Overview { selected: 0 },
            "after pop, current should return to Overview"
        );
    }

    #[test]
    fn trail_pop_at_root_returns_false() {
        let mut trail = Trail::new();
        let popped = trail.pop();
        assert!(!popped, "pop at root Overview should return false");
        assert_eq!(
            *trail.current(),
            Screen::Overview { selected: 0 },
            "trail should remain at Overview after failed pop"
        );
        assert_eq!(trail.depth(), 1, "depth should still be 1 after failed pop");
    }

    #[test]
    fn trail_current_mut_allows_cursor_update() {
        let mut trail = Trail::new();
        if let Screen::Overview { selected } = trail.current_mut() {
            *selected = 5;
        }
        assert_eq!(
            *trail.current(),
            Screen::Overview { selected: 5 },
            "current_mut should allow updating the cursor position"
        );
    }

    // ── Trail breadcrumb ─────────────────────────────────────────────────

    #[test]
    fn breadcrumb_overview_only() {
        let graph = test_graph();
        let trail = Trail::new();
        let crumbs = trail.breadcrumb(&graph);
        assert_eq!(
            crumbs,
            vec![('○', "overview")],
            "overview-only trail should produce a single breadcrumb entry"
        );
    }

    #[test]
    fn breadcrumb_with_node_focus() {
        let graph = test_graph();
        let mut trail = Trail::new();
        // test_bar is at index 0 in the test vector
        trail.push(Screen::NodeFocus {
            node: NodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            selected: 0,
        });
        let crumbs = trail.breadcrumb(&graph);
        assert_eq!(
            crumbs.len(),
            2,
            "overview + NodeFocus should produce 2 breadcrumbs"
        );
        assert_eq!(
            crumbs[0],
            ('○', "overview"),
            "first breadcrumb is always overview"
        );
        assert_eq!(
            crumbs[1].0, 'T',
            "NodeFocus for a Test node should use 'T' sigil"
        );
        assert_eq!(
            crumbs[1].1, "test_foo.py::test_bar",
            "NodeFocus breadcrumb label should be the node's display name"
        );
    }

    #[test]
    fn breadcrumb_skips_disambiguation_and_history() {
        let graph = test_graph();
        let mut trail = Trail::new();
        trail.push(Screen::Disambiguation {
            query: "test".to_string(),
            matches: vec![NodeRef {
                kind: NodeKind::Test,
                index: 0,
            }],
            selected: 0,
        });
        let crumbs = trail.breadcrumb(&graph);
        assert_eq!(
            crumbs,
            vec![('○', "overview")],
            "Disambiguation screen should be skipped in breadcrumb"
        );

        let mut trail2 = Trail::new();
        trail2.push(Screen::History { selected: 0 });
        let crumbs2 = trail2.breadcrumb(&graph);
        assert_eq!(
            crumbs2,
            vec![('○', "overview")],
            "History screen should be skipped in breadcrumb"
        );
    }

    // ── Direct jump ──────────────────────────────────────────────────────

    #[test]
    fn trail_direct_jump_no_match_stays_overview() {
        let graph = test_graph();
        let trail = resolve_direct_jump(&graph, "nonexistent");
        assert_eq!(
            *trail.current(),
            Screen::Overview { selected: 0 },
            "unmatched direct jump should stay on Overview"
        );
        assert_eq!(
            trail.depth(),
            1,
            "unmatched direct jump should not push any screen"
        );
    }

    #[test]
    fn trail_direct_jump_single_match_pushes_node_focus() {
        let graph = test_graph();
        let trail = resolve_direct_jump(&graph, "test_bar");
        assert_eq!(
            trail.depth(),
            2,
            "single-match direct jump should push NodeFocus on top of Overview"
        );
        match trail.current() {
            Screen::NodeFocus { node, selected } => {
                assert_eq!(
                    node.kind,
                    NodeKind::Test,
                    "matched node is a Test, so NodeFocus should have kind Test"
                );
                assert_eq!(node.index, 0, "test_bar is at index 0 in the test vector");
                assert_eq!(*selected, 0, "NodeFocus selected cursor starts at 0");
            }
            other => panic!("expected NodeFocus for single-match jump, got {:?}", other),
        }
    }

    #[test]
    fn trail_direct_jump_multiple_matches_pushes_disambiguation() {
        let graph = test_graph();
        // "test_" matches both test_bar and test_baz
        let trail = resolve_direct_jump(&graph, "test_");
        assert_eq!(
            trail.depth(),
            2,
            "multi-match direct jump should push Disambiguation on top of Overview"
        );
        match trail.current() {
            Screen::Disambiguation {
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
                "expected Disambiguation for multi-match jump, got {:?}",
                other
            ),
        }
    }

    #[test]
    fn trail_direct_jump_case_insensitive() {
        let graph = test_graph();
        let trail = resolve_direct_jump(&graph, "SLOW");
        assert_eq!(
            trail.depth(),
            2,
            "case-insensitive match should find the mark node"
        );
        match trail.current() {
            Screen::NodeFocus { node, .. } => {
                assert_eq!(
                    node.kind,
                    NodeKind::Mark,
                    "SLOW should match the 'slow' mark node"
                );
            }
            other => panic!(
                "expected NodeFocus for case-insensitive match, got {:?}",
                other
            ),
        }
    }
}
