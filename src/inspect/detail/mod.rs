//! Node detail view rendering for `oxitest inspect`.
//!
//! Renders the right-pane detail view for a selected node.  Each of the
//! five node types has a dedicated renderer that shows its fields and
//! navigable connections.

mod conftest;
mod fixture;
mod mark;
mod plugin;
pub(crate) mod styles;
mod test;

use ratatui::text::{Line, Span};

use super::graph::{self, InspectGraph, NodeKind, NodeRef};

use styles::{bool_field, connection_line, field_line, section_header, sigil_style};

// ── Public API ──────────────────────────────────────────────────────────────

/// Build the detail view content lines for the given node.
///
/// Returns a `Vec<Line>` suitable for embedding in a `Paragraph` widget.
/// When `node_ref` is `None`, returns a placeholder message.
pub(crate) fn render_detail<'a>(graph: &InspectGraph, node_ref: Option<&NodeRef>) -> Vec<Line<'a>> {
    let Some(node_ref) = node_ref else {
        return vec![Line::from("Select a node to view details")];
    };

    match node_ref.kind {
        NodeKind::Fixture => fixture::render_fixture(graph, node_ref),
        NodeKind::Test => test::render_test(graph, node_ref),
        NodeKind::Mark => mark::render_mark(graph, node_ref),
        NodeKind::Conftest => conftest::render_conftest(graph, node_ref),
        NodeKind::Plugin => plugin::render_plugin(graph, node_ref),
    }
}

/// Build compact preview content for the right pane.
///
/// Shows: node header, 2-3 key properties, top 3 edges per group.
/// Omits: description text, some boolean fields.
pub(crate) fn render_preview<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
    match node_ref.kind {
        NodeKind::Fixture => fixture::preview_fixture(graph, node_ref),
        NodeKind::Test => test::preview_test(graph, node_ref),
        NodeKind::Mark => mark::preview_mark(graph, node_ref),
        NodeKind::Conftest => conftest::preview_conftest(graph, node_ref),
        NodeKind::Plugin => plugin::preview_plugin(graph, node_ref),
    }
}

// ── Edge navigation helpers ──────────────────────────────────────────────────

/// Collect all selectable edge `NodeRefs` for a node.
fn collect_selectable_edges(graph: &InspectGraph, node: &NodeRef) -> Vec<NodeRef> {
    match node.kind {
        NodeKind::Fixture => fixture::collect_edges(graph, node),
        NodeKind::Test => test::collect_edges(graph, node),
        NodeKind::Mark => mark::collect_edges(graph, node),
        NodeKind::Conftest => conftest::collect_edges(graph, node),
        NodeKind::Plugin => plugin::collect_edges(graph, node),
    }
}

/// Return the `NodeRef` of the selectable edge at `index` within a focused node.
pub(crate) fn edge_node_at(graph: &InspectGraph, node: &NodeRef, index: usize) -> Option<NodeRef> {
    let edges = collect_selectable_edges(graph, node);
    edges.get(index).cloned()
}

/// Count the number of selectable edge items for a focused node.
pub(crate) fn selectable_edge_count(graph: &InspectGraph, node: &NodeRef) -> usize {
    collect_selectable_edges(graph, node).len()
}

// ── Group detail (parametrize collapse) ──────────────────────────────────

/// Compute the intersection of index slices across a set of test variants.
///
/// Returns the indices that appear in every variant's slice (e.g. `fixture_deps` or marks).
/// Returns an empty vec if `indices` is empty.
#[allow(dead_code)] // retained for future parametrize group detail rendering
fn shared_indices<'a>(indices: &[usize], extractor: impl Fn(usize) -> &'a [usize]) -> Vec<usize> {
    if indices.is_empty() {
        return vec![];
    }
    let first: std::collections::HashSet<usize> = extractor(indices[0]).iter().copied().collect();
    indices
        .iter()
        .skip(1)
        .fold(first, |acc, &i| {
            let set: std::collections::HashSet<usize> = extractor(i).iter().copied().collect();
            acc.intersection(&set).copied().collect()
        })
        .into_iter()
        .collect()
}

