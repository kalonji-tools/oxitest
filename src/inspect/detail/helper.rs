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
        field_line("namespace", &helper.namespace),
        field_line("signature", &helper.signature),
        field_line("source", &helper.source),
    ];

    if let Some(docstring) = &helper.docstring {
        lines.push(field_line("docstring", docstring));
    }

    // Owner: conftest or plugin
    lines.push(Line::from(""));
    lines.push(section_header("Defined In"));
    if let Some(conftest_idx) = helper.conftest_idx {
        lines.push(connection_line('C', &graph.conftests[conftest_idx].path));
    } else if let Some(plugin_idx) = helper.plugin_idx {
        lines.push(connection_line('P', &graph.plugins[plugin_idx].name));
    } else {
        lines.push(connection_line('?', &helper.source));
    }

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

    // Defined In — single owner (conftest or plugin)
    lines.push(Line::from(""));
    lines.push(section_header("Defined In (1)"));
    if let Some(conftest_idx) = helper.conftest_idx {
        lines.push(connection_line('C', &graph.conftests[conftest_idx].path));
    } else if let Some(plugin_idx) = helper.plugin_idx {
        lines.push(connection_line('P', &graph.plugins[plugin_idx].name));
    } else {
        lines.push(connection_line('?', &helper.source));
    }

    lines
}

pub(crate) fn collect_edges(graph: &InspectGraph, node: &NodeRef) -> Vec<NodeRef> {
    let helper = &graph.helpers[node.index];
    if let Some(conftest_idx) = helper.conftest_idx {
        vec![NodeRef::new(NodeKind::Conftest, conftest_idx)]
    } else if let Some(plugin_idx) = helper.plugin_idx {
        vec![NodeRef::new(NodeKind::Plugin, plugin_idx)]
    } else {
        vec![]
    }
}
