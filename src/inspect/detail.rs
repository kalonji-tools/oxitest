//! Node detail view rendering for `oxitest inspect`.
//!
//! Renders the right-pane detail view for a selected node.  Each of the
//! six node types has a dedicated renderer that shows its fields and
//! navigable connections.

use ratatui::{
    style::{Color, Modifier, Style},
    text::{Line, Span},
};

use super::graph::{self, BrokenEdge, InspectGraph, NodeKind, NodeRef};

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
        NodeKind::Fixture => render_fixture(graph, node_ref),
        NodeKind::Test => render_test(graph, node_ref),
        NodeKind::Mark => render_mark(graph, node_ref),
        NodeKind::Conftest => render_conftest(graph, node_ref),
        NodeKind::Plugin => render_plugin(graph, node_ref),
        NodeKind::Helper => render_helper(graph, node_ref),
    }
}

// ── Styles ──────────────────────────────────────────────────────────────────

fn label_style() -> Style {
    Style::default()
        .fg(Color::DarkGray)
        .add_modifier(Modifier::BOLD)
}

fn value_style() -> Style {
    Style::default().fg(Color::White)
}

fn sigil_style() -> Style {
    Style::default().fg(Color::Cyan)
}

fn warning_style() -> Style {
    Style::default().fg(Color::Yellow)
}

fn header_style() -> Style {
    Style::default()
        .fg(Color::Yellow)
        .add_modifier(Modifier::BOLD)
}

// ── Helpers ─────────────────────────────────────────────────────────────────

/// Render a single `label: value` field line.
fn field_line<'a>(label: &str, value: &str) -> Line<'a> {
    Line::from(vec![
        Span::styled(format!("  {label}: "), label_style()),
        Span::styled(value.to_string(), value_style()),
    ])
}

/// Render a boolean field as yes/no.
fn bool_field<'a>(label: &str, value: bool) -> Line<'a> {
    field_line(label, if value { "yes" } else { "no" })
}

/// Render a section header (e.g., "Depends On", "Consumers").
fn section_header<'a>(title: &str) -> Line<'a> {
    Line::from(Span::styled(format!("  {title}"), header_style()))
}

/// Render a connection entry: `sigil name`.
fn connection_line<'a>(sigil: char, name: &str) -> Line<'a> {
    Line::from(vec![
        Span::raw("    "),
        Span::styled(format!("{sigil}"), sigil_style()),
        Span::raw(format!(" {name}")),
    ])
}

/// Render a broken edge entry with warning sigil.
fn broken_edge_line<'a>(qualifier: &str, binding_type: &str) -> Line<'a> {
    Line::from(vec![
        Span::raw("    "),
        Span::styled("!", warning_style()),
        Span::raw(format!(" {qualifier}")),
        Span::styled(format!(" ({binding_type}, unresolved)"), warning_style()),
    ])
}

/// Collect broken edges for a given node reference.
fn broken_edges_for<'a>(broken_edges: &'a [BrokenEdge], node_ref: &NodeRef) -> Vec<&'a BrokenEdge> {
    broken_edges
        .iter()
        .filter(|e| e.from == *node_ref)
        .collect()
}

/// Compute the intersection of index slices across a set of test variants.
///
/// Returns the indices that appear in every variant's slice (e.g. fixture_deps or marks).
/// Returns an empty vec if `indices` is empty.
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

// ── Group detail (parametrize collapse) ──────────────────────────────────

/// Render the detail pane for a collapsed parametrize group.
///
/// Shows the shared base name, variant count, and connections that are
/// common across all variants (fixture deps, marks).
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

// ── Per-type renderers ──────────────────────────────────────────────────────

