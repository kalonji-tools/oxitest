//! In-memory graph connecting the five inspect node types.
//!
//! [`InspectGraph`] holds typed vectors for each node kind.  [`NodeRef`] is a
//! lightweight handle (kind + index) used by the navigation stack,
//! search results, and detail views.

pub mod builder;
pub mod nodes;

use nodes::{DeclarationNode, FixtureNode, MarkNode, PluginNode, TestNode};

use crate::query::resource::QueryEntry;

// ── NodeKind ─────────────────────────────────────────────────────────────────

/// Discriminant for the five node types in the inspect graph.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum NodeKind {
    Fixture,
    Test,
    Mark,
    Declaration,
    Plugin,
}

impl NodeKind {
    /// Single-character sigil used as a visual prefix in the TUI tree.
    pub(crate) const fn sigil(self) -> char {
        match self {
            Self::Fixture => 'F',
            Self::Test => 'T',
            Self::Mark => 'M',
            Self::Declaration => 'D',
            Self::Plugin => 'P',
        }
    }

    /// Lowercase label for this node kind, used in flash messages.
    pub(crate) const fn label(self) -> &'static str {
        match self {
            Self::Fixture => "fixture",
            Self::Test => "test",
            Self::Mark => "mark",
            Self::Declaration => "declaration",
            Self::Plugin => "plugin",
        }
    }
}

// ── NodeRef ──────────────────────────────────────────────────────────────────

/// A uniform handle for referencing any node in the graph.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct NodeRef {
    pub kind: NodeKind,
    pub index: usize,
}

impl NodeRef {
    pub(crate) const fn new(kind: NodeKind, index: usize) -> Self {
        Self { kind, index }
    }
}

// ── InspectGraph ─────────────────────────────────────────────────────────────

/// The immutable in-memory graph consumed by the inspect TUI.
#[derive(Debug, Default)]
pub struct InspectGraph {
    pub fixtures: Vec<FixtureNode>,
    pub tests: Vec<TestNode>,
    pub marks: Vec<MarkNode>,
    pub declarations: Vec<DeclarationNode>,
    pub plugins: Vec<PluginNode>,
    /// Autouse fixtures that **apply** to each module, keyed by module path and
    /// held in firing order (widest lifetime first, ADR-0009 Rule 7).
    ///
    /// Keyed by module rather than by test because `get_autouse` is: every test
    /// in one module has the same set. This says which fixtures apply, never
    /// which test builds one — Rule 7 makes the counts a rate, so the build
    /// lands in whichever test reaches the boundary first, and that depends on
    /// order, worker assignment and deselection (#1722).
    pub autouse_by_module: ahash::AHashMap<String, Vec<AutouseFixture>>,
}

// ── AutouseFixture ───────────────────────────────────────────────────────────

/// One autouse fixture that applies to a module.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AutouseFixture {
    pub name: String,
    /// The `Lifetime` the declaration wrote, never the caching `Scope`.
    pub lifetime: String,
}

impl InspectGraph {
    /// Strip an absolute rootdir prefix from all path fields in the graph.
    ///
    /// Normalizes `FixtureNode.source`, `DeclarationNode.path`,
    /// and `TestNode.node_id` to relative paths for display in the TUI.
    ///
    /// Uses `camino::Utf8Path::strip_prefix` for platform-safe path handling
    /// instead of manual string prefix manipulation with hardcoded separators.
    pub(crate) fn relativize_paths(&mut self, rootdir: &str) {
        if rootdir.is_empty() {
            return;
        }
        let root = camino::Utf8Path::new(rootdir);
        for f in &mut self.fixtures {
            if let Ok(rel) = camino::Utf8Path::new(&f.source).strip_prefix(root) {
                f.source = rel.to_string();
            }
            // The anchor is shown beside the source, so an absolute one here
            // would mix two path conventions in a single detail pane (#1722).
            if let Ok(rel) = camino::Utf8Path::new(&f.anchor).strip_prefix(root) {
                f.anchor = rel.to_string();
            }
        }
        for t in &mut self.tests {
            if let Ok(rel) = camino::Utf8Path::new(&t.node_id).strip_prefix(root) {
                t.node_id = rel.to_string();
            }
        }
        for c in &mut self.declarations {
            if let Ok(rel) = camino::Utf8Path::new(&c.path).strip_prefix(root) {
                c.path = rel.to_string();
            }
            if let Ok(rel) = camino::Utf8Path::new(&c.anchor).strip_prefix(root) {
                c.anchor = rel.to_string();
            }
        }
    }

