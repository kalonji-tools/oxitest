//! Mark node detail and preview rendering.

use ratatui::text::{Line, Span};

use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef};

use super::styles::{connection_line, field_line, preview_edges, section_header, sigil_style};

pub(crate) fn render_mark<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
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

pub(crate) fn preview_mark<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
    let mark = &graph.marks[node_ref.index];
    let mut lines = vec![
        Line::from(vec![
            Span::styled("M", sigil_style()),
            Span::raw(format!(" {}", mark.name)),
        ]),
        Line::from(""),
    ];

    if !mark.used_by.is_empty() {
        let edge_refs: Vec<NodeRef> = mark
            .used_by
            .iter()
            .map(|&idx| NodeRef {
                kind: NodeKind::Test,
                index: idx,
            })
            .collect();
        lines.push(section_header(&format!("Tests ({})", edge_refs.len())));
        preview_edges(&mut lines, &edge_refs, graph, 3);
    }

    lines
}

pub(crate) fn collect_edges(graph: &InspectGraph, node: &NodeRef) -> Vec<NodeRef> {
    graph.marks[node.index]
        .used_by
        .iter()
        .map(|&idx| NodeRef::new(NodeKind::Test, idx))
        .collect()
}
