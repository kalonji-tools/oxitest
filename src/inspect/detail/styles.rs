//! Shared styles and helper functions for detail rendering.

use ratatui::{
    style::{Color, Modifier, Style},
    text::{Line, Span},
};

use crate::inspect::graph::{BrokenEdge, InspectGraph, NodeRef};

// ── Styles ──────────────────────────────────────────────────────────────────

pub fn label_style() -> Style {
    Style::default()
        .fg(Color::DarkGray)
        .add_modifier(Modifier::BOLD)
}

pub fn value_style() -> Style {
    Style::default().fg(Color::White)
}

pub fn sigil_style() -> Style {
    Style::default().fg(Color::Cyan)
}

pub fn warning_style() -> Style {
    Style::default().fg(Color::Yellow)
}

pub fn header_style() -> Style {
    Style::default()
        .fg(Color::Yellow)
        .add_modifier(Modifier::BOLD)
}

// ── Helpers ─────────────────────────────────────────────────────────────────

/// Render a single `label: value` field line.
pub fn field_line<'a>(label: &str, value: &str) -> Line<'a> {
    Line::from(vec![
        Span::styled(format!("  {label}: "), label_style()),
        Span::styled(value.to_string(), value_style()),
    ])
}

/// Render a boolean field as yes/no.
pub fn bool_field<'a>(label: &str, value: bool) -> Line<'a> {
    field_line(label, if value { "yes" } else { "no" })
}

/// Render a section header (e.g., "Depends On", "Consumers").
pub fn section_header<'a>(title: &str) -> Line<'a> {
    Line::from(Span::styled(format!("  {title}"), header_style()))
}

/// Render a connection entry: `sigil name`.
pub fn connection_line<'a>(sigil: char, name: &str) -> Line<'a> {
    Line::from(vec![
        Span::raw("    "),
        Span::styled(format!("{sigil}"), sigil_style()),
        Span::raw(format!(" {name}")),
    ])
}

/// Render a broken edge entry with warning sigil.
pub fn broken_edge_line<'a>(qualifier: &str, binding_type: &str) -> Line<'a> {
    Line::from(vec![
        Span::raw("    "),
        Span::styled("!", warning_style()),
        Span::raw(format!(" {qualifier}")),
        Span::styled(format!(" ({binding_type}, unresolved)"), warning_style()),
    ])
}

/// Collect broken edges for a given node reference.
pub fn broken_edges_for<'a>(
    broken_edges: &'a [BrokenEdge],
    node_ref: &NodeRef,
) -> Vec<&'a BrokenEdge> {
    broken_edges
        .iter()
        .filter(|e| e.from == *node_ref)
        .collect()
}

/// Append up to `max_shown` connection lines from `edges`, then a "N more"
/// line if any are truncated.
pub fn preview_edges<'a>(
    lines: &mut Vec<Line<'a>>,
    edges: &[NodeRef],
    graph: &InspectGraph,
    max_shown: usize,
) {
    for r in edges.iter().take(max_shown) {
        lines.push(connection_line(r.kind.sigil(), graph.node_name(r)));
    }
    let remaining = edges.len().saturating_sub(max_shown);
    if remaining > 0 {
        lines.push(Line::from(Span::styled(
            format!("  · · · {remaining} more"),
            label_style(),
        )));
    }
}

/// Render one autouse row: the fixture sigil, its name, and its Lifetime.
///
/// The tier is a column rather than a labelled field, so the rows line up and
/// read as a list. The value is always a `Lifetime` — only a `ModuleSource` or
/// a `PluginModuleSource` can carry `autouse`, and both declare one (#1722).
pub fn autouse_line<'a>(name: &str, lifetime: &str) -> Line<'a> {
    Line::from(vec![
        Span::raw("    "),
        Span::styled("F", sigil_style()),
        Span::raw(format!(" {name:<24} ")),
        Span::styled(lifetime.to_string(), label_style()),
    ])
}

/// Render an indented status row inside a section — "none", "loading…" — for
/// a section that renders even when it has no rows to show.
pub fn note_line<'a>(text: &str) -> Line<'a> {
    Line::from(vec![
        Span::raw("    "),
        Span::styled(text.to_string(), value_style()),
    ])
}
