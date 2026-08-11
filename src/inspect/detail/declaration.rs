//! Declaration node detail and preview rendering.

use ratatui::text::{Line, Span};

use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef};

use super::styles::{connection_line, field_line, preview_edges, section_header, sigil_style};

pub fn render_declaration<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
    let declaration = &graph.declarations[node_ref.index];
    let mut lines = vec![
        Line::from(vec![
            Span::styled(NodeKind::Declaration.sigil().to_string(), sigil_style()),
            Span::raw(format!(" {}", declaration.path)),
        ]),
        Line::from(""),
        field_line("fixtures_count", &declaration.fixtures.len().to_string()),
    ];

    // Fixtures defined here
    if !declaration.fixtures.is_empty() {
        lines.push(Line::from(""));
        lines.push(section_header("Fixtures"));
        for &fix_idx in &declaration.fixtures {
            lines.push(connection_line('F', &graph.fixtures[fix_idx].name));
        }
    }

    lines
}

pub fn preview_declaration<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
    let declaration = &graph.declarations[node_ref.index];
    let mut lines = vec![
        Line::from(vec![
            Span::styled(NodeKind::Declaration.sigil().to_string(), sigil_style()),
            Span::raw(format!(" {}", declaration.path)),
        ]),
        Line::from(""),
    ];

    if !declaration.fixtures.is_empty() {
        let edge_refs: Vec<NodeRef> = declaration
            .fixtures
            .iter()
            .map(|&idx| NodeRef {
                kind: NodeKind::Fixture,
                index: idx,
            })
            .collect();
        lines.push(section_header(&format!("Fixtures ({})", edge_refs.len())));
        preview_edges(&mut lines, &edge_refs, graph, 3);
    }

    lines
}

pub fn collect_edges(graph: &InspectGraph, node: &NodeRef) -> Vec<NodeRef> {
    let c = &graph.declarations[node.index];
    c.fixtures
        .iter()
        .map(|&idx| NodeRef::new(NodeKind::Fixture, idx))
        .collect()
}