    /// Return the display name for the node at the given reference.
    pub(crate) fn node_name(&self, r: &NodeRef) -> &str {
        match r.kind {
            NodeKind::Fixture => &self.fixtures[r.index].name,
            NodeKind::Test => &self.tests[r.index].node_id,
            NodeKind::Mark => &self.marks[r.index].name,
            NodeKind::Declaration => &self.declarations[r.index].path,
            NodeKind::Plugin => &self.plugins[r.index].name,
        }
    }

    /// Return the number of nodes of the given kind.
    pub(crate) const fn node_count(&self, kind: NodeKind) -> usize {
        match kind {
            NodeKind::Fixture => self.fixtures.len(),
            NodeKind::Test => self.tests.len(),
            NodeKind::Mark => self.marks.len(),
            NodeKind::Declaration => self.declarations.len(),
            NodeKind::Plugin => self.plugins.len(),
        }
    }

    /// Whether the graph contains zero nodes of all kinds.
    pub(crate) const fn is_empty(&self) -> bool {
        self.fixtures.is_empty()
            && self.tests.is_empty()
            && self.marks.is_empty()
            && self.declarations.is_empty()
            && self.plugins.is_empty()
    }

    /// Return node refs for a specific kind.
    #[allow(dead_code)] // only reached from tests
    pub(crate) fn nodes_of_kind(&self, kind: NodeKind) -> Vec<NodeRef> {
        let count = self.node_count(kind);
        (0..count).map(|i| NodeRef { kind, index: i }).collect()
    }

    /// Build a [`QueryEntry`] for DSL evaluation against a node.
    ///
    /// All nodes get a `name` field.  Test nodes additionally get a `mark` field
    /// whose value is the comma-joined names of all marks applied to that test,
    /// enabling DSL expressions like `mark(slow)` to filter tests by mark name.
    pub(crate) fn node_query_entry(&self, r: &NodeRef) -> QueryEntry {
        let mut fields = std::collections::HashMap::new();
        fields.insert("name".to_string(), self.node_name(r).to_string());

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

    /// Return references to every node in the graph, in display order.
    ///
    /// Iteration order: tests → fixtures → marks → declarations → plugins.
    /// Each `NodeRef.index` is the position within its own typed vector,
    /// matching the O(1) lookup semantics of `node_name`.
    pub(crate) fn all_node_refs(&self) -> Vec<NodeRef> {
        let mut refs = Vec::new();
        for i in 0..self.tests.len() {
            refs.push(NodeRef {
                kind: NodeKind::Test,
                index: i,
            });
        }
        for i in 0..self.fixtures.len() {
            refs.push(NodeRef {
                kind: NodeKind::Fixture,
                index: i,
            });
        }
        for i in 0..self.marks.len() {
            refs.push(NodeRef {
                kind: NodeKind::Mark,
                index: i,
            });
        }
        for i in 0..self.declarations.len() {
            refs.push(NodeRef {
                kind: NodeKind::Declaration,
                index: i,
            });
        }
        for i in 0..self.plugins.len() {
            refs.push(NodeRef {
                kind: NodeKind::Plugin,
                index: i,
            });
        }
        refs
    }
}

// ── Helpers ──────────────────────────────────────────────────────────────────

/// Extract the base function name from a node ID by stripping `[param_id]`.
/// Returns the full `node_id` if no `[` is found.
#[allow(dead_code)] // retained for future parametrize group collapsing
pub fn base_test_name(node_id: &str) -> &str {
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
    }

