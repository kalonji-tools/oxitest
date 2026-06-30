//! Source view: file reading and editor integration for inspect nodes.

use ratatui::{
    style::{Color, Style},
    text::{Line, Span},
};

use super::graph::{InspectGraph, NodeKind, NodeRef};

/// Extract the source file path and starting line for a node.
///
/// Returns `None` for node kinds without source code (Mark, Plugin, Conftest).
pub(crate) fn node_source_location(
    graph: &InspectGraph,
    node: &NodeRef,
) -> Option<(String, usize)> {
    match node.kind {
        NodeKind::Fixture => {
            let source = &graph.fixtures[node.index].source;
            if source.is_empty() || source.starts_with('<') {
                None
            } else {
                Some((source.clone(), 1))
            }
        }
        NodeKind::Test => {
            let node_id = &graph.tests[node.index].node_id;
            let file = node_id.split("::").next().unwrap_or(node_id);
            Some((file.to_string(), 1))
        }
        NodeKind::Helper => {
            let source = &graph.helpers[node.index].source;
            if source.is_empty() {
                None
            } else {
                Some((source.clone(), 1))
            }
        }
        NodeKind::Mark | NodeKind::Plugin | NodeKind::Conftest => None,
    }
}

/// Read a source file and return it as styled ratatui lines with line numbers.
///
/// On read error, returns a single error line describing the failure.
pub(crate) fn read_source_lines(path: &str) -> Vec<Line<'static>> {
    let content = match std::fs::read_to_string(path) {
        Ok(c) => c,
        Err(e) => return vec![Line::from(format!("  Error reading {path}: {e}"))],
    };
    let line_count = content.lines().count();
    let width = line_count.to_string().len();
    content
        .lines()
        .enumerate()
        .map(|(i, text)| {
            let lineno = i + 1;
            Line::from(vec![
                Span::styled(
                    format!(" {lineno:>width$} │ "),
                    Style::default().fg(Color::DarkGray),
                ),
                Span::raw(text.to_string()),
            ])
        })
        .collect()
}