fn render_fixture<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
    let fixture = &graph.fixtures[node_ref.index];
    let mut lines = vec![
        Line::from(vec![
            Span::styled("F", sigil_style()),
            Span::raw(format!(" {}", fixture.name)),
        ]),
        Line::from(""),
        field_line("scope", &fixture.scope),
        field_line("binding", &fixture.binding_type),
        bool_field("autouse", fixture.autouse),
        bool_field("async", fixture.is_async),
        field_line("source", &fixture.source),
    ];

    if !fixture.description.is_empty() {
        lines.push(field_line("description", &fixture.description));
    }

    // Depends on (other fixtures)
    if !fixture.depends_on.is_empty() {
        lines.push(Line::from(""));
        lines.push(section_header("Depends On"));
        for &dep_idx in &fixture.depends_on {
            lines.push(connection_line('F', &graph.fixtures[dep_idx].name));
        }
    }

    // Broken edges (unresolved dependencies)
    let broken = broken_edges_for(&graph.broken_edges, node_ref);
    if !broken.is_empty() {
        if fixture.depends_on.is_empty() {
            lines.push(Line::from(""));
            lines.push(section_header("Depends On"));
        }
        for edge in &broken {
            lines.push(broken_edge_line(&edge.qualifier, &edge.binding_type));
        }
    }

    // Consumers
    if !fixture.consumers.is_empty() {
        lines.push(Line::from(""));
        lines.push(section_header("Consumers"));
        for (kind, idx) in &fixture.consumers {
            let sigil = kind.sigil();
            let name_ref = NodeRef {
                kind: *kind,
                index: *idx,
            };
            lines.push(connection_line(sigil, graph.node_name(&name_ref)));
        }
    }

    // Owner (conftest or plugin)
    if let Some(conftest_idx) = fixture.conftest_idx {
        lines.push(Line::from(""));
        lines.push(section_header("Defined In"));
        lines.push(connection_line('C', &graph.conftests[conftest_idx].path));
    }
    if let Some(plugin_idx) = fixture.plugin_idx {
        lines.push(Line::from(""));
        lines.push(section_header("Provided By"));
        lines.push(connection_line('P', &graph.plugins[plugin_idx].name));
    }

    lines
}

fn render_test<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
    let test = &graph.tests[node_ref.index];
    let mut lines = vec![
        Line::from(vec![
            Span::styled("T", sigil_style()),
            Span::raw(format!(" {}", test.node_id)),
        ]),
        Line::from(""),
        bool_field("async", test.is_async),
    ];

    if let Some(param_id) = &test.param_id {
        lines.push(field_line("param_id", param_id));
        lines.push(field_line("param_count", &test.param_count.to_string()));
    }

    // Fixture dependencies
    if !test.fixture_deps.is_empty() {
        lines.push(Line::from(""));
        lines.push(section_header("Fixture Dependencies"));
        for &dep_idx in &test.fixture_deps {
            lines.push(connection_line('F', &graph.fixtures[dep_idx].name));
        }
    }

    // Broken edges (unresolved fixture deps)
    let broken = broken_edges_for(&graph.broken_edges, node_ref);
    if !broken.is_empty() {
        if test.fixture_deps.is_empty() {
            lines.push(Line::from(""));
            lines.push(section_header("Fixture Dependencies"));
        }
        for edge in &broken {
            lines.push(broken_edge_line(&edge.qualifier, &edge.binding_type));
        }
    }

    // Marks
    if !test.marks.is_empty() {
        lines.push(Line::from(""));
        lines.push(section_header("Marks"));
        for &mark_idx in &test.marks {
            lines.push(connection_line('M', &graph.marks[mark_idx].name));
        }
    }

    // Variants
    if !test.variants.is_empty() {
        lines.push(Line::from(""));
        lines.push(section_header("Variants"));
        for &variant_idx in &test.variants {
            lines.push(connection_line('T', &graph.tests[variant_idx].node_id));
        }
    }

    lines
}

fn render_mark<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
    let mark = &graph.marks[node_ref.index];
    let mut lines = vec![
        Line::from(vec![
            Span::styled("M", sigil_style()),
            Span::raw(format!(" {}", mark.name)),
        ]),
        Line::from(""),
        field_line("used_by_count", &mark.used_by.len().to_string()),
    ];

    // Tests using this mark
    if !mark.used_by.is_empty() {
        lines.push(Line::from(""));
        lines.push(section_header("Used By"));
        for &test_idx in &mark.used_by {
            lines.push(connection_line('T', &graph.tests[test_idx].node_id));
        }
    }

    lines
}