/// Render the detail pane for a collapsed parametrize group.
///
/// Shows the shared base name, variant count, and connections that are
/// common across all variants (fixture deps, marks).
#[allow(dead_code)] // retained for future parametrize group detail rendering
pub(crate) fn render_group_detail<'a>(graph: &InspectGraph, indices: &[usize]) -> Vec<Line<'a>> {
    if indices.is_empty() {
        return vec![Line::from("Empty group")];
    }

    let first = &graph.tests[indices[0]];

    // Derive base name from the first variant's node_id.
    let base_name = graph::base_test_name(&first.node_id);

    let mut lines = vec![
        Line::from(vec![
            Span::styled("T", sigil_style()),
            Span::raw(format!(" {base_name}")),
        ]),
        Line::from(""),
        field_line("variants", &indices.len().to_string()),
    ];

    // Shared async status (show if all variants agree).
    let all_async = indices.iter().all(|&i| graph.tests[i].is_async);
    let any_async = indices.iter().any(|&i| graph.tests[i].is_async);
    if all_async {
        lines.push(bool_field("async", true));
    } else if !any_async {
        lines.push(bool_field("async", false));
    } else {
        lines.push(field_line("async", "mixed"));
    }

    // Shared fixture dependencies: intersection across all variants.
    let shared_fixture_deps = shared_indices(indices, |i| &graph.tests[i].fixture_deps);

    if !shared_fixture_deps.is_empty() {
        lines.push(Line::from(""));
        lines.push(section_header("Shared Fixture Dependencies"));
        for &dep_idx in &shared_fixture_deps {
            lines.push(connection_line('F', &graph.fixtures[dep_idx].name));
        }
    }

    // Shared marks: intersection across all variants.
    let shared_marks = shared_indices(indices, |i| &graph.tests[i].marks);

    if !shared_marks.is_empty() {
        lines.push(Line::from(""));
        lines.push(section_header("Shared Marks"));
        for &mark_idx in &shared_marks {
            lines.push(connection_line('M', &graph.marks[mark_idx].name));
        }
    }

    lines
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::inspect::graph::nodes::*;

    /// Build a minimal graph for testing a fixture detail view.
    fn fixture_graph() -> InspectGraph {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "db_session".to_string(),
            binding_type: "fixture".to_string(),
            scope: "session".to_string(),
            autouse: false,
            source: "tests/conftest.py".to_string(),
            is_async: false,
            description: "Database session fixture".to_string(),
            consumers: vec![NodeRef {
                kind: NodeKind::Test,
                index: 0,
            }],
            conftest_idx: Some(0),
            plugin_idx: None,
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_db.py::test_create_user".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![0],
            marks: vec![],
        });
        graph.conftests.push(ConftestNode {
            path: "tests/conftest.py".to_string(),
            fixtures: vec![0],
        });
        graph
    }

    /// Build a graph with a fixture that has broken edges.
    fn fixture_with_broken_edges_graph() -> InspectGraph {
        use crate::inspect::graph::BrokenEdge;

        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "db_session".to_string(),
            binding_type: "fixture".to_string(),
            scope: "session".to_string(),
            autouse: false,
            source: "tests/conftest.py".to_string(),
            is_async: true,
            description: "".to_string(),
            consumers: vec![],
            conftest_idx: None,
            plugin_idx: None,
        });
        graph.broken_edges.push(BrokenEdge {
            from: NodeRef {
                kind: NodeKind::Fixture,
                index: 0,
            },
            qualifier: "missing_dep".to_string(),
            binding_type: "fixture".to_string(),
        });
        graph
    }

    /// Build a graph for testing a test detail view.
    fn test_graph() -> InspectGraph {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "db_session".to_string(),
            binding_type: "fixture".to_string(),
            scope: "function".to_string(),
            autouse: false,
            source: String::new(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            conftest_idx: None,
            plugin_idx: None,
        });
        graph.marks.push(MarkNode {
            name: "slow".to_string(),
            used_by: vec![0],
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_db.py::test_create_user".to_string(),
            is_async: true,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![0],
            marks: vec![0],
        });
        graph
    }

    /// Build a graph with a parametrized test for testing variants.
    fn test_parametrized_graph() -> InspectGraph {
        let mut graph = InspectGraph::default();
        graph.tests.push(TestNode {
            node_id: "tests/test_math.py::test_add[1+2]".to_string(),
            is_async: false,
            param_id: Some("1+2".to_string()),
            param_count: 2,
            variants: vec![1],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_math.py::test_add[3+4]".to_string(),
            is_async: false,
            param_id: Some("3+4".to_string()),
            param_count: 2,
            variants: vec![0],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph
    }

    /// Build a graph for testing a mark detail view.
    fn mark_graph() -> InspectGraph {
        let mut graph = InspectGraph::default();
        graph.tests.push(TestNode {
            node_id: "tests/test_a.py::test_one".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![0],
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_b.py::test_two".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![0],
        });
        graph.marks.push(MarkNode {
            name: "slow".to_string(),
            used_by: vec![0, 1],
        });
        graph
    }

    /// Build a graph for testing a conftest detail view.
    fn conftest_graph() -> InspectGraph {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "db".to_string(),
            binding_type: "fixture".to_string(),
            scope: "session".to_string(),
            autouse: false,
            source: "tests/conftest.py".to_string(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            conftest_idx: Some(0),
            plugin_idx: None,
        });
        graph.fixtures.push(FixtureNode {
            name: "cache".to_string(),
            binding_type: "fixture".to_string(),
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
            fixtures: vec![0, 1],
        });
        graph
    }

    /// Build a graph for testing a plugin detail view.
    fn plugin_graph() -> InspectGraph {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "std_capture".to_string(),
            binding_type: "fixture".to_string(),
            scope: "function".to_string(),
            autouse: false,
            source: "<plugin:capture>".to_string(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            conftest_idx: None,
            plugin_idx: Some(0),
        });
        graph.plugins.push(PluginNode {
            name: "capture".to_string(),
            protocols: vec![
                "CollectorProvider".to_string(),
                "ReporterProvider".to_string(),
            ],
            fixtures: vec![0],
        });
        graph
    }

    // ── Unit tests ──────────────────────────────────────────────────────

    #[test]
    fn render_detail_none_shows_placeholder() {
        let graph = InspectGraph::default();
        let lines = render_detail(&graph, None);
        assert_eq!(
            lines.len(),
            1,
            "no selection should produce a single placeholder line"
        );
    }

    #[test]
    fn render_detail_fixture_shows_all_fields() {
        let graph = fixture_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Fixture,
            index: 0,
        };
        let lines = render_detail(&graph, Some(&node_ref));
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("db_session"),
            "fixture detail should show the fixture name"
        );
        assert!(
            text.contains("session"),
            "fixture detail should show the scope"
        );
        assert!(
            text.contains("fixture"),
            "fixture detail should show the binding type"
        );
        assert!(
            text.contains("Consumers"),
            "fixture with consumers should show the Consumers section"
        );
        assert!(
            text.contains("Defined In"),
            "fixture with conftest_idx should show the Defined In section"
        );
    }

    #[test]
    fn render_detail_fixture_broken_edge() {
        let graph = fixture_with_broken_edges_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Fixture,
            index: 0,
        };
        let lines = render_detail(&graph, Some(&node_ref));
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("missing_dep"),
            "broken edge detail should show the unresolved qualifier"
        );
        assert!(
            text.contains("unresolved"),
            "broken edge detail should indicate the edge is unresolved"
        );
    }

    #[test]
    fn render_detail_test_shows_all_fields() {
        let graph = test_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Test,
            index: 0,
        };
        let lines = render_detail(&graph, Some(&node_ref));
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("test_create_user"),
            "test detail should show the test node_id"
        );
        assert!(
            text.contains("async"),
            "test detail should show the async field"
        );
        assert!(
            text.contains("Fixture Dependencies"),
            "test with fixture deps should show the Fixture Dependencies section"
        );
        assert!(
            text.contains("Marks"),
            "test with marks should show the Marks section"
        );
    }

    #[test]
    fn render_detail_test_parametrized_shows_variants() {
        let graph = test_parametrized_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Test,
            index: 0,
        };
        let lines = render_detail(&graph, Some(&node_ref));
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("param_id"),
            "parametrized test should show param_id"
        );
        assert!(
            text.contains("Variants"),
            "parametrized test should show Variants section"
        );
    }

    #[test]
    fn render_detail_mark_shows_used_by() {
        let graph = mark_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Mark,
            index: 0,
        };
        let lines = render_detail(&graph, Some(&node_ref));
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("slow"),
            "mark detail should show the mark name"
        );
        assert!(
            text.contains("Used By"),
            "mark with consumers should show the Used By section"
        );
        assert!(
            text.contains("test_one"),
            "mark detail should list tests that use it"
        );
    }

    #[test]
    fn render_detail_conftest_shows_fixtures() {
        let graph = conftest_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Conftest,
            index: 0,
        };
        let lines = render_detail(&graph, Some(&node_ref));
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("tests/conftest.py"),
            "conftest detail should show the path"
        );
        assert!(
            text.contains("Fixtures"),
            "conftest with fixtures should show the Fixtures section"
        );
    }

    #[test]
    fn render_detail_plugin_shows_protocols_and_fixtures() {
        let graph = plugin_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Plugin,
            index: 0,
        };
        let lines = render_detail(&graph, Some(&node_ref));
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("capture"),
            "plugin detail should show the plugin name"
        );
        assert!(
            text.contains("CollectorProvider"),
            "plugin detail should show protocol names"
        );
        assert!(
            text.contains("Fixtures"),
            "plugin with fixtures should show the Fixtures section"
        );
    }

    // ── Group detail tests ───────────────────────────────────────────────

    #[test]
    fn render_group_detail_shows_base_name_and_variant_count() {
        let graph = test_parametrized_graph();
        let indices = vec![0, 1];
        let lines = render_group_detail(&graph, &indices);
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("test_add"),
            "group detail should show the base name"
        );
        assert!(
            text.contains("2"),
            "group detail should show the variant count"
        );
    }

    #[test]
    fn render_group_detail_shows_shared_marks() {
        let mut graph = InspectGraph::default();
        graph.marks.push(MarkNode {
            name: "slow".to_string(),
            used_by: vec![0, 1],
        });
        graph.tests.push(TestNode {
            node_id: "t.py::test_add[1]".to_string(),
            is_async: false,
            param_id: Some("1".to_string()),
            param_count: 2,
            variants: vec![1],
            fixture_deps: vec![],
            marks: vec![0],
        });
        graph.tests.push(TestNode {
            node_id: "t.py::test_add[2]".to_string(),
            is_async: false,
            param_id: Some("2".to_string()),
            param_count: 2,
            variants: vec![0],
            fixture_deps: vec![],
            marks: vec![0],
        });
        let lines = render_group_detail(&graph, &[0, 1]);
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("Shared Marks"),
            "group detail should show shared marks when all variants share a mark"
        );
        assert!(
            text.contains("slow"),
            "group detail should show the shared mark name"
        );
    }

    #[test]
    fn render_group_detail_empty_indices() {
        let graph = InspectGraph::default();
        let lines = render_group_detail(&graph, &[]);
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("Empty group"),
            "empty indices should show a placeholder message"
        );
    }

    // ── render_preview tests ─────────────────────────────────────────────

    #[test]
    fn preview_fixture_shows_scope_and_source() {
        let graph = fixture_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Fixture,
            index: 0,
        };
        let lines = render_preview(&graph, &node_ref);
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("db_session"),
            "fixture preview should show the fixture name"
        );
        assert!(
            text.contains("session"),
            "fixture preview should show the scope"
        );
        assert!(
            text.contains("tests/conftest.py"),
            "fixture preview should show the source"
        );
        assert!(
            !text.contains("binding"),
            "fixture preview should omit binding_type"
        );
        assert!(
            !text.contains("autouse"),
            "fixture preview should omit autouse"
        );
    }

    #[test]
    fn preview_fixture_shows_consumers_section() {
        let graph = fixture_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Fixture,
            index: 0,
        };
        let lines = render_preview(&graph, &node_ref);
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("Consumers"),
            "fixture preview should show the Consumers section when consumers exist"
        );
        assert!(
            text.contains("test_create_user"),
            "fixture preview should list consumer test names"
        );
    }

    #[test]
    fn preview_truncates_edges_at_three() {
        let mut graph = InspectGraph::default();
        // 5 consumer tests — only first 3 should appear, then "2 more"
        for i in 0..5 {
            graph.tests.push(TestNode {
                node_id: format!("tests/test_x.py::test_{i}"),
                is_async: false,
                param_id: None,
                param_count: 0,
                variants: vec![],
                fixture_deps: vec![0],
                marks: vec![],
            });
        }
        graph.fixtures.push(FixtureNode {
            name: "shared_fixture".to_string(),
            binding_type: "fixture".to_string(),
            scope: "function".to_string(),
            autouse: false,
            source: "conftest.py".to_string(),
            is_async: false,
            description: String::new(),
            consumers: (0..5)
                .map(|i| NodeRef {
                    kind: NodeKind::Test,
                    index: i,
                })
                .collect(),
            conftest_idx: None,
            plugin_idx: None,
        });
        let node_ref = NodeRef {
            kind: NodeKind::Fixture,
            index: 0,
        };
        let lines = render_preview(&graph, &node_ref);
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("2 more"),
            "preview should show '2 more' when 5 consumers exist but only 3 are shown"
        );
        assert!(
            text.contains("test_0"),
            "preview should show the first consumer"
        );
        assert!(
            text.contains("test_2"),
            "preview should show the third consumer"
        );
        assert!(
            !text.contains("test_3"),
            "preview should not show the fourth consumer"
        );
    }

    #[test]
    fn preview_test_shows_async_and_edges() {
        let graph = test_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Test,
            index: 0,
        };
        let lines = render_preview(&graph, &node_ref);
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("test_create_user"),
            "test preview should show the test node_id"
        );
        assert!(
            text.contains("async"),
            "test preview should show async status"
        );
        assert!(
            text.contains("Fixtures"),
            "test preview should show the Fixtures section"
        );
        assert!(
            text.contains("Marks"),
            "test preview should show the Marks section"
        );
    }

    #[test]
    fn preview_mark_shows_tests_section() {
        let graph = mark_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Mark,
            index: 0,
        };
        let lines = render_preview(&graph, &node_ref);
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("slow"),
            "mark preview should show the mark name"
        );
        assert!(
            text.contains("Tests"),
            "mark preview should show a Tests section"
        );
        assert!(
            text.contains("test_one"),
            "mark preview should list tests that use it"
        );
    }

    #[test]
    fn preview_conftest_shows_fixtures() {
        let graph = conftest_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Conftest,
            index: 0,
        };
        let lines = render_preview(&graph, &node_ref);
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("tests/conftest.py"),
            "conftest preview should show the path"
        );
        assert!(
            text.contains("Fixtures"),
            "conftest preview should show Fixtures section"
        );
    }

    #[test]
    fn preview_plugin_shows_protocols_and_fixtures() {
        let graph = plugin_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Plugin,
            index: 0,
        };
        let lines = render_preview(&graph, &node_ref);
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("capture"),
            "plugin preview should show the plugin name"
        );
        assert!(
            text.contains("CollectorProvider"),
            "plugin preview should show protocols"
        );
        assert!(
            text.contains("Fixtures"),
            "plugin preview should show Fixtures section"
        );
    }

    // ── Edge navigation helper tests ────────────────────────────────────

    #[test]
    fn fixture_edges_include_consumers_and_conftest() {
        let graph = fixture_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Fixture,
            index: 0,
        };
        // fixture_graph has 1 consumer (Test index 0) and conftest_idx = Some(0)
        assert_eq!(
            selectable_edge_count(&graph, &node_ref),
            2,
            "fixture with one consumer and one conftest should have 2 selectable edges"
        );
        assert_eq!(
            edge_node_at(&graph, &node_ref, 0),
            Some(NodeRef {
                kind: NodeKind::Test,
                index: 0
            }),
            "first edge should be the consumer test"
        );
        assert_eq!(
            edge_node_at(&graph, &node_ref, 1),
            Some(NodeRef {
                kind: NodeKind::Conftest,
                index: 0
            }),
            "second edge should be the conftest owner"
        );
    }

    #[test]
    fn fixture_edges_include_plugin() {
        let graph = plugin_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Fixture,
            index: 0,
        };
        // plugin_graph fixture has no consumers, no conftest, plugin_idx = Some(0)
        assert_eq!(
            selectable_edge_count(&graph, &node_ref),
            1,
            "fixture with only a plugin owner should have 1 selectable edge"
        );
        assert_eq!(
            edge_node_at(&graph, &node_ref, 0),
            Some(NodeRef {
                kind: NodeKind::Plugin,
                index: 0
            }),
            "sole edge should be the plugin owner"
        );
    }

    #[test]
    fn test_edges_include_fixtures_and_marks() {
        let graph = test_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Test,
            index: 0,
        };
        // test_graph test has fixture_deps=[0] and marks=[0]
        assert_eq!(
            selectable_edge_count(&graph, &node_ref),
            2,
            "test with one fixture dep and one mark should have 2 selectable edges"
        );
        assert_eq!(
            edge_node_at(&graph, &node_ref, 0),
            Some(NodeRef {
                kind: NodeKind::Fixture,
                index: 0
            }),
            "first edge should be the fixture dependency"
        );
        assert_eq!(
            edge_node_at(&graph, &node_ref, 1),
            Some(NodeRef {
                kind: NodeKind::Mark,
                index: 0
            }),
            "second edge should be the mark"
        );
    }

    #[test]
    fn mark_edges_are_used_by_tests() {
        let graph = mark_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Mark,
            index: 0,
        };
        // mark_graph mark has used_by=[0, 1]
        assert_eq!(
            selectable_edge_count(&graph, &node_ref),
            2,
            "mark used by two tests should have 2 selectable edges"
        );
        assert_eq!(
            edge_node_at(&graph, &node_ref, 0),
            Some(NodeRef {
                kind: NodeKind::Test,
                index: 0
            }),
            "first edge should be the first test"
        );
        assert_eq!(
            edge_node_at(&graph, &node_ref, 1),
            Some(NodeRef {
                kind: NodeKind::Test,
                index: 1
            }),
            "second edge should be the second test"
        );
    }

    #[test]
    fn conftest_edges_include_fixtures() {
        let graph = conftest_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Conftest,
            index: 0,
        };
        // conftest_graph conftest has fixtures=[0, 1]
        assert_eq!(
            selectable_edge_count(&graph, &node_ref),
            2,
            "conftest with two fixtures should have 2 selectable edges"
        );
        assert_eq!(
            edge_node_at(&graph, &node_ref, 0),
            Some(NodeRef {
                kind: NodeKind::Fixture,
                index: 0
            }),
            "first edge should be the first fixture"
        );
        assert_eq!(
            edge_node_at(&graph, &node_ref, 1),
            Some(NodeRef {
                kind: NodeKind::Fixture,
                index: 1
            }),
            "second edge should be the second fixture"
        );
    }

    #[test]
    fn plugin_edges_are_fixtures() {
        let graph = plugin_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Plugin,
            index: 0,
        };
        // plugin_graph plugin has fixtures=[0]
        assert_eq!(
            selectable_edge_count(&graph, &node_ref),
            1,
            "plugin with one fixture should have 1 selectable edge"
        );
        assert_eq!(
            edge_node_at(&graph, &node_ref, 0),
            Some(NodeRef {
                kind: NodeKind::Fixture,
                index: 0
            }),
            "sole edge should be the fixture"
        );
    }

    #[test]
    fn edge_node_at_out_of_bounds_returns_none() {
        let graph = plugin_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Fixture,
            index: 0,
        };
        assert_eq!(
            edge_node_at(&graph, &node_ref, 1),
            None,
            "index beyond edge count should return None"
        );
        assert_eq!(
            edge_node_at(&graph, &node_ref, 100),
            None,
            "large index should return None"
        );
    }

    #[test]
    fn fixture_with_no_edges_has_zero_count() {
        let graph = fixture_with_broken_edges_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Fixture,
            index: 0,
        };
        // This fixture has no consumers, no conftest_idx, no plugin_idx
        assert_eq!(
            selectable_edge_count(&graph, &node_ref),
            0,
            "fixture with no consumers or owners should have 0 selectable edges"
        );
    }
}

