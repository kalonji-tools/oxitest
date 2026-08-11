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
pub enum SignalKind {
    /// One or more fixtures are defined but never consumed by any test or
    /// other fixture.
    UnusedFixtures,
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
pub struct Signal {
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
pub fn detect_signals(graph: &InspectGraph) -> Vec<Signal> {
    if graph.fixtures.is_empty() {
        return Vec::new();
    }
    let mut signals = Vec::new();
    detect_unused_fixtures(graph, &mut signals);
    detect_broken_edges(graph, &mut signals);
    detect_high_fan_in(graph, &mut signals);
    detect_deep_chains(graph, &mut signals);
    detect_scope_mismatches(graph, &mut signals);
    signals
}

// ── Detectors ────────────────────────────────────────────────────────────────

/// Flag every fixture that has no consumers and is not marked `autouse`.
///
/// Only declaration-defined fixtures are checked — builtins and plugin fixtures
/// have runtime-wired consumers invisible to the graph.  Autouse fixtures are
/// always active, so lacking an explicit consumer is expected.
fn detect_unused_fixtures(graph: &InspectGraph, signals: &mut Vec<Signal>) {
    let affected: Vec<NodeRef> = graph
        .fixtures
        .iter()
        .enumerate()
        // `home` is non-empty for exactly the fixtures a user declared, across
        // all three of ADR-0009 Rule 5's homes. It replaces a filename test
        // that asked `ends_with("conftest.py")` — unsatisfiable after #1720,
        // so the signal could never fire — and which a rename would have
        // narrowed to `__fixtures__.py`, silently skipping declarations in an
        // `__init__.py` or inline in a test module (#1722).
        .filter(|(_, f)| f.consumers.is_empty() && !f.autouse && !f.home.is_empty())
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
const fn detect_deep_chains(graph: &InspectGraph, signals: &mut Vec<Signal>) {
    // No fixture→fixture dep edges yet — nothing to traverse.
    let _ = (graph, signals);
}

/// Placeholder for scope-mismatch detection.
///
/// Requires scope-aware edge traversal across fixture dependency chains.
/// Reserved for a future implementation pass.
const fn detect_scope_mismatches(graph: &InspectGraph, signals: &mut Vec<Signal>) {
    // Scope-aware traversal not yet implemented.
    let _ = (graph, signals);
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::inspect::graph::{
        BrokenEdge, InspectGraph, NodeKind, NodeRef,
        nodes::{DeclarationNode, FixtureNode, TestNode},
    };

    // ── Helpers ───────────────────────────────────────────────────────────────

    fn make_fixture(name: &str, autouse: bool, consumers: Vec<NodeRef>) -> FixtureNode {
        FixtureNode {
            lifetime: "function".to_string(),
            anchor: String::new(),
            home: "fixtures-file".to_string(),
            name: name.to_string(),
            binding_type: String::new(),
            scope: "each".to_string(),
            autouse,
            source: "__fixtures__.py".to_string(),
            is_async: false,
            description: String::new(),
            consumers,
            declaration_idx: Some(0), // declaration-defined fixture
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

    #[test]
    fn unused_fixture_detected_in_all_three_declaration_homes() {
        // Arrange — one orphan per home. A filename filter sees only the first.
        let mut graph = InspectGraph::default();
        graph.declarations.push(DeclarationNode {
            anchor: "tests".to_string(),
            home: "fixtures-file".to_string(),
            path: "tests/__fixtures__.py".to_string(),
            fixtures: vec![0, 1, 2],
        });
        for (name, home, source) in [
            ("orphan_a", "fixtures-file", "tests/__fixtures__.py"),
            ("orphan_b", "package-init", "tests/__init__.py"),
            ("orphan_c", "inline", "tests/test_thing.py"),
        ] {
            let mut fixture = make_fixture(name, false, vec![]);
            fixture.home = home.to_string();
            fixture.source = source.to_string();
            graph.fixtures.push(fixture);
        }

        // Act
        let signals = detect_signals(&graph);
        let unused: Vec<_> = signals
            .iter()
            .filter(|s| s.kind == SignalKind::UnusedFixtures)
            .collect();

        // Assert
        assert_eq!(
            unused[0].affected.len(),
            3,
            "all three of ADR-0009 Rule 5's declaration homes must be checked for \
             orphans; the predicate this replaced tested the source filename, so it \
             saw __fixtures__.py and silently skipped an __init__.py declaration and \
             an inline one (#1722)"
        );
    }

    #[test]
    fn ambient_fixture_is_never_reported_unused() {
        // Arrange — a builtin has no consumers in the graph because its
        // consumers are wired at runtime, not statically.
        let mut graph = InspectGraph::default();
        let mut builtin = make_fixture("_TempDirFixture", false, vec![]);
        builtin.home = String::new();
        builtin.source = "<builtin>".to_string();
        builtin.declaration_idx = None;
        graph.fixtures.push(builtin);

        // Act
        let signals = detect_signals(&graph);

        // Assert
        assert!(
            !signals.iter().any(|s| s.kind == SignalKind::UnusedFixtures),
            "an ambient fixture has runtime-wired consumers the graph cannot see, so \
             reporting it as unused would be a false positive on every project"
        );
    }

    // ── Test: unused fixture is detected ─────────────────────────────────────

    #[test]
    fn unused_fixture_detected() {
        let mut graph = InspectGraph::default();
        // Declaration node so declaration_idx=Some(0) is valid.
        graph.declarations.push(DeclarationNode {
            anchor: String::new(),
            home: "fixtures-file".to_string(),
            path: "__fixtures__.py".to_string(),
            fixtures: vec![],
        });
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

    // ── Test: builtin/plugin fixtures not flagged as unused ─────────────────

    #[test]
    fn builtin_fixture_not_flagged() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            lifetime: String::new(),
            anchor: String::new(),
            home: String::new(),
            name: "_TempDir".to_string(),
            binding_type: String::new(),
            scope: "each".to_string(),
            autouse: false,
            source: "<builtin>".to_string(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            declaration_idx: None,
            plugin_idx: None,
        });
        graph.fixtures.push(FixtureNode {
            lifetime: String::new(),
            anchor: String::new(),
            home: String::new(),
            name: "cache".to_string(),
            binding_type: String::new(),
            scope: "each".to_string(),
            autouse: false,
            source: "<plugin:cache>".to_string(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            declaration_idx: None,
            plugin_idx: None,
        });

        let signals = detect_signals(&graph);
        let unused: Vec<_> = signals
            .iter()
            .filter(|s| s.kind == SignalKind::UnusedFixtures)
            .collect();

        assert!(
            unused.is_empty(),
            "builtin and plugin fixtures must not be flagged as unused"
        );
    }

    // ── Test: broken edges produce a BrokenEdges signal ──────────────────────

    #[test]
    fn broken_edges_detected() {
        let mut graph = InspectGraph::default();
        // Declaration node so declaration_idx=Some(0) is valid.
        graph.declarations.push(DeclarationNode {
            anchor: String::new(),
            home: "fixtures-file".to_string(),
            path: "__fixtures__.py".to_string(),
            fixtures: vec![],
        });
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
}