fn render_conftest<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
    let conftest = &graph.conftests[node_ref.index];
    let mut lines = vec![
        Line::from(vec![
            Span::styled("C", sigil_style()),
            Span::raw(format!(" {}", conftest.path)),
        ]),
        Line::from(""),
        field_line("fixtures_count", &conftest.fixtures.len().to_string()),
        field_line("helpers_count", &conftest.helpers.len().to_string()),
    ];

    // Fixtures defined here
    if !conftest.fixtures.is_empty() {
        lines.push(Line::from(""));
        lines.push(section_header("Fixtures"));
        for &fix_idx in &conftest.fixtures {
            lines.push(connection_line('F', &graph.fixtures[fix_idx].name));
        }
    }

    // Helpers defined here
    if !conftest.helpers.is_empty() {
        lines.push(Line::from(""));
        lines.push(section_header("Helpers"));
        for &helper_idx in &conftest.helpers {
            lines.push(connection_line('H', &graph.helpers[helper_idx].name));
        }
    }

    lines
}

fn render_plugin<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
    let plugin = &graph.plugins[node_ref.index];
    let mut lines = vec![
        Line::from(vec![
            Span::styled("P", sigil_style()),
            Span::raw(format!(" {}", plugin.name)),
        ]),
        Line::from(""),
    ];

    if !plugin.protocols.is_empty() {
        lines.push(field_line("protocols", &plugin.protocols.join(", ")));
    }

    // Fixtures provided by this plugin
    if !plugin.fixtures.is_empty() {
        lines.push(Line::from(""));
        lines.push(section_header("Fixtures"));
        for &fix_idx in &plugin.fixtures {
            lines.push(connection_line('F', &graph.fixtures[fix_idx].name));
        }
    }

    lines
}