/// Open the given file at the given line in the user's `$VISUAL` or `$EDITOR`.
///
/// Falls back to `vi` if neither environment variable is set.
/// Returns an error string if the editor cannot be launched or exits non-zero.
pub(crate) fn open_in_editor(path: &str, line: usize) -> Result<(), String> {
    let editor = std::env::var("VISUAL")
        .or_else(|_| std::env::var("EDITOR"))
        .unwrap_or_else(|_| "vi".to_string());
    let status = std::process::Command::new(&editor)
        .arg(format!("+{line}"))
        .arg(path)
        .status()
        .map_err(|e| format!("failed to launch {editor}: {e}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("{editor} exited with {status}"))
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::inspect::graph::InspectGraph;
    use crate::inspect::graph::nodes::{FixtureNode, HelperNode, MarkNode, TestNode};

    fn make_fixture(source: &str) -> FixtureNode {
        FixtureNode {
            name: "fx".to_string(),
            binding_type: String::new(),
            scope: "function".to_string(),
            autouse: false,
            source: source.to_string(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            conftest_idx: None,
            plugin_idx: None,
        }
    }

    fn make_test(node_id: &str) -> TestNode {
        TestNode {
            node_id: node_id.to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![],
        }
    }

    fn make_mark(name: &str) -> MarkNode {
        MarkNode {
            name: name.to_string(),
            used_by: vec![],
        }
    }

    fn make_helper(source: &str) -> HelperNode {
        HelperNode {
            name: "helper_fn".to_string(),
            signature: "helper_fn()".to_string(),
            docstring: None,
            source: source.to_string(),
            conftest_idx: 0,
        }
    }

    #[test]
    fn fixture_source_location() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(make_fixture("tests/conftest.py"));
        let node = NodeRef::new(NodeKind::Fixture, 0);
        let result = node_source_location(&graph, &node);
        assert_eq!(
            result,
            Some(("tests/conftest.py".to_string(), 1)),
            "conftest fixture should return Some with the source path and line 1"
        );
    }

    #[test]
    fn builtin_fixture_returns_none() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(make_fixture("<builtin>"));
        let node = NodeRef::new(NodeKind::Fixture, 0);
        let result = node_source_location(&graph, &node);
        assert!(
            result.is_none(),
            "fixture with '<builtin>' source should return None (no viewable source)"
        );
    }

    #[test]
    fn plugin_fixture_source_returns_none() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(make_fixture("<plugin:cache>"));
        let node = NodeRef::new(NodeKind::Fixture, 0);
        let result = node_source_location(&graph, &node);
        assert!(
            result.is_none(),
            "fixture with '<plugin:...>' source should return None"
        );
    }

    #[test]
    fn empty_fixture_source_returns_none() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(make_fixture(""));
        let node = NodeRef::new(NodeKind::Fixture, 0);
        let result = node_source_location(&graph, &node);
        assert!(
            result.is_none(),
            "fixture with empty source string should return None"
        );
    }

    #[test]
    fn test_source_location_extracts_file() {
        let mut graph = InspectGraph::default();
        graph.tests.push(make_test("tests/test_foo.py::test_bar"));
        let node = NodeRef::new(NodeKind::Test, 0);
        let result = node_source_location(&graph, &node);
        assert_eq!(
            result,
            Some(("tests/test_foo.py".to_string(), 1)),
            "test node_id should be split on '::' to extract the file path"
        );
    }

    #[test]
    fn test_source_location_no_separator() {
        let mut graph = InspectGraph::default();
        graph.tests.push(make_test("tests/test_foo.py"));
        let node = NodeRef::new(NodeKind::Test, 0);
        let result = node_source_location(&graph, &node);
        assert_eq!(
            result,
            Some(("tests/test_foo.py".to_string(), 1)),
            "test node_id with no '::' separator should return the full id as path"
        );
    }

    #[test]
    fn mark_returns_none() {
        let mut graph = InspectGraph::default();
        graph.marks.push(make_mark("slow"));
        let node = NodeRef::new(NodeKind::Mark, 0);
        let result = node_source_location(&graph, &node);
        assert!(
            result.is_none(),
            "Mark kind has no source file and should return None"
        );
    }

    #[test]
    fn plugin_kind_returns_none() {
        use crate::inspect::graph::nodes::PluginNode;
        let mut graph = InspectGraph::default();
        graph.plugins.push(PluginNode {
            name: "capture".to_string(),
            protocols: vec![],
            fixtures: vec![],
        });
        let node = NodeRef::new(NodeKind::Plugin, 0);
        let result = node_source_location(&graph, &node);
        assert!(
            result.is_none(),
            "Plugin kind has no source file and should return None"
        );
    }

    #[test]
    fn conftest_kind_returns_none() {
        use crate::inspect::graph::nodes::ConftestNode;
        let mut graph = InspectGraph::default();
        graph.conftests.push(ConftestNode {
            path: "tests/conftest.py".to_string(),
            fixtures: vec![],
            helpers: vec![],
        });
        let node = NodeRef::new(NodeKind::Conftest, 0);
        let result = node_source_location(&graph, &node);
        assert!(
            result.is_none(),
            "Conftest kind should return None (conftest path is not a source location)"
        );
    }

    #[test]
    fn helper_source_location() {
        let mut graph = InspectGraph::default();
        graph.helpers.push(make_helper("tests/conftest.py"));
        let node = NodeRef::new(NodeKind::Helper, 0);
        let result = node_source_location(&graph, &node);
        assert_eq!(
            result,
            Some(("tests/conftest.py".to_string(), 1)),
            "helper with a non-empty source should return Some with path and line 1"
        );
    }

    #[test]
    fn helper_empty_source_returns_none() {
        let mut graph = InspectGraph::default();
        graph.helpers.push(make_helper(""));
        let node = NodeRef::new(NodeKind::Helper, 0);
        let result = node_source_location(&graph, &node);
        assert!(
            result.is_none(),
            "helper with empty source string should return None"
        );
    }

    #[test]
    fn read_source_lines_nonexistent_file() {
        let lines = read_source_lines("/nonexistent/path/that/does/not/exist.py");
        assert_eq!(
            lines.len(),
            1,
            "nonexistent file should return exactly one error line"
        );
        let text = lines[0]
            .spans
            .iter()
            .map(|s| s.content.as_ref())
            .collect::<String>();
        assert!(
            text.contains("Error reading"),
            "error line should contain 'Error reading', got: {text:?}"
        );
    }

    #[test]
    fn read_source_lines_real_file() {
        // Use this source file itself — it always exists in the test environment.
        let this_file = file!();
        // file!() returns a relative path; make it absolute via the manifest dir.
        let manifest = std::env::var("CARGO_MANIFEST_DIR").unwrap_or_else(|_| ".".to_string());
        let path = format!("{manifest}/{this_file}");
        let lines = read_source_lines(&path);
        assert!(
            lines.len() > 1,
            "reading a real file should return more than one line"
        );
        // Every line should have exactly 2 spans: line-number gutter + content.
        for (i, line) in lines.iter().enumerate() {
            assert_eq!(
                line.spans.len(),
                2,
                "line {i} should have 2 spans (gutter + content)"
            );
        }
    }
}
