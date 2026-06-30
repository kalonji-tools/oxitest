//! Graph-derived diagnostics for the inspect overview.
//!
//! Signals are lightweight diagnostic hints derived from the structure of the
//! [`InspectGraph`].  They surface actionable patterns — unused fixtures,
//! broken dependency edges, high fan-in nodes — without requiring a full
//! analysis pass.
//!
//! Call [`detect_signals`] once after the graph is fully built.  The
//! returned [`Signal`] list is consumed by the overview panel (Task 2) and
//! rendered in the TUI (Task 3).

use super::graph::{BrokenEdge, InspectGraph, NodeKind, NodeRef};

// ── SignalKind ────────────────────────────────────────────────────────────────

/// Categorises a detected graph anomaly or pattern.
#[derive(Debug, Clone, PartialEq, Eq)]
#[allow(dead_code)] // DeepChains and ScopeMismatches reserved for future passes
pub(crate) enum SignalKind {
    /// One or more fixtures are defined but never consumed by any test or
    /// other fixture.
    UnusedFixtures,
    /// One or more helper functions live in a conftest whose fixture count is
    /// zero, suggesting nothing in the test suite imports from that conftest.
    UnusedHelpers,
    /// The graph contains edges that could not be resolved during construction
    /// (e.g. a fixture dependency name that matches no known fixture).
    BrokenEdges,
    /// A fixture is consumed by more than half of all tests, making it a
    /// high-coupling hotspot.
    HighFanIn,
    /// A fixture dependency chain is unusually deep.
    ///
    /// Detection requires fixture→fixture dependency edges which are not yet
    /// captured by the builder; this variant is reserved for a future pass.
    DeepChains,
    /// A fixture's scope is wider than that of a fixture that depends on it,
    /// which can cause unexpected sharing.
    ///
    /// Detection requires scope-aware edge traversal which is not yet
    /// implemented; this variant is reserved for a future pass.
    ScopeMismatches,
}

// ── Signal ───────────────────────────────────────────────────────────────────

/// A single diagnostic hint derived from the inspect graph.
#[derive(Debug, Clone)]
pub(crate) struct Signal {
    /// The category of the anomaly.
    #[allow(dead_code)] // consumed once signal detail view renders kind
    pub kind: SignalKind,
    /// Human-readable summary shown in the overview panel.
    pub message: String,
    /// The specific nodes implicated by this signal.
    pub affected: Vec<NodeRef>,
}

// ── Public entry point ────────────────────────────────────────────────────────

/// Analyse `graph` and return all detected signals.
///
/// Returns an empty `Vec` immediately when the graph has no fixtures —
/// there is nothing meaningful to diagnose before Phase 2 data arrives.
///
/// Detectors run in a fixed order so signals appear consistently in the
/// overview panel regardless of graph construction order.
pub(crate) fn detect_signals(graph: &InspectGraph) -> Vec<Signal> {
    if graph.fixtures.is_empty() {
        return Vec::new();
    }
    let mut signals = Vec::new();
    detect_unused_fixtures(graph, &mut signals);
    detect_unused_helpers(graph, &mut signals);
    detect_broken_edges(graph, &mut signals);
    detect_high_fan_in(graph, &mut signals);
    detect_deep_chains(graph, &mut signals);
    detect_scope_mismatches(graph, &mut signals);
    signals
}

// ── Detectors ────────────────────────────────────────────────────────────────

/// Flag every fixture that has no consumers and is not marked `autouse`.
///
/// Autouse fixtures are always active, so lacking an explicit consumer is
/// expected and should not be reported.
fn detect_unused_fixtures(graph: &InspectGraph, signals: &mut Vec<Signal>) {
    let affected: Vec<NodeRef> = graph
        .fixtures
        .iter()
        .enumerate()
        .filter(|(_, f)| f.consumers.is_empty() && !f.autouse)
        .map(|(i, _)| NodeRef {
            kind: NodeKind::Fixture,
            index: i,
        })
        .collect();

    if !affected.is_empty() {
        let count = affected.len();
        signals.push(Signal {
            kind: SignalKind::UnusedFixtures,
            message: format!(
                "{count} fixture{s} defined but never consumed",
                s = if count == 1 { "" } else { "s" }
            ),
            affected,
        });
    }
}

/// Flag helper functions whose parent conftest defines no fixtures.
///
/// A conftest with zero fixtures is unlikely to be imported by test modules,
/// which means its helper functions are probably dead code.  This is a
/// heuristic — a conftest can also be used for its side-effects (e.g.
/// `pytest_configure` hooks), so this signal should be treated as advisory.
fn detect_unused_helpers(graph: &InspectGraph, signals: &mut Vec<Signal>) {
    let affected: Vec<NodeRef> = graph
        .helpers
        .iter()
        .enumerate()
        .filter(|(_, h)| {
            graph
                .conftests
                .get(h.conftest_idx)
                .map_or(false, |c| c.fixtures.is_empty())
        })
        .map(|(i, _)| NodeRef {
            kind: NodeKind::Helper,
            index: i,
        })
        .collect();

    if !affected.is_empty() {
        let count = affected.len();
        signals.push(Signal {
            kind: SignalKind::UnusedHelpers,
            message: format!(
                "{count} helper{s} in conftest{s2} with no fixtures",
                s = if count == 1 { "" } else { "s" },
                s2 = if count == 1 { "" } else { "s" },
            ),
            affected,
        });
    }
}

