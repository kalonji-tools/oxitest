//! In-memory graph connecting the six inspect node types.
//!
//! [`InspectGraph`] holds typed vectors for each node kind and a list of
//! [`BrokenEdge`]s for unresolved fixture references.  [`NodeRef`] is a
//! lightweight handle (kind + index) used by the navigation stack,
//! search results, and detail views.

pub(crate) mod builder;
pub(crate) mod nodes;

use nodes::{ConftestNode, FixtureNode, HelperNode, MarkNode, PluginNode, TestNode};

// ── NodeKind ─────────────────────────────────────────────────────────────────

/// Discriminant for the six node types in the inspect graph.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub(crate) enum NodeKind {
    Fixture,
    Test,
    Mark,
    Conftest,
    Plugin,
    Helper,
}

impl NodeKind {
    /// Single-character sigil used as a visual prefix in the TUI tree.
    pub(crate) fn sigil(self) -> char {
        match self {
            Self::Fixture => 'F',
            Self::Test => 'T',
            Self::Mark => 'M',
            Self::Conftest => 'C',
            Self::Plugin => 'P',
            Self::Helper => 'H',
        }
    }
}

// ── NodeRef ──────────────────────────────────────────────────────────────────

/// A uniform handle for referencing any node in the graph.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub(crate) struct NodeRef {
    pub kind: NodeKind,
    pub index: usize,
}

// ── BrokenEdge ───────────────────────────────────────────────────────────────

/// An edge that could not be resolved during graph construction.
///
/// Typically a fixture dependency name that does not match any known
/// fixture node.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct BrokenEdge {
    /// The node that references the missing target.
    pub from: NodeRef,
    /// The qualifier (name) that could not be resolved.
    pub qualifier: String,
    /// The binding type of the unresolved reference (e.g. `"fixture"`).
    pub binding_type: String,
}

// ── InspectGraph ─────────────────────────────────────────────────────────────

/// The immutable in-memory graph consumed by the inspect TUI.
#[derive(Debug, Default)]
pub(crate) struct InspectGraph {
    pub fixtures: Vec<FixtureNode>,
    pub tests: Vec<TestNode>,
    pub marks: Vec<MarkNode>,
    pub conftests: Vec<ConftestNode>,
    pub plugins: Vec<PluginNode>,
    pub helpers: Vec<HelperNode>,
    #[allow(dead_code)] // displayed by detail view (#1117) and navigation (#1116)
    pub broken_edges: Vec<BrokenEdge>,
}

impl InspectGraph {
    /// Return the display name for the node at the given reference.
    #[allow(dead_code)] // used by navigation (#1116), detail (#1117), and search (#1118)
    pub(crate) fn node_name(&self, r: &NodeRef) -> &str {
        match r.kind {
            NodeKind::Fixture => &self.fixtures[r.index].name,
            NodeKind::Test => &self.tests[r.index].node_id,
            NodeKind::Mark => &self.marks[r.index].name,
            NodeKind::Conftest => &self.conftests[r.index].path,
            NodeKind::Plugin => &self.plugins[r.index].name,
            NodeKind::Helper => &self.helpers[r.index].name,
        }
    }

    /// Return the sigil character for the node at the given reference.
    #[allow(dead_code)] // used by navigation (#1116), detail (#1117), and search (#1118)
    pub(crate) fn node_sigil(&self, r: &NodeRef) -> char {
        r.kind.sigil()
    }

    /// Return the number of nodes of the given kind.
    pub(crate) fn node_count(&self, kind: NodeKind) -> usize {
        match kind {
            NodeKind::Fixture => self.fixtures.len(),
            NodeKind::Test => self.tests.len(),
            NodeKind::Mark => self.marks.len(),
            NodeKind::Conftest => self.conftests.len(),
            NodeKind::Plugin => self.plugins.len(),
            NodeKind::Helper => self.helpers.len(),
        }
    }

