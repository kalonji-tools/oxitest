//! Overview section data for the cartographic landing screen.
//!
//! [`OverviewSections`] holds pre-sorted slices of "gravity" entries —
//! fixtures with the most consumers, marks with the most tests, conftests
//! with the most fixtures, and diagnostic signals — used to populate the
//! TUI overview panel.

use super::graph::{InspectGraph, NodeKind, NodeRef};
use super::signals::Signal;

// ── Entry types ──────────────────────────────────────────────────────────────

/// A fixture node ranked by how many tests/fixtures consume it.
pub(crate) struct GravityEntry {
    pub node_ref: NodeRef,
    pub name: String,
    pub consumer_count: usize,
}

/// A mark node ranked by how many tests carry it.
pub(crate) struct MarkEntry {
    pub node_ref: NodeRef,
    pub name: String,
    pub test_count: usize,
}

/// A conftest node ranked by how many fixtures it defines.
pub(crate) struct ConftestEntry {
    pub node_ref: NodeRef,
    pub path: String,
    pub fixture_count: usize,
    pub helper_count: usize,
}

// ── OverviewItem ─────────────────────────────────────────────────────────────

/// A single selectable item in the overview panel (flat cursor abstraction).
#[cfg(test)]
#[allow(dead_code)] // variants matched by pattern, fields not read directly
pub(crate) enum OverviewItem {
    Gravity(GravityEntry),
    Mark(MarkEntry),
    Conftest(ConftestEntry),
    Signal(Signal),
}

// ── OverviewSections ─────────────────────────────────────────────────────────

/// Pre-sorted overview data derived from an [`InspectGraph`].
///
/// Flat cursor layout (for `item_at` / `node_ref_at`):
///
/// ```text
/// [gravity[0], …, marks[0], …, conftests[0], …, signals[0], …]
/// ```
pub(crate) struct OverviewSections {
    pub gravity: Vec<GravityEntry>,
    pub marks: Vec<MarkEntry>,
    pub conftests: Vec<ConftestEntry>,
    pub signals: Vec<Signal>,
}

impl Default for OverviewSections {
    fn default() -> Self {
        Self {
            gravity: Vec::new(),
            marks: Vec::new(),
            conftests: Vec::new(),
            signals: Vec::new(),
        }
    }
}

impl OverviewSections {
    /// Build overview sections from a graph.
    ///
    /// - `gravity`: fixtures with >0 consumers, sorted descending by
    ///   `consumers.len()`.
    /// - `marks`: all marks, sorted descending by `used_by.len()`.
    /// - `conftests`: all conftests, sorted descending by `fixtures.len()`.
    pub(crate) fn from_graph(graph: &InspectGraph) -> Self {
        // ── Gravity (fixtures by consumer count) ─────────────────────────
        let mut gravity: Vec<GravityEntry> = graph
            .fixtures
            .iter()
            .enumerate()
            .filter(|(_, f)| !f.consumers.is_empty())
            .map(|(i, f)| GravityEntry {
                node_ref: NodeRef::new(NodeKind::Fixture, i),
                name: f.name.clone(),
                consumer_count: f.consumers.len(),
            })
            .collect();
        gravity.sort_by_key(|e| std::cmp::Reverse(e.consumer_count));

        // ── Marks (by test count) ─────────────────────────────────────────
        let mut marks: Vec<MarkEntry> = graph
            .marks
            .iter()
            .enumerate()
            .map(|(i, m)| MarkEntry {
                node_ref: NodeRef::new(NodeKind::Mark, i),
                name: m.name.clone(),
                test_count: m.used_by.len(),
            })
            .collect();
        marks.sort_by_key(|e| std::cmp::Reverse(e.test_count));

        // ── Conftests (by fixture count) ──────────────────────────────────
        let mut conftests: Vec<ConftestEntry> = graph
            .conftests
            .iter()
            .enumerate()
            .map(|(i, c)| ConftestEntry {
                node_ref: NodeRef::new(NodeKind::Conftest, i),
                path: c.path.clone(),
                fixture_count: c.fixtures.len(),
                helper_count: c.helpers.len(),
            })
            .collect();
        conftests.sort_by_key(|e| std::cmp::Reverse(e.fixture_count));

        let signals = super::signals::detect_signals(graph);

        Self {
            gravity,
            marks,
            conftests,
            signals,
        }
    }