/// Report edges that the builder could not resolve.
///
/// The builder already collects `BrokenEdge` records during `resolve_edges`;
/// this detector simply promotes them into [`Signal`]s so they surface in the
/// overview panel.
fn detect_broken_edges(graph: &InspectGraph, signals: &mut Vec<Signal>) {
    if graph.broken_edges.is_empty() {
        return;
    }

    let affected: Vec<NodeRef> = graph
        .broken_edges
        .iter()
        .map(|BrokenEdge { from, .. }| from.clone())
        .collect();

    let count = affected.len();
    signals.push(Signal {
        kind: SignalKind::BrokenEdges,
        message: format!(
            "{count} unresolved fixture reference{s}",
            s = if count == 1 { "" } else { "s" }
        ),
        affected,
    });
}

/// Flag fixtures consumed by more than half of all tests.
///
/// A fixture with very high fan-in is a coupling hotspot: a change to it
/// affects a large fraction of the test suite.  The threshold is
/// `tests.len() / 2` (integer division), so a fixture must be consumed by
/// *more than* half of all tests to be flagged.
///
/// No signal is emitted when there are fewer than 2 tests (the threshold
/// would be 0, flagging everything).
fn detect_high_fan_in(graph: &InspectGraph, signals: &mut Vec<Signal>) {
    if graph.tests.len() < 2 {
        return;
    }
    let threshold = graph.tests.len() / 2;

    let affected: Vec<NodeRef> = graph
        .fixtures
        .iter()
        .enumerate()
        .filter(|(_, f)| f.consumers.len() > threshold)
        .map(|(i, _)| NodeRef {
            kind: NodeKind::Fixture,
            index: i,
        })
        .collect();

    if !affected.is_empty() {
        let count = affected.len();
        signals.push(Signal {
            kind: SignalKind::HighFanIn,
            message: format!(
                "{count} fixture{s} consumed by >50% of tests",
                s = if count == 1 { "" } else { "s" }
            ),
            affected,
        });
    }
}

/// Placeholder for deep-chain detection.
///
/// Requires fixture→fixture dependency edges which are not yet captured by
/// the graph builder.  Reserved for a future implementation pass.
fn detect_deep_chains(graph: &InspectGraph, signals: &mut Vec<Signal>) {
    // No fixture→fixture dep edges yet — nothing to traverse.
    let _ = (graph, signals);
}