    /// Whether the graph contains zero nodes of all kinds.
    pub(crate) fn is_empty(&self) -> bool {
        self.fixtures.is_empty()
            && self.tests.is_empty()
            && self.marks.is_empty()
            && self.conftests.is_empty()
            && self.plugins.is_empty()
            && self.helpers.is_empty()
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_graph_is_empty() {
        let graph = InspectGraph::default();
        assert!(
            graph.is_empty(),
            "default graph should have no nodes in any vector"
        );
        assert_eq!(
            graph.broken_edges.len(),
            0,
            "default graph should have no broken edges"
        );
    }

    #[test]
    fn node_name_returns_correct_name_for_each_kind() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "db".to_string(),
            binding_type: String::new(),
            scope: "function".to_string(),
            autouse: false,
            source: String::new(),
            is_async: false,
            description: String::new(),
            depends_on: vec![],
            consumers: vec![],
            conftest_idx: None,
            plugin_idx: None,
        });
        graph.tests.push(TestNode {
            node_id: "test_foo.py::test_bar".to_string(),
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
        graph.conftests.push(ConftestNode {
            path: "tests/conftest.py".to_string(),
            fixtures: vec![],
            helpers: vec![],
        });
        graph.plugins.push(PluginNode {
            name: "capture".to_string(),
            protocols: vec![],
            fixtures: vec![],
        });
        graph.helpers.push(HelperNode {
            name: "make_db".to_string(),
            signature: "make_db()".to_string(),
            docstring: None,
            source: "tests/conftest.py".to_string(),
            conftest_idx: 0,
        });

        let cases: Vec<(NodeRef, &str)> = vec![
            (
                NodeRef {
                    kind: NodeKind::Fixture,
                    index: 0,
                },
                "db",
            ),
            (
                NodeRef {
                    kind: NodeKind::Test,
                    index: 0,
                },
                "test_foo.py::test_bar",
            ),
            (
                NodeRef {
                    kind: NodeKind::Mark,
                    index: 0,
                },
                "slow",
            ),
            (
                NodeRef {
                    kind: NodeKind::Conftest,
                    index: 0,
                },
                "tests/conftest.py",
            ),
            (
                NodeRef {
                    kind: NodeKind::Plugin,
                    index: 0,
                },
                "capture",
            ),
            (
                NodeRef {
                    kind: NodeKind::Helper,
                    index: 0,
                },
                "make_db",
            ),
        ];

        for (node_ref, expected) in &cases {
            assert_eq!(
                graph.node_name(node_ref),
                *expected,
                "node_name for {:?} should return '{}'",
                node_ref.kind,
                expected
            );
        }
    }

    #[test]
    fn node_sigil_returns_correct_char_for_each_kind() {
        let cases = [
            (NodeKind::Fixture, 'F'),
            (NodeKind::Test, 'T'),
            (NodeKind::Mark, 'M'),
            (NodeKind::Conftest, 'C'),
            (NodeKind::Plugin, 'P'),
            (NodeKind::Helper, 'H'),
        ];
        let graph = InspectGraph::default();
        for (kind, expected) in &cases {
            let r = NodeRef {
                kind: *kind,
                index: 0,
            };
            assert_eq!(
                graph.node_sigil(&r),
                *expected,
                "sigil for {:?} should be '{}'",
                kind,
                expected
            );
        }
    }

    #[test]
    fn node_count_returns_vector_length() {
        let mut graph = InspectGraph::default();
        assert_eq!(
            graph.node_count(NodeKind::Test),
            0,
            "empty graph should have 0 tests"
        );

        graph.tests.push(TestNode {
            node_id: "t1".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph.tests.push(TestNode {
            node_id: "t2".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });
        assert_eq!(
            graph.node_count(NodeKind::Test),
            2,
            "graph with 2 tests pushed should report count 2"
        );
    }

    #[test]
    fn is_empty_returns_false_when_any_vector_has_nodes() {
        let mut graph = InspectGraph::default();
        graph.marks.push(MarkNode {
            name: "slow".to_string(),
            used_by: vec![],
        });
        assert!(
            !graph.is_empty(),
            "graph with one mark node should not be empty"
        );
    }
}
