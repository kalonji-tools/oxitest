//! Test node detail and preview rendering.

use ratatui::text::{Line, Span};

use crate::inspect::app::FixtureDataState;
use crate::inspect::graph::{InspectGraph, NodeKind, NodeRef};

use super::styles::{
    autouse_line, bool_field, connection_line, field_line, note_line, preview_edges,
    section_header, sigil_style,
};

pub fn render_test<'a>(
    graph: &InspectGraph,
    node_ref: &NodeRef,
    state: FixtureDataState,
) -> Vec<Line<'a>> {
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

    // Autouse fixtures that apply to this test's module.
    //
    // Always rendered, unlike the sections around it. ADR-0009 Rule 7 keeps
    // autouse specifically because "the invisibility concern ... is solved by
    // tooling", and a section that disappears when it has nothing to say is
    // indistinguishable from a tool that never looked. The three states are
    // distinguishable for the same reason: "none" must never stand in for
    // "not measured" (#1722).
    lines.push(Line::from(""));
    lines.push(section_header("Autouse (applies here)"));
    match state {
        FixtureDataState::Loading => lines.push(note_line("loading…")),
        FixtureDataState::Unavailable => {
            lines.push(note_line("unavailable — fixture data did not load"));
        }
        FixtureDataState::Ready => {
            // The `[param]` suffix always sits after the `::`, so the first segment
            // is the module either way.
            let module = test.node_id.split("::").next().unwrap_or("");
            match graph.autouse_by_module.get(module) {
                Some(fixtures) if !fixtures.is_empty() => {
                    for fixture in fixtures {
                        lines.push(autouse_line(&fixture.name, &fixture.lifetime));
                    }
                }
                _ => lines.push(note_line("none")),
            }
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

pub fn preview_test<'a>(graph: &InspectGraph, node_ref: &NodeRef) -> Vec<Line<'a>> {
    let test = &graph.tests[node_ref.index];
    let mut lines = vec![
        Line::from(vec![
            Span::styled("T", sigil_style()),
            Span::raw(format!(" {}", test.node_id)),
        ]),
        Line::from(""),
        bool_field("async", test.is_async),
    ];

    if !test.fixture_deps.is_empty() {
        let edge_refs: Vec<NodeRef> = test
            .fixture_deps
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

    if !test.marks.is_empty() {
        let edge_refs: Vec<NodeRef> = test
            .marks
            .iter()
            .map(|&idx| NodeRef {
                kind: NodeKind::Mark,
                index: idx,
            })
            .collect();
        lines.push(Line::from(""));
        lines.push(section_header(&format!("Marks ({})", edge_refs.len())));
        preview_edges(&mut lines, &edge_refs, graph, 3);
    }

    lines
}

pub fn collect_edges(graph: &InspectGraph, node: &NodeRef) -> Vec<NodeRef> {
    let t = &graph.tests[node.index];
    let mut edges: Vec<NodeRef> = t
        .fixture_deps
        .iter()
        .map(|&idx| NodeRef::new(NodeKind::Fixture, idx))
        .collect();
    edges.extend(t.marks.iter().map(|&idx| NodeRef::new(NodeKind::Mark, idx)));
    edges
}