    /// Total number of selectable items across all sections.
    pub(crate) fn item_count(&self) -> usize {
        self.gravity.len() + self.marks.len() + self.conftests.len() + self.signals.len()
    }

    /// Return the item at the given flat cursor `index`.
    ///
    /// Flat order: gravity entries, then mark entries, then conftest entries.
    /// Returns `None` if `index >= item_count()`.
    #[cfg(test)]
    pub(crate) fn item_at(&self, index: usize) -> Option<OverviewItem> {
        let g = self.gravity.len();
        let m = self.marks.len();
        let c = self.conftests.len();

        if index < g {
            let e = &self.gravity[index];
            Some(OverviewItem::Gravity(GravityEntry {
                node_ref: e.node_ref.clone(),
                name: e.name.clone(),
                consumer_count: e.consumer_count,
            }))
        } else if index < g + m {
            let e = &self.marks[index - g];
            Some(OverviewItem::Mark(MarkEntry {
                node_ref: e.node_ref.clone(),
                name: e.name.clone(),
                test_count: e.test_count,
            }))
        } else if index < g + m + c {
            let e = &self.conftests[index - g - m];
            Some(OverviewItem::Conftest(ConftestEntry {
                node_ref: e.node_ref.clone(),
                path: e.path.clone(),
                fixture_count: e.fixture_count,
                helper_count: e.helper_count,
            }))
        } else if index < g + m + c + self.signals.len() {
            let s = &self.signals[index - g - m - c];
            Some(OverviewItem::Signal(s.clone()))
        } else {
            None
        }
    }