/// Placeholder for scope-mismatch detection.
///
/// Requires scope-aware edge traversal across fixture dependency chains.
/// Reserved for a future implementation pass.
fn detect_scope_mismatches(graph: &InspectGraph, signals: &mut Vec<Signal>) {
    // Scope-aware traversal not yet implemented.
    let _ = (graph, signals);
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::inspect::graph::{
        BrokenEdge, InspectGraph, NodeKind, NodeRef,
        nodes::{ConftestNode, FixtureNode, HelperNode, TestNode},
    };

    // ── Helpers ───────────────────────────────────────────────────────────────

    fn make_fixture(name: &str, autouse: bool, consumers: Vec<NodeRef>) -> FixtureNode {
        FixtureNode {
            name: name.to_string(),
            binding_type: String::new(),
            scope: "function".to_string(),
            autouse,
            source: String::new(),
            is_async: false,
            description: String::new(),
            consumers,
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

    // ── Test: empty graph returns no signals ──────────────────────────────────

    #[test]
    fn detect_signals_empty_graph_returns_empty() {
        let graph = InspectGraph::default();
        let signals = detect_signals(&graph);
        assert!(
            signals.is_empty(),
            "empty graph should produce no signals — nothing to diagnose before Phase 2 data"
        );
    }

    // ── Test: unused fixture is detected ─────────────────────────────────────

    #[test]
    fn unused_fixture_detected() {
        let mut graph = InspectGraph::default();
        // Fixture with no consumers and autouse=false.
        graph
            .fixtures
            .push(make_fixture("orphan_db", false, vec![]));

        let signals = detect_signals(&graph);
        let unused: Vec<_> = signals
            .iter()
            .filter(|s| s.kind == SignalKind::UnusedFixtures)
            .collect();

        assert_eq!(
            unused.len(),
            1,
            "exactly one UnusedFixtures signal should be emitted for a non-autouse fixture with no consumers"
        );
        assert_eq!(
            unused[0].affected,
            vec![NodeRef {
                kind: NodeKind::Fixture,
                index: 0
            }],
            "the affected NodeRef should point to the orphan fixture at index 0"
        );
    }

    // ── Test: autouse fixture with no consumers is NOT flagged ────────────────

    #[test]
    fn autouse_fixture_not_flagged() {
        let mut graph = InspectGraph::default();
        // autouse=true — should never be reported as unused.
        graph
            .fixtures
            .push(make_fixture("auto_setup", true, vec![]));

        let signals = detect_signals(&graph);
        let unused: Vec<_> = signals
            .iter()
            .filter(|s| s.kind == SignalKind::UnusedFixtures)
            .collect();

        assert!(
            unused.is_empty(),
            "autouse fixture with no explicit consumers must not be flagged as unused"
        );
    }

    // ── Test: broken edges produce a BrokenEdges signal ──────────────────────

    #[test]
    fn broken_edges_detected() {
        let mut graph = InspectGraph::default();
        // Need at least one fixture so detect_signals doesn't return early.
        graph
            .fixtures
            .push(make_fixture("real_fixture", false, vec![]));

        let from = NodeRef {
            kind: NodeKind::Test,
            index: 0,
        };
        graph.broken_edges.push(BrokenEdge {
            from: from.clone(),
            qualifier: "missing_fixture".to_string(),
            binding_type: "fixture".to_string(),
        });

        let signals = detect_signals(&graph);
        let broken: Vec<_> = signals
            .iter()
            .filter(|s| s.kind == SignalKind::BrokenEdges)
            .collect();

        assert_eq!(
            broken.len(),
            1,
            "one BrokenEdges signal should be emitted when graph.broken_edges is non-empty"
        );
        assert_eq!(
            broken[0].affected,
            vec![from],
            "the affected NodeRef should be the 'from' node of the broken edge"
        );
    }

    // ── Test: high fan-in fixture is detected ─────────────────────────────────

    #[test]
    fn high_fan_in_detected() {
        let mut graph = InspectGraph::default();

        // Three tests.
        graph.tests.push(make_test("test_a.py::test_one"));
        graph.tests.push(make_test("test_a.py::test_two"));
        graph.tests.push(make_test("test_a.py::test_three"));

        // Fixture consumed by all 3 tests — exceeds threshold of 3/2 = 1.
        let consumers = vec![
            NodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            NodeRef {
                kind: NodeKind::Test,
                index: 1,
            },
            NodeRef {
                kind: NodeKind::Test,
                index: 2,
            },
        ];
        graph
            .fixtures
            .push(make_fixture("shared_db", false, consumers));

        let signals = detect_signals(&graph);
        let high_fan: Vec<_> = signals
            .iter()
            .filter(|s| s.kind == SignalKind::HighFanIn)
            .collect();

        assert_eq!(
            high_fan.len(),
            1,
            "one HighFanIn signal should be emitted for a fixture consumed by all 3 tests (>50%)"
        );
        assert_eq!(
            high_fan[0].affected,
            vec![NodeRef {
                kind: NodeKind::Fixture,
                index: 0
            }],
            "the affected NodeRef should point to the high-fan-in fixture at index 0"
        );
    }

    // ── Test: fixture below threshold is NOT flagged ──────────────────────────

    #[test]
    fn fixture_below_fan_in_threshold_not_flagged() {
        let mut graph = InspectGraph::default();

        // Four tests — threshold = 4/2 = 2. A fixture with exactly 2 consumers
        // is NOT > 2, so it must not be flagged.
        for i in 0..4 {
            graph.tests.push(make_test(&format!("test_a.py::test_{i}")));
        }

        let consumers = vec![
            NodeRef {
                kind: NodeKind::Test,
                index: 0,
            },
            NodeRef {
                kind: NodeKind::Test,
                index: 1,
            },
        ];
        graph
            .fixtures
            .push(make_fixture("moderate_fixture", false, consumers));

        let signals = detect_signals(&graph);
        let high_fan: Vec<_> = signals
            .iter()
            .filter(|s| s.kind == SignalKind::HighFanIn)
            .collect();

        assert!(
            high_fan.is_empty(),
            "fixture consumed by exactly 50% of tests should not exceed the >50% threshold"
        );
    }

    // ── Test: unused helper in fixture-less conftest is detected ──────────────

    #[test]
    fn unused_helper_in_empty_conftest_detected() {
        let mut graph = InspectGraph::default();

        // A conftest with no fixtures.
        graph.conftests.push(ConftestNode {
            path: "tests/conftest.py".to_string(),
            fixtures: vec![],
            helpers: vec![0],
        });

        // A helper pointing to that conftest.
        graph.helpers.push(HelperNode {
            name: "make_thing".to_string(),
            signature: "make_thing()".to_string(),
            docstring: None,
            source: "tests/conftest.py".to_string(),
            conftest_idx: 0,
        });

        // Need at least one fixture for detect_signals to proceed.
        graph
            .fixtures
            .push(make_fixture("some_fixture", false, vec![]));

        let signals = detect_signals(&graph);
        let unused_helpers: Vec<_> = signals
            .iter()
            .filter(|s| s.kind == SignalKind::UnusedHelpers)
            .collect();

        assert_eq!(
            unused_helpers.len(),
            1,
            "one UnusedHelpers signal should be emitted for a helper in a fixture-less conftest"
        );
        assert_eq!(
            unused_helpers[0].affected,
            vec![NodeRef {
                kind: NodeKind::Helper,
                index: 0
            }],
            "the affected NodeRef should point to the helper at index 0"
        );
    }
}