    #[test]
    fn node_name_returns_correct_name_for_each_kind() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            lifetime: "function".to_string(),
            anchor: String::new(),
            home: "fixtures-file".to_string(),
            name: "db".to_string(),
            binding_type: String::new(),
            scope: "each".to_string(),
            autouse: false,
            source: String::new(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            declaration_idx: None,
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
        graph.declarations.push(DeclarationNode {
            anchor: String::new(),
            home: "fixtures-file".to_string(),
            path: "tests/__fixtures__.py".to_string(),
            fixtures: vec![],
        });
        graph.plugins.push(PluginNode {
            name: "capture".to_string(),
            protocols: vec![],
            fixtures: vec![],
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
                    kind: NodeKind::Declaration,
                    index: 0,
                },
                "tests/__fixtures__.py",
            ),
            (
                NodeRef {
                    kind: NodeKind::Plugin,
                    index: 0,
                },
                "capture",
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
    fn sigil_returns_correct_char_for_each_kind() {
        let cases = [
            (NodeKind::Fixture, 'F'),
            (NodeKind::Test, 'T'),
            (NodeKind::Mark, 'M'),
            (NodeKind::Declaration, 'D'),
            (NodeKind::Plugin, 'P'),
        ];
        for (kind, expected) in &cases {
            assert_eq!(
                kind.sigil(),
                *expected,
                "sigil for {kind:?} should be '{expected}'"
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

    #[test]
    fn all_node_refs_returns_all_nodes() {
        use crate::query::resource::QueryEntry;
        use builder::GraphBuilder;

        fn entry(pairs: &[(&str, &str)]) -> QueryEntry {
            let fields = pairs
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect();
            QueryEntry { fields }
        }

        let mut builder = GraphBuilder::new();
        // 1 fixture
        builder.add_fixture_entries(&[entry(&[
            ("name", "fx"),
            ("source", "__fixtures__.py"),
            ("home", "fixtures-file"),
            ("type", "str"),
            ("scope", "each"),
            ("autouse", "false"),
            ("async", "false"),
            ("description", ""),
        ])]);
        // 1 test
        builder.add_test_entries(&[entry(&[
            ("name", "test_a.py::test_one"),
            ("source", "test_a.py"),
            ("mark", ""),
            ("async", "false"),
        ])]);
        builder.resolve_edges();
        let graph = builder.build();
        let refs = graph.all_node_refs();
        // resolve_edges auto-creates a DeclarationNode for the fixture's source path,
        // so the graph contains 1 test + 1 fixture + 1 declaration = 3 nodes total.
        assert_eq!(
            refs.len(),
            3,
            "1 test + 1 fixture + 1 auto-created declaration = 3 node refs"
        );
    }

    // ── relativize_paths tests ────────────────────────────────────────────

    #[test]
    fn relativize_paths_strips_prefix() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            lifetime: "function".to_string(),
            anchor: String::new(),
            home: "fixtures-file".to_string(),
            name: "db".to_string(),
            binding_type: String::new(),
            scope: "each".to_string(),
            autouse: false,
            source: "/home/user/project/__fixtures__.py".to_string(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            declaration_idx: None,
            plugin_idx: None,
        });
        graph.tests.push(TestNode {
            node_id: "/home/user/project/tests/test_a.py::test_one".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph.declarations.push(DeclarationNode {
            anchor: String::new(),
            home: "fixtures-file".to_string(),
            path: "/home/user/project/tests/__fixtures__.py".to_string(),
            fixtures: vec![],
        });

        graph.relativize_paths("/home/user/project");

        assert_eq!(
            graph.fixtures[0].source, "__fixtures__.py",
            "fixture source should have rootdir prefix stripped"
        );
        assert_eq!(
            graph.tests[0].node_id, "tests/test_a.py::test_one",
            "test node_id should have rootdir prefix stripped"
        );
        assert_eq!(
            graph.declarations[0].path, "tests/__fixtures__.py",
            "declaration path should have rootdir prefix stripped"
        );
    }

    #[test]
    fn relativize_paths_no_match_unchanged() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            lifetime: "function".to_string(),
            anchor: String::new(),
            home: "fixtures-file".to_string(),
            name: "fx".to_string(),
            binding_type: String::new(),
            scope: "each".to_string(),
            autouse: false,
            source: "/other/path/__fixtures__.py".to_string(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            declaration_idx: None,
            plugin_idx: None,
        });

        graph.relativize_paths("/home/user/project");

        assert_eq!(
            graph.fixtures[0].source, "/other/path/__fixtures__.py",
            "path that does not start with rootdir should remain unchanged"
        );
    }

    #[test]
    fn relativize_paths_empty_rootdir_noop() {
        let mut graph = InspectGraph::default();
        graph.tests.push(TestNode {
            node_id: "/home/user/project/test_a.py::test_one".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        });

        graph.relativize_paths("");

        assert_eq!(
            graph.tests[0].node_id, "/home/user/project/test_a.py::test_one",
            "empty rootdir should not modify any paths"
        );
    }

    #[test]
    fn relativize_paths_trailing_slash() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            lifetime: "function".to_string(),
            anchor: String::new(),
            home: "fixtures-file".to_string(),
            name: "db".to_string(),
            binding_type: String::new(),
            scope: "each".to_string(),
            autouse: false,
            source: "/home/user/project/__fixtures__.py".to_string(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            declaration_idx: None,
            plugin_idx: None,
        });

        graph.relativize_paths("/home/user/project/");

        assert_eq!(
            graph.fixtures[0].source, "__fixtures__.py",
            "rootdir with trailing slash should work the same as without"
        );
    }

    #[test]
    fn relativize_paths_plugin_source_unchanged() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            lifetime: String::new(),
            anchor: String::new(),
            home: String::new(),
            name: "cache".to_string(),
            binding_type: String::new(),
            scope: "each".to_string(),
            autouse: false,
            source: "<plugin:cache>".to_string(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            declaration_idx: None,
            plugin_idx: None,
        });

        graph.relativize_paths("/home/user/project");

        assert_eq!(
            graph.fixtures[0].source, "<plugin:cache>",
            "plugin source markers like '<plugin:cache>' should not be modified"
        );
    }
}