    /// Return the [`NodeRef`] at the given flat cursor `index`.
    ///
    /// Indexes directly into the section vectors without cloning.
    /// Returns `None` if `index >= item_count()`.
    pub(crate) fn node_ref_at(&self, index: usize) -> Option<NodeRef> {
        let g = self.gravity.len();
        let m = self.marks.len();
        let c = self.conftests.len();
        if index < g {
            Some(self.gravity[index].node_ref.clone())
        } else if index < g + m {
            Some(self.marks[index - g].node_ref.clone())
        } else if index < g + m + c {
            Some(self.conftests[index - g - m].node_ref.clone())
        } else if index < g + m + c + self.signals.len() {
            self.signals[index - g - m - c].affected.first().cloned()
        } else {
            None
        }
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::inspect::graph::nodes::{ConftestNode, FixtureNode, HelperNode, MarkNode, TestNode};

    /// Build a fixture node with the given name and a pre-set consumer list.
    fn make_fixture(name: &str, consumers: Vec<NodeRef>) -> FixtureNode {
        FixtureNode {
            name: name.to_string(),
            binding_type: "fixture".to_string(),
            scope: "function".to_string(),
            autouse: false,
            source: "tests/conftest.py".to_string(),
            is_async: false,
            description: String::new(),
            consumers,
            conftest_idx: None,
            plugin_idx: None,
        }
    }

    /// Build a mark node with the given name and used_by list.
    fn make_mark(name: &str, used_by: Vec<usize>) -> MarkNode {
        MarkNode {
            name: name.to_string(),
            used_by,
        }
    }

    /// Build a minimal test node.
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

    /// Build a conftest node with fixture and helper index lists.
    fn make_conftest(path: &str, fixtures: Vec<usize>, helpers: Vec<usize>) -> ConftestNode {
        ConftestNode {
            path: path.to_string(),
            fixtures,
            helpers,
        }
    }

    /// Build a minimal helper node.
    fn make_helper(name: &str) -> HelperNode {
        HelperNode {
            name: name.to_string(),
            signature: format!("{name}()"),
            docstring: None,
            source: "tests/conftest.py".to_string(),
            conftest_idx: 0,
        }
    }

    // ── sections_from_graph ──────────────────────────────────────────────

    #[test]
    fn sections_from_graph_populates_all_three_sections() {
        let mut graph = InspectGraph::default();

        // Two fixtures: one with consumers, one without.
        graph.fixtures.push(make_fixture(
            "db",
            vec![NodeRef {
                kind: NodeKind::Test,
                index: 0,
            }],
        ));
        graph.fixtures.push(make_fixture("unused_fx", vec![]));

        // One test so the consumer NodeRef is meaningful.
        graph.tests.push(make_test("tests/test_db.py::test_query"));

        // One mark used by one test.
        graph.marks.push(make_mark("slow", vec![0]));

        // One conftest with one fixture and one helper.
        graph
            .conftests
            .push(make_conftest("tests/conftest.py", vec![0], vec![0]));
        graph.helpers.push(make_helper("make_db"));

        let sections = OverviewSections::from_graph(&graph);

        assert_eq!(
            sections.gravity.len(),
            1,
            "only fixtures with >0 consumers should appear in gravity"
        );
        assert_eq!(
            sections.gravity[0].name, "db",
            "gravity entry should be the fixture with consumers"
        );
        assert_eq!(
            sections.gravity[0].consumer_count, 1,
            "consumer_count should match the fixture's consumers length"
        );

        assert_eq!(
            sections.marks.len(),
            1,
            "all marks should appear in the marks section"
        );
        assert_eq!(
            sections.marks[0].name, "slow",
            "mark entry name should match"
        );
        assert_eq!(
            sections.marks[0].test_count, 1,
            "test_count should equal used_by.len()"
        );

        assert_eq!(
            sections.conftests.len(),
            1,
            "all conftests should appear in the conftests section"
        );
        assert_eq!(
            sections.conftests[0].path, "tests/conftest.py",
            "conftest path should match"
        );
        assert_eq!(
            sections.conftests[0].fixture_count, 1,
            "fixture_count should equal conftests.fixtures.len()"
        );
        assert_eq!(
            sections.conftests[0].helper_count, 1,
            "helper_count should equal conftests.helpers.len()"
        );
    }

    #[test]
    fn sections_from_empty_graph_are_empty() {
        let graph = InspectGraph::default();
        let sections = OverviewSections::from_graph(&graph);
        assert_eq!(
            sections.item_count(),
            0,
            "overview sections from an empty graph should have zero items"
        );
    }

    // ── flat_cursor_mapping ──────────────────────────────────────────────

    #[test]
    fn flat_cursor_mapping_covers_all_items() {
        let mut graph = InspectGraph::default();

        // Add two gravity fixtures (both with consumers).
        graph.fixtures.push(make_fixture(
            "fx_a",
            vec![NodeRef {
                kind: NodeKind::Test,
                index: 0,
            }],
        ));
        graph.fixtures.push(make_fixture(
            "fx_b",
            vec![
                NodeRef {
                    kind: NodeKind::Test,
                    index: 0,
                },
                NodeRef {
                    kind: NodeKind::Test,
                    index: 1,
                },
            ],
        ));
        graph.tests.push(make_test("tests/test_a.py::test_one"));
        graph.tests.push(make_test("tests/test_a.py::test_two"));

        // Add one mark.
        graph.marks.push(make_mark("integration", vec![0]));

        // Add one conftest.
        graph
            .conftests
            .push(make_conftest("tests/conftest.py", vec![0, 1], vec![]));

        let sections = OverviewSections::from_graph(&graph);
        let total = sections.item_count();
        let expected = sections.gravity.len()
            + sections.marks.len()
            + sections.conftests.len()
            + sections.signals.len();

        assert_eq!(
            total, expected,
            "item_count should equal gravity + marks + conftests + signals"
        );

        // Every index in [0, total) must return Some.
        for i in 0..total {
            assert!(
                sections.item_at(i).is_some(),
                "item_at({i}) should return Some for valid cursor index"
            );
            assert!(
                sections.node_ref_at(i).is_some(),
                "node_ref_at({i}) should return Some for valid cursor index"
            );
        }

        // Out-of-bounds must return None.
        assert!(
            sections.item_at(total).is_none(),
            "item_at(total) should return None — index is out of bounds"
        );
        assert!(
            sections.node_ref_at(total).is_none(),
            "node_ref_at(total) should return None — index is out of bounds"
        );
    }

    #[test]
    fn flat_cursor_mapping_item_kinds_are_correct() {
        let mut graph = InspectGraph::default();

        // One gravity fixture.
        graph.fixtures.push(make_fixture(
            "db",
            vec![NodeRef {
                kind: NodeKind::Test,
                index: 0,
            }],
        ));
        graph.tests.push(make_test("tests/test_db.py::test_q"));

        // One mark.
        graph.marks.push(make_mark("slow", vec![0]));

        // One conftest.
        graph
            .conftests
            .push(make_conftest("conftest.py", vec![0], vec![]));

        let sections = OverviewSections::from_graph(&graph);

        // Index 0 → Gravity
        assert!(
            matches!(sections.item_at(0), Some(OverviewItem::Gravity(_))),
            "index 0 should be a Gravity item"
        );
        // Index 1 → Mark
        assert!(
            matches!(sections.item_at(1), Some(OverviewItem::Mark(_))),
            "index 1 should be a Mark item"
        );
        // Index 2 → Conftest
        assert!(
            matches!(sections.item_at(2), Some(OverviewItem::Conftest(_))),
            "index 2 should be a Conftest item"
        );
    }

    #[test]
    fn node_ref_at_returns_correct_kind() {
        let mut graph = InspectGraph::default();

        graph.fixtures.push(make_fixture(
            "fx",
            vec![NodeRef {
                kind: NodeKind::Test,
                index: 0,
            }],
        ));
        graph.tests.push(make_test("tests/test.py::test_it"));
        graph.marks.push(make_mark("m", vec![0]));
        graph.conftests.push(make_conftest("c.py", vec![0], vec![]));

        let sections = OverviewSections::from_graph(&graph);

        assert_eq!(
            sections.node_ref_at(0).map(|r| r.kind),
            Some(NodeKind::Fixture),
            "node_ref_at(0) should be a Fixture ref (gravity)"
        );
        assert_eq!(
            sections.node_ref_at(1).map(|r| r.kind),
            Some(NodeKind::Mark),
            "node_ref_at(1) should be a Mark ref"
        );
        assert_eq!(
            sections.node_ref_at(2).map(|r| r.kind),
            Some(NodeKind::Conftest),
            "node_ref_at(2) should be a Conftest ref"
        );
    }

    // ── gravity_sorted_descending ────────────────────────────────────────

    #[test]
    fn gravity_sorted_descending_by_consumer_count() {
        let mut graph = InspectGraph::default();

        // Three fixtures with 1, 3, and 2 consumers respectively.
        let test_ref = |i| NodeRef {
            kind: NodeKind::Test,
            index: i,
        };
        graph.fixtures.push(make_fixture("low", vec![test_ref(0)])); // 1 consumer
        graph.fixtures.push(make_fixture(
            "high",
            vec![test_ref(0), test_ref(1), test_ref(2)],
        )); // 3 consumers
        graph
            .fixtures
            .push(make_fixture("mid", vec![test_ref(0), test_ref(1)])); // 2 consumers

        // Dummy tests so references are valid.
        for i in 0..3 {
            graph
                .tests
                .push(make_test(&format!("tests/t.py::test_{i}")));
        }

        let sections = OverviewSections::from_graph(&graph);

        assert_eq!(
            sections.gravity.len(),
            3,
            "all three fixtures have consumers and should appear in gravity"
        );
        assert_eq!(
            sections.gravity[0].name, "high",
            "fixture with 3 consumers should rank first"
        );
        assert_eq!(
            sections.gravity[0].consumer_count, 3,
            "top gravity entry should have consumer_count = 3"
        );
        assert_eq!(
            sections.gravity[1].name, "mid",
            "fixture with 2 consumers should rank second"
        );
        assert_eq!(
            sections.gravity[2].name, "low",
            "fixture with 1 consumer should rank third"
        );
    }

    #[test]
    fn gravity_excludes_fixtures_with_zero_consumers() {
        let mut graph = InspectGraph::default();

        graph.fixtures.push(make_fixture(
            "with_consumer",
            vec![NodeRef {
                kind: NodeKind::Test,
                index: 0,
            }],
        ));
        graph.fixtures.push(make_fixture("no_consumer", vec![]));
        graph.tests.push(make_test("tests/t.py::test_it"));

        let sections = OverviewSections::from_graph(&graph);

        assert_eq!(
            sections.gravity.len(),
            1,
            "fixtures with 0 consumers must be excluded from gravity"
        );
        assert_eq!(
            sections.gravity[0].name, "with_consumer",
            "only the fixture with consumers should appear"
        );
    }

    // ── signal_integration ──────────────────────────────────────────────

    #[test]
    fn signals_populated_from_graph_with_unused_fixture() {
        let mut graph = InspectGraph::default();
        // Unused fixture (no consumers, not autouse) triggers UnusedFixtures signal.
        graph.fixtures.push(FixtureNode {
            name: "orphan".to_string(),
            binding_type: String::new(),
            scope: "function".to_string(),
            autouse: false,
            source: String::new(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            conftest_idx: None,
            plugin_idx: None,
        });

        let sections = OverviewSections::from_graph(&graph);

        assert!(
            !sections.signals.is_empty(),
            "graph with an unused fixture should produce at least one signal"
        );
        assert!(
            sections.signals[0].message.contains("fixture"),
            "first signal message should mention fixtures — got: {}",
            sections.signals[0].message,
        );
    }

    #[test]
    fn signals_included_in_item_count() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "orphan".to_string(),
            binding_type: String::new(),
            scope: "function".to_string(),
            autouse: false,
            source: String::new(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            conftest_idx: None,
            plugin_idx: None,
        });

        let sections = OverviewSections::from_graph(&graph);
        let expected = sections.gravity.len()
            + sections.marks.len()
            + sections.conftests.len()
            + sections.signals.len();

        assert_eq!(
            sections.item_count(),
            expected,
            "item_count should include signals in the total — \
             gravity={}, marks={}, conftests={}, signals={}",
            sections.gravity.len(),
            sections.marks.len(),
            sections.conftests.len(),
            sections.signals.len(),
        );
    }

