//! Helper node detail and preview rendering.

use ratatui::text::{Line, Span};

use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef};

use super::styles::{connection_line, field_line, section_header, sigil_style};

pub(crate) fn render_helper<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
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

pub(crate) fn preview_helper<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
    let helper = &graph.helpers[node_ref.index];
    let mut lines = vec![
        Line::from(vec![
            Span::styled("H", sigil_style()),
            Span::raw(format!(" {}", helper.name)),
        ]),
        Line::from(""),
        field_line("signature", &helper.signature),
    ];

    // Defined In — always a single conftest, no truncation needed
    lines.push(Line::from(""));
    lines.push(section_header("Defined In (1)"));
    lines.push(connection_line(
        'C',
        &graph.conftests[helper.conftest_idx].path,
    ));

    lines
}

pub(crate) fn collect_edges(graph: &InspectGraph, node: &NodeRef) -> Vec<NodeRef> {
    vec![NodeRef::new(
        NodeKind::Conftest,
        graph.helpers[node.index].conftest_idx,
    )]
}