fn render_helper<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
    let helper = &graph.helpers[node_ref.index];
    let mut lines = vec![
        Line::from(vec![
            Span::styled("H", sigil_style()),
            Span::raw(format!(" {}", helper.name)),
        ]),
        Line::from(""),
        field_line("signature", &helper.signature),
        field_line("source", &helper.source),
    ];

    if let Some(docstring) = &helper.docstring {
        lines.push(field_line("docstring", docstring));
    }

    // Conftest owner
    lines.push(Line::from(""));
    lines.push(section_header("Defined In"));
    lines.push(connection_line(
        'C',
        &graph.conftests[helper.conftest_idx].path,
    ));

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
            depends_on: vec![],
            consumers: vec![(NodeKind::Test, 0)],
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
            helpers: vec![],
        });
        graph
    }

    /// Build a graph with a fixture that has broken edges.
    fn fixture_with_broken_edges_graph() -> InspectGraph {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "db_session".to_string(),
            binding_type: "fixture".to_string(),
            scope: "session".to_string(),
            autouse: false,
            source: "tests/conftest.py".to_string(),
            is_async: true,
            description: "".to_string(),
            depends_on: vec![],
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
            depends_on: vec![],
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
            depends_on: vec![],
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
            depends_on: vec![],
            consumers: vec![],
            conftest_idx: Some(0),
            plugin_idx: None,
        });
        graph.helpers.push(HelperNode {
            name: "make_db".to_string(),
            signature: "make_db(name: str)".to_string(),
            docstring: None,
            source: "tests/conftest.py".to_string(),
            conftest_idx: 0,
        });
        graph.conftests.push(ConftestNode {
            path: "tests/conftest.py".to_string(),
            fixtures: vec![0, 1],
            helpers: vec![0],
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
            depends_on: vec![],
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

    /// Build a graph for testing a helper detail view.
    fn helper_graph() -> InspectGraph {
        let mut graph = InspectGraph::default();
        graph.conftests.push(ConftestNode {
            path: "tests/conftest.py".to_string(),
            fixtures: vec![],
            helpers: vec![0],
        });
        graph.helpers.push(HelperNode {
            name: "make_db".to_string(),
            signature: "make_db(name: str)".to_string(),
            docstring: Some("Create a test database.".to_string()),
            source: "tests/conftest.py".to_string(),
            conftest_idx: 0,
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
    fn render_detail_conftest_shows_fixtures_and_helpers() {
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
        assert!(
            text.contains("Helpers"),
            "conftest with helpers should show the Helpers section"
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

    #[test]
    fn render_detail_helper_shows_all_fields() {
        let graph = helper_graph();
        let node_ref = NodeRef {
            kind: NodeKind::Helper,
            index: 0,
        };
        let lines = render_detail(&graph, Some(&node_ref));
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        assert!(
            text.contains("make_db"),
            "helper detail should show the helper name"
        );
        assert!(
            text.contains("make_db(name: str)"),
            "helper detail should show the signature"
        );
        assert!(
            text.contains("Create a test database."),
            "helper with docstring should show the docstring"
        );
        assert!(
            text.contains("Defined In"),
            "helper should show the Defined In section"
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
}

#[cfg(test)]
mod snapshot_tests {
    use super::*;
    use crate::inspect::app::InspectApp;
    use crate::inspect::graph::nodes::*;
    use crate::inspect::nav::NavScreen;
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
            depends_on: vec![],
            consumers: vec![(NodeKind::Test, 0)],
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
            helpers: vec![],
        });
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 120;
        app.nav.push(NavScreen::NodeDetail {
            node: NodeRef {
                kind: NodeKind::Fixture,
                index: 0,
            },
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
            depends_on: vec![],
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
        app.nav.push(NavScreen::NodeDetail {
            node: NodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
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
        app.nav.push(NavScreen::NodeDetail {
            node: NodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
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
        app.nav.push(NavScreen::NodeDetail {
            node: NodeRef {
                kind: NodeKind::Mark,
                index: 0,
            },
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
            depends_on: vec![],
            consumers: vec![],
            conftest_idx: Some(0),
            plugin_idx: None,
        });
        graph.helpers.push(HelperNode {
            name: "make_db".to_string(),
            signature: "make_db(name: str)".to_string(),
            docstring: None,
            source: "tests/conftest.py".to_string(),
            conftest_idx: 0,
        });
        graph.conftests.push(ConftestNode {
            path: "tests/conftest.py".to_string(),
            fixtures: vec![0],
            helpers: vec![0],
        });
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 120;
        app.nav.push(NavScreen::NodeDetail {
            node: NodeRef {
                kind: NodeKind::Conftest,
                index: 0,
            },
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
            depends_on: vec![],
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
        app.nav.push(NavScreen::NodeDetail {
            node: NodeRef {
                kind: NodeKind::Plugin,
                index: 0,
            },
        });
        assert_snapshot!("detail_plugin", render_to_string(&app, 120, 24));
    }

    #[test]
    fn snap_detail_helper() {
        let mut graph = InspectGraph::default();
        graph.conftests.push(ConftestNode {
            path: "tests/conftest.py".to_string(),
            fixtures: vec![],
            helpers: vec![0],
        });
        graph.helpers.push(HelperNode {
            name: "make_db".to_string(),
            signature: "make_db(name: str)".to_string(),
            docstring: Some("Create a test database.".to_string()),
            source: "tests/conftest.py".to_string(),
            conftest_idx: 0,
        });
        let mut app = InspectApp::new(Some(graph), None);
        app.terminal_width = 120;
        app.nav.push(NavScreen::NodeDetail {
            node: NodeRef {
                kind: NodeKind::Helper,
                index: 0,
            },
        });
        assert_snapshot!("detail_helper", render_to_string(&app, 120, 24));
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
            depends_on: vec![],
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
        app.nav.push(NavScreen::NodeDetail {
            node: NodeRef {
                kind: NodeKind::Fixture,
                index: 0,
            },
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