#[cfg(test)]
mod snapshot_tests {
    use super::*;
    use crate::inspect::app::InspectApp;
    use crate::inspect::graph::BrokenEdge;
    use crate::inspect::graph::nodes::*;
    use crate::inspect::nav::Screen;
    use crate::inspect::ui::draw;
    use insta::assert_snapshot;
    use ratatui::{Terminal, backend::TestBackend};

    /// Helper: create a `TestBackend` terminal of the given size, render the
    /// app, and return the buffer as a string for snapshot comparison.
    fn render_to_string(app: &InspectApp, width: u16, height: u16) -> String {
        let backend = TestBackend::new(width, height);
        let mut terminal =
            Terminal::new(backend).expect("TestBackend terminal creation should not fail");
        terminal
            .draw(|f| draw(f, app))
            .expect("drawing should not fail");
        terminal.backend().to_string()
    }

    #[test]
    fn snap_detail_fixture() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "db_session".to_string(),
            binding_type: "fixture".to_string(),
            scope: "session".to_string(),
            autouse: false,
            source: "tests/conftest.py".to_string(),
            is_async: false,
            description: "Database session fixture".to_string(),
            consumers: vec![NodeRef {
                kind: NodeKind::Test,
                index: 0,
            }],
            conftest_idx: Some(0),
            plugin_idx: None,
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_db.py::test_create_user".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![0],
            marks: vec![],
        });
        graph.conftests.push(ConftestNode {
            path: "tests/conftest.py".to_string(),
            fixtures: vec![0],
        });
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 120;
        app.nav.push(Screen::NodeFocus {
            node: NodeRef {
                kind: NodeKind::Fixture,
                index: 0,
            },
            selected: 0,
        });
        assert_snapshot!("detail_fixture", render_to_string(&app, 120, 24));
    }

    #[test]
    fn snap_detail_test() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "db_session".to_string(),
            binding_type: "fixture".to_string(),
            scope: "function".to_string(),
            autouse: false,
            source: String::new(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            conftest_idx: None,
            plugin_idx: None,
        });
        graph.marks.push(MarkNode {
            name: "slow".to_string(),
            used_by: vec![0],
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_db.py::test_create_user".to_string(),
            is_async: true,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![0],
            marks: vec![0],
        });
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 120;
        app.nav.push(Screen::NodeFocus {
            node: NodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            selected: 0,
        });
        assert_snapshot!("detail_test", render_to_string(&app, 120, 24));
    }

    #[test]
    fn snap_detail_test_parametrized() {
        let mut graph = InspectGraph::default();
        graph.tests.push(TestNode {
            node_id: "tests/test_math.py::test_add[1+2]".to_string(),
            is_async: false,
            param_id: Some("1+2".to_string()),
            param_count: 2,
            variants: vec![1],
            fixture_deps: vec![],
            marks: vec![],
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_math.py::test_add[3+4]".to_string(),
            is_async: false,
            param_id: Some("3+4".to_string()),
            param_count: 2,
            variants: vec![0],
            fixture_deps: vec![],
            marks: vec![],
        });
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 120;
        app.nav.push(Screen::NodeFocus {
            node: NodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            selected: 0,
        });
        assert_snapshot!("detail_test_parametrized", render_to_string(&app, 120, 24));
    }

    #[test]
    fn snap_detail_mark() {
        let mut graph = InspectGraph::default();
        graph.tests.push(TestNode {
            node_id: "tests/test_a.py::test_one".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![0],
        });
        graph.tests.push(TestNode {
            node_id: "tests/test_b.py::test_two".to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![0],
        });
        graph.marks.push(MarkNode {
            name: "slow".to_string(),
            used_by: vec![0, 1],
        });
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 120;
        app.nav.push(Screen::NodeFocus {
            node: NodeRef {
                kind: NodeKind::Mark,
                index: 0,
            },
            selected: 0,
        });
        assert_snapshot!("detail_mark", render_to_string(&app, 120, 24));
    }

    #[test]
    fn snap_detail_conftest() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "db".to_string(),
            binding_type: "fixture".to_string(),
            scope: "session".to_string(),
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
        });
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 120;
        app.nav.push(Screen::NodeFocus {
            node: NodeRef {
                kind: NodeKind::Conftest,
                index: 0,
            },
            selected: 0,
        });
        assert_snapshot!("detail_conftest", render_to_string(&app, 120, 24));
    }

    #[test]
    fn snap_detail_plugin() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "std_capture".to_string(),
            binding_type: "fixture".to_string(),
            scope: "function".to_string(),
            autouse: false,
            source: "<plugin:capture>".to_string(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            conftest_idx: None,
            plugin_idx: Some(0),
        });
        graph.plugins.push(PluginNode {
            name: "capture".to_string(),
            protocols: vec![
                "CollectorProvider".to_string(),
                "ReporterProvider".to_string(),
            ],
            fixtures: vec![0],
        });
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 120;
        app.nav.push(Screen::NodeFocus {
            node: NodeRef {
                kind: NodeKind::Plugin,
                index: 0,
            },
            selected: 0,
        });
        assert_snapshot!("detail_plugin", render_to_string(&app, 120, 24));
    }

    #[test]
    fn snap_detail_fixture_broken_edge() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "db_session".to_string(),
            binding_type: "fixture".to_string(),
            scope: "session".to_string(),
            autouse: false,
            source: "tests/conftest.py".to_string(),
            is_async: true,
            description: "".to_string(),
            consumers: vec![],
            conftest_idx: None,
            plugin_idx: None,
        });
        graph.broken_edges.push(BrokenEdge {
            from: NodeRef {
                kind: NodeKind::Fixture,
                index: 0,
            },
            qualifier: "missing_dep".to_string(),
            binding_type: "fixture".to_string(),
        });
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 120;
        app.nav.push(Screen::NodeFocus {
            node: NodeRef {
                kind: NodeKind::Fixture,
                index: 0,
            },
            selected: 0,
        });
        assert_snapshot!(
            "detail_fixture_broken_edge",
            render_to_string(&app, 120, 24)
        );
    }

    #[test]
    fn snap_detail_no_selection() {
        let graph = InspectGraph::default();
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 120;
        assert_snapshot!("detail_no_selection", render_to_string(&app, 120, 24));
    }
}
