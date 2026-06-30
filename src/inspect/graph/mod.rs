//! In-memory graph connecting the six inspect node types.
//!
//! [`InspectGraph`] holds typed vectors for each node kind and a list of
//! [`BrokenEdge`]s for unresolved fixture references.  [`NodeRef`] is a
//! lightweight handle (kind + index) used by the navigation stack,
//! search results, and detail views.

pub(crate) mod builder;
pub(crate) mod nodes;

use nodes::{ConftestNode, FixtureNode, HelperNode, MarkNode, PluginNode, TestNode};

use crate::query::resource::QueryEntry;

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

// ── Searchable impl ──────────────────────────────────────────────────────────

impl crate::inspect::search::Searchable for InspectGraph {
    /// Return all nodes across all six typed vectors.
    ///
    /// Iteration order: fixtures → tests → marks → conftests → plugins → helpers.
    /// The `index` in each `NodeRef` is the position within its own typed vector,
    /// not a global flat index, so it can be used directly for O(1) lookups.
    fn all_nodes(&self) -> Vec<NodeRef> {
        use NodeKind::*;
        [Fixture, Test, Mark, Conftest, Plugin, Helper]
            .into_iter()
            .flat_map(|kind| self.nodes_of_kind(kind))
            .collect()
    }

    /// Return all nodes of the given kind.
    ///
    /// Each `NodeRef.index` is the position within the kind's typed vector,
    /// matching the lookup semantics of `node_name` and `node_query_entry`.
    fn nodes_of_kind(&self, kind: NodeKind) -> Vec<NodeRef> {
        (0..self.node_count(kind))
            .map(|i| NodeRef { kind, index: i })
            .collect()
    }

    /// Return the display name for the node at `r`.
    ///
    /// Delegates to the existing `InspectGraph::node_name` method which
    /// dispatches on kind to the appropriate typed vector.
    fn node_name(&self, r: NodeRef) -> &str {
        // The graph's existing node_name takes &NodeRef, so borrow temporarily.
        InspectGraph::node_name(self, &r)
    }

    /// Return the kind of the node — read directly from the NodeRef discriminant.
    ///
    /// `NodeRef` already carries its kind, so no graph lookup is needed.
    fn node_kind(&self, r: NodeRef) -> NodeKind {
        r.kind
    }

    /// Build a `QueryEntry` for DSL evaluation against a node.
    ///
    /// All nodes get a `name` field.  Test nodes additionally get a `mark` field
    /// whose value is the comma-joined names of all marks applied to that test,
    /// enabling DSL expressions like `mark(slow)` to filter tests by mark name.
    fn node_query_entry(&self, r: NodeRef) -> QueryEntry {
        let mut fields = std::collections::HashMap::new();
        // Call the inherent method (takes &NodeRef), not the trait method.
        fields.insert("name".to_string(), self.node_name(&r).to_string());

        if r.kind == NodeKind::Test {
            let test = &self.tests[r.index];
            let mark_names: Vec<&str> = test
                .marks
                .iter()
                .filter_map(|&mi| self.marks.get(mi).map(|m| m.name.as_str()))
                .collect();
            fields.insert("mark".to_string(), mark_names.join(","));
        }

        QueryEntry { fields }
    }
}

// ── Helpers ──────────────────────────────────────────────────────────────────

/// Extract the base function name from a node ID by stripping `[param_id]`.
/// Returns the full node_id if no `[` is found.
pub(crate) fn base_test_name(node_id: &str) -> &str {
    node_id.rfind('[').map_or(node_id, |pos| &node_id[..pos])
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

    #[test]
    fn base_test_name_strips_param_id() {
        assert_eq!(
            super::base_test_name("tests/test_math.py::test_add[1+2]"),
            "tests/test_math.py::test_add",
            "base_test_name should strip the bracketed param_id suffix"
        );
    }

    #[test]
    fn base_test_name_returns_full_id_when_no_bracket() {
        assert_eq!(
            super::base_test_name("tests/test_math.py::test_solo"),
            "tests/test_math.py::test_solo",
            "base_test_name should return the full node_id when no '[' is present"
        );
    }
}
