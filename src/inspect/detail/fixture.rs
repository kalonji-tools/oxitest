//! Fixture node detail and preview rendering.

use ratatui::text::{Line, Span};

use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef};

use super::styles::{
    bool_field, connection_line, field_line, preview_edges, section_header, sigil_style,
};

/// The tier row to show a user for this fixture, as `(label, value)`.
///
/// A declared fixture reports the `Lifetime` its declaration wrote, because
/// that is the word the user typed. An ambient fixture — builtin, framework or
/// plugin — declares none, and `session` is a `Scope` that no `Lifetime` maps
/// to, so its caching tier is the only answer available.
///
/// **The label moves with the value.** Rendering a `Scope` word under the
/// `lifetime` label would print `lifetime: each`, presenting one vocabulary as
/// the other — the same confusion this row exists to remove, inverted. One
/// word, one meaning (#1722).
fn tier_row(fixture: &super::super::graph::nodes::FixtureNode) -> (&str, &str) {
    if fixture.lifetime.is_empty() {
        ("scope", &fixture.scope)
    } else {
        ("lifetime", &fixture.lifetime)
    }
}

pub fn render_fixture<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
    let fixture = &graph.fixtures[node_ref.index];
    let mut lines = vec![
        Line::from(vec![
            Span::styled("F", sigil_style()),
            Span::raw(format!(" {}", fixture.name)),
        ]),
        Line::from(""),
        {
            let (label, value) = tier_row(fixture);
            field_line(label, value)
        },
        field_line("binding", &fixture.binding_type),
        bool_field("autouse", fixture.autouse),
        bool_field("async", fixture.is_async),
        field_line("source", &fixture.source),
    ];

    if !fixture.description.is_empty() {
        lines.push(field_line("description", &fixture.description));
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

    // Owner (declaration or plugin)
    if let Some(declaration_idx) = fixture.declaration_idx {
        lines.push(Line::from(""));
        lines.push(section_header("Defined In"));
        lines.push(connection_line(
            NodeKind::Declaration.sigil(),
            &graph.declarations[declaration_idx].path,
        ));
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
        {
            let (label, value) = tier_row(fixture);
            field_line(label, value)
        },
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
    if let Some(idx) = f.declaration_idx {
        edges.push(NodeRef::new(NodeKind::Declaration, idx));
    }
    if let Some(idx) = f.plugin_idx {
        edges.push(NodeRef::new(NodeKind::Plugin, idx));
    }
    edges
}
