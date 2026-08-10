//! Fixture node detail and preview rendering.

use ratatui::text::{Line, Span};

use crate::inspect::graph::{InspectGraph, NodeRef};

use super::styles::{
    bool_field, broken_edge_line, broken_edges_for, connection_line, field_line, preview_edges,
    section_header, sigil_style,
};

pub fn render_fixture<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
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

    // Broken edges (unresolved dependencies)
    let broken = broken_edges_for(&graph.broken_edges, node_ref);
    if !broken.is_empty() {
        lines.push(Line::from(""));
        lines.push(section_header("Depends On"));
        for edge in &broken {
            lines.push(broken_edge_line(&edge.qualifier, &edge.binding_type));
        }
    }

    // Consumers
    if !fixture.consumers.is_empty() {
        lines.push(Line::from(""));
        lines.push(section_header("Consumers"));
        for consumer in &fixture.consumers {
            let sigil = consumer.kind.sigil();
            lines.push(connection_line(sigil, graph.node_name(consumer)));
        }
    }

    // Owner (conftest or plugin)
    if let Some(conftest_idx) = fixture.conftest_idx {
        lines.push(Line::from(""));
        lines.push(section_header("Defined In"));
        lines.push(connection_line('C', &graph.declarations[conftest_idx].path));
    }
    if let Some(plugin_idx) = fixture.plugin_idx {
        lines.push(Line::from(""));
        lines.push(section_header("Provided By"));
        lines.push(connection_line('P', &graph.plugins[plugin_idx].name));
    }

    lines
}

pub fn preview_fixture<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
    let fixture = &graph.fixtures[node_ref.index];
    let mut lines = vec![
        Line::from(vec![
            Span::styled("F", sigil_style()),
            Span::raw(format!(" {}", fixture.name)),
        ]),
        Line::from(""),
        field_line("scope", &fixture.scope),
        field_line("source", &fixture.source),
    ];

    if !fixture.consumers.is_empty() {
        lines.push(Line::from(""));
        lines.push(section_header(&format!(
            "Consumers ({})",
            fixture.consumers.len()
        )));
        preview_edges(&mut lines, &fixture.consumers, graph, 3);
    }

    lines
}

pub fn collect_edges(graph: &InspectGraph, node: &NodeRef) -> Vec<NodeRef> {
    use crate::inspect::graph::NodeKind;

    let f = &graph.fixtures[node.index];
    let mut edges = Vec::new();
    edges.extend(f.consumers.iter().cloned());
    if let Some(idx) = f.conftest_idx {
        edges.push(NodeRef::new(NodeKind::Declaration, idx));
    }
    if let Some(idx) = f.plugin_idx {
        edges.push(NodeRef::new(NodeKind::Plugin, idx));
    }
    edges
}
