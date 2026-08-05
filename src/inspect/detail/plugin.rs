//! Plugin node detail and preview rendering.

use ratatui::text::{Line, Span};

use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef};

use super::styles::{connection_line, field_line, preview_edges, section_header, sigil_style};

pub fn render_plugin<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
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

pub fn preview_plugin<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
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

    if !plugin.fixtures.is_empty() {
        let edge_refs: Vec<NodeRef> = plugin
            .fixtures
            .iter()
            .map(|&idx| NodeRef {
                kind: NodeKind::Fixture,
                index: idx,
            })
            .collect();
        lines.push(Line::from(""));
        lines.push(section_header(&format!("Fixtures ({})", edge_refs.len())));
        preview_edges(&mut lines, &edge_refs, graph, 3);
    }

    lines
}

pub fn collect_edges(graph: &InspectGraph, node: &NodeRef) -> Vec<NodeRef> {
    graph.plugins[node.index]
        .fixtures
        .iter()
        .map(|&idx| NodeRef::new(NodeKind::Fixture, idx))
        .collect()
}