    #[test]
    fn item_at_returns_signal_variant_for_signal_index() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "orphan".to_string(),
            binding_type: String::new(),
            scope: "function".to_string(),
            autouse: false,
            source: String::new(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            conftest_idx: None,
            plugin_idx: None,
        });

        let sections = OverviewSections::from_graph(&graph);
        let signal_start = sections.gravity.len() + sections.marks.len() + sections.conftests.len();

        assert!(
            !sections.signals.is_empty(),
            "setup: graph must produce at least one signal for this test"
        );
        assert!(
            matches!(
                sections.item_at(signal_start),
                Some(OverviewItem::Signal(_))
            ),
            "item_at at the signal range should return OverviewItem::Signal"
        );
    }

    #[test]
    fn node_ref_at_returns_first_affected_for_signal() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(FixtureNode {
            name: "orphan".to_string(),
            binding_type: String::new(),
            scope: "function".to_string(),
            autouse: false,
            source: String::new(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            conftest_idx: None,
            plugin_idx: None,
        });

        let sections = OverviewSections::from_graph(&graph);
        let signal_start = sections.gravity.len() + sections.marks.len() + sections.conftests.len();

        let node_ref = sections.node_ref_at(signal_start);
        let expected = sections.signals[0].affected.first().cloned();

        assert_eq!(
            node_ref, expected,
            "node_ref_at for a signal should return its first affected NodeRef"
        );
    }

    #[test]
    fn empty_graph_has_no_signals() {
        let graph = InspectGraph::default();
        let sections = OverviewSections::from_graph(&graph);

        assert!(
            sections.signals.is_empty(),
            "empty graph should produce no signals — nothing to diagnose"
        );
    }
}
