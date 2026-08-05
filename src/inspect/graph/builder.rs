//! Graph construction from [`QueryEntry`] data.
//!
//! [`GraphBuilder`] accumulates nodes via `add_*_entries` methods, then
//! [`resolve_edges`](GraphBuilder::resolve_edges) wires cross-references, populates inverse edges,
//! groups parametrized tests, and records broken edges for unresolved
//! fixture dependencies.

use std::collections::HashMap;

use ahash::{AHashMap, AHashSet};

use crate::query::resource::QueryEntry;

use super::InspectGraph;
use super::nodes::{ConftestNode, FixtureNode, MarkNode, PluginNode, TestNode};

// ── GraphBuilder ─────────────────────────────────────────────────────────────

/// Incrementally builds an [`InspectGraph`] from flat [`QueryEntry`] slices.
pub struct GraphBuilder {
    graph: InspectGraph,
    // Lookup tables for deduplication during construction.
    fixture_by_name: HashMap<String, usize>,
    test_by_node_id: HashMap<String, usize>,
    mark_by_name: HashMap<String, usize>,
    conftest_by_path: HashMap<String, usize>,
    plugin_by_name: HashMap<String, usize>,
    // Side channel: mark names per test index (populated during add_test_entries,
    // consumed during resolve_edges).
    test_mark_names: HashMap<usize, Vec<String>>,
}

impl GraphBuilder {
    pub(crate) fn new() -> Self {
        Self {
            graph: InspectGraph::default(),
            fixture_by_name: HashMap::new(),
            test_by_node_id: HashMap::new(),
            mark_by_name: HashMap::new(),
            conftest_by_path: HashMap::new(),
            plugin_by_name: HashMap::new(),
            test_mark_names: HashMap::new(),
        }
    }

    /// Reconstruct a builder from an existing graph, repopulating lookup tables.
    ///
    /// Used by progressive loading (#1119) to merge phase-2 data (fixtures,
    /// plugins) into a graph that already contains phase-1 data (tests,
    /// marks).
    pub(crate) fn from_graph(graph: InspectGraph) -> Self {
        let fixture_by_name = graph
            .fixtures
            .iter()
            .enumerate()
            .map(|(i, f)| (f.name.clone(), i))
            .collect();
        let test_by_node_id = graph
            .tests
            .iter()
            .enumerate()
            .map(|(i, t)| (t.node_id.clone(), i))
            .collect();
        let mark_by_name = graph
            .marks
            .iter()
            .enumerate()
            .map(|(i, m)| (m.name.clone(), i))
            .collect();
        let conftest_by_path = graph
            .conftests
            .iter()
            .enumerate()
            .map(|(i, c)| (c.path.clone(), i))
            .collect();
        let plugin_by_name = graph
            .plugins
            .iter()
            .enumerate()
            .map(|(i, p)| (p.name.clone(), i))
            .collect();

        Self {
            graph,
            fixture_by_name,
            test_by_node_id,
            mark_by_name,
            conftest_by_path,
            plugin_by_name,
            test_mark_names: HashMap::new(),
        }
    }

    // ── add_* methods ────────────────────────────────────────────────────

    /// Add test nodes from instant-tier AST extraction.
    ///
    /// Expected entry fields: `name` (`node_id`), `source`, `mark`, `async`.
    pub(crate) fn add_test_entries(&mut self, entries: &[QueryEntry]) {
        for entry in entries {
            let node_id = entry.get("name").unwrap_or_default().to_string();
            if self.test_by_node_id.contains_key(&node_id) {
                continue;
            }
            let is_async = entry.get("async").is_some_and(|v| v == "true");

            // Store mark names in side channel for resolve_edges.
            let mark_names: Vec<String> = entry
                .get("mark")
                .unwrap_or_default()
                .split(',')
                .filter(|s| !s.is_empty())
                .map(|s| s.to_string())
                .collect();

            let idx = self.graph.tests.len();
            if !mark_names.is_empty() {
                self.test_mark_names.insert(idx, mark_names);
            }

            self.graph.tests.push(TestNode {
                node_id: node_id.clone(),
                is_async,
                param_id: None,
                param_count: 0,
                variants: vec![],
                fixture_deps: vec![],
                marks: vec![],
            });
            self.test_by_node_id.insert(node_id, idx);
        }
    }

    /// Add mark nodes from instant-tier AST extraction.
    ///
    /// Expected entry fields: `name`, `used_in`.
    pub(crate) fn add_mark_entries(&mut self, entries: &[QueryEntry]) {
        for entry in entries {
            let name = entry.get("name").unwrap_or_default().to_string();
            if self.mark_by_name.contains_key(&name) {
                continue;
            }
            let idx = self.graph.marks.len();
            self.graph.marks.push(MarkNode {
                name: name.clone(),
                used_by: vec![],
            });
            self.mark_by_name.insert(name, idx);
        }
    }

    /// Add fixture nodes from Python-tier collection.
    ///
    /// Expected entry fields: `name`, `source`, `shared` (= binding type),
    /// `autouse`, `async`, `description`, `scope`, `type`.
    pub(crate) fn add_fixture_entries(&mut self, entries: &[QueryEntry]) {
        for entry in entries {
            let name = entry.get("name").unwrap_or_default().to_string();
            if self.fixture_by_name.contains_key(&name) {
                continue;
            }
            let source = entry.get("source").unwrap_or_default().to_string();
            let binding_type = entry.get("type").unwrap_or("fixture").to_string();
            let scope = entry.get("scope").unwrap_or("function").to_string();
            let autouse = entry.get("autouse").is_some_and(|v| v == "true");
            let is_async = entry.get("async").is_some_and(|v| v == "true");
            let description = entry.get("description").unwrap_or_default().to_string();

            let idx = self.graph.fixtures.len();
            self.graph.fixtures.push(FixtureNode {
                name: name.clone(),
                binding_type,
                scope,
                autouse,
                source,
                is_async,
                description,
                consumers: vec![],
                conftest_idx: None,
                plugin_idx: None,
            });
            self.fixture_by_name.insert(name, idx);
        }
    }

    /// Add plugin nodes from Python-tier collection.
    ///
    /// Expected entry fields: `name`, `protocol`.
    pub(crate) fn add_plugin_entries(&mut self, entries: &[QueryEntry]) {
        for entry in entries {
            let name = entry.get("name").unwrap_or_default().to_string();
            if self.plugin_by_name.contains_key(&name) {
                continue;
            }
            let protocols: Vec<String> = entry
                .get("protocol")
                .unwrap_or_default()
                .split(',')
                .filter(|s| !s.is_empty())
                .map(|s| s.to_string())
                .collect();

            let idx = self.graph.plugins.len();
            self.graph.plugins.push(PluginNode {
                name: name.clone(),
                protocols,
                fixtures: vec![],
            });
            self.plugin_by_name.insert(name, idx);
        }
    }

    /// Wire test→fixture consumer edges from Python-tier fixture dependency data.
    ///
    /// Expected entry fields: `test_node_id`, `fixture_names` (comma-separated).
    /// For each entry, looks up the test by `node_id` and each fixture by name,
    /// then adds forward (test→fixture) and inverse (fixture→test) edges.
    /// Entries referencing unknown tests or fixtures are silently skipped.
    ///
    /// `rootdir` is stripped from absolute `test_node_id` values so they match
    /// the relative keys stored in `test_by_node_id` (phase-1 relativizes paths
    /// after graph build; phase-2 Python data carries absolute paths).
    pub(crate) fn add_fixture_dep_entries(&mut self, entries: &[QueryEntry], rootdir: &str) {
        let prefix = if rootdir.is_empty() {
            String::new()
        } else if rootdir.ends_with('/') {
            rootdir.to_string()
        } else {
            format!("{rootdir}/")
        };

        // Pre-seed seen-sets from existing edges so progressive loading
        // (from_graph → add_fixture_dep_entries) does not re-add edges
        // that were already wired in a previous build phase.
        let mut seen_test_deps: AHashMap<usize, AHashSet<usize>> = AHashMap::new();
        for (test_idx, test) in self.graph.tests.iter().enumerate() {
            if !test.fixture_deps.is_empty() {
                seen_test_deps.insert(test_idx, test.fixture_deps.iter().copied().collect());
            }
        }
        let mut seen_fix_consumers: AHashMap<usize, AHashSet<super::NodeRef>> = AHashMap::new();
        for (fix_idx, fix) in self.graph.fixtures.iter().enumerate() {
            if !fix.consumers.is_empty() {
                seen_fix_consumers.insert(fix_idx, fix.consumers.iter().cloned().collect());
            }
        }

        for entry in entries {
            let raw_id = entry.get("test_node_id").unwrap_or_default();
            let test_node_id = if prefix.is_empty() {
                raw_id
            } else {
                raw_id.strip_prefix(&prefix).unwrap_or(raw_id)
            };
            let fixture_names = entry.get("fixture_names").unwrap_or_default();

            let Some(&test_idx) = self.test_by_node_id.get(test_node_id) else {
                continue;
            };

            for name in fixture_names.split(',') {
                let name = name.trim();
                if name.is_empty() {
                    continue;
                }
                let Some(&fix_idx) = self.fixture_by_name.get(name) else {
                    continue;
                };

                // Forward: test -> fixture (O(1) HashSet lookup instead of O(n) Vec::contains)
                if seen_test_deps.entry(test_idx).or_default().insert(fix_idx) {
                    self.graph.tests[test_idx].fixture_deps.push(fix_idx);
                }
                // Inverse: fixture -> test (consumer)
                let consumer_ref = super::NodeRef::new(super::NodeKind::Test, test_idx);
                if seen_fix_consumers
                    .entry(fix_idx)
                    .or_default()
                    .insert(consumer_ref.clone())
                {
                    self.graph.fixtures[fix_idx].consumers.push(consumer_ref);
                }
            }
        }
    }

    // ── Edge resolution ──────────────────────────────────────────────────

    /// Wire up cross-references between nodes.
    ///
    /// This must be called after all `add_*_entries` methods.  It resolves:
    ///
    /// 1. **Test -> Mark** edges from test entry `mark` fields
    /// 2. **Fixture -> Conftest** membership edges (by source path)
    /// 3. **Fixture -> Plugin** edges (by `<plugin:name>` source prefix)
    /// 4. **Parametrize grouping** (strip `[param_id]` from `node_id`)
    /// 5. **Broken edges** for unresolved fixture references
    /// 6. **Inverse edges** (`consumers`, `used_by`, `conftest.fixtures`)
    pub(crate) fn resolve_edges(&mut self) {
        self.resolve_test_to_mark_edges();
        self.resolve_fixture_to_conftest_edges();
        self.resolve_fixture_to_plugin_edges();
        self.resolve_parametrize_grouping();
    }

    /// Consume the builder and return the finished graph.
    pub(crate) fn build(self) -> InspectGraph {
        self.graph
    }

    // ── Private helpers ──────────────────────────────────────────────────

    /// Ensure a conftest node exists for the given path, returning its index.
    fn ensure_conftest(&mut self, path: &str) -> usize {
        if let Some(&idx) = self.conftest_by_path.get(path) {
            return idx;
        }
        let idx = self.graph.conftests.len();
        self.graph.conftests.push(ConftestNode {
            path: path.to_string(),
            fixtures: vec![],
        });
        self.conftest_by_path.insert(path.to_string(), idx);
        idx
    }

    /// Ensure a mark node exists for the given name, returning its index.
    fn ensure_mark(&mut self, name: &str) -> usize {
        if let Some(&idx) = self.mark_by_name.get(name) {
            return idx;
        }
        let idx = self.graph.marks.len();
        self.graph.marks.push(MarkNode {
            name: name.to_string(),
            used_by: vec![],
        });
        self.mark_by_name.insert(name.to_string(), idx);
        idx
    }

    /// Wire test -> mark edges using the mark data stored in `test_mark_names`.
    fn resolve_test_to_mark_edges(&mut self) {
        let mark_map = std::mem::take(&mut self.test_mark_names);

        // Pre-seed seen-sets from existing edges (progressive loading support).
        let mut seen_test_marks: AHashMap<usize, AHashSet<usize>> = AHashMap::new();
        for (test_idx, test) in self.graph.tests.iter().enumerate() {
            if !test.marks.is_empty() {
                seen_test_marks.insert(test_idx, test.marks.iter().copied().collect());
            }
        }
        let mut seen_mark_users: AHashMap<usize, AHashSet<usize>> = AHashMap::new();
        for (mark_idx, mark) in self.graph.marks.iter().enumerate() {
            if !mark.used_by.is_empty() {
                seen_mark_users.insert(mark_idx, mark.used_by.iter().copied().collect());
            }
        }

        for (test_idx, mark_names) in mark_map {
            for name in mark_names {
                let mark_idx = self.ensure_mark(&name);
                // Forward: test -> mark
                if seen_test_marks
                    .entry(test_idx)
                    .or_default()
                    .insert(mark_idx)
                {
                    self.graph.tests[test_idx].marks.push(mark_idx);
                }
                // Inverse: mark -> test
                if seen_mark_users
                    .entry(mark_idx)
                    .or_default()
                    .insert(test_idx)
                {
                    self.graph.marks[mark_idx].used_by.push(test_idx);
                }
            }
        }
    }

    fn resolve_fixture_to_conftest_edges(&mut self) {
        // Pre-seed seen-set from existing edges (progressive loading support).
        let mut seen_conftest_fixtures: AHashMap<usize, AHashSet<usize>> = AHashMap::new();
        for (ct_idx, ct) in self.graph.conftests.iter().enumerate() {
            if !ct.fixtures.is_empty() {
                seen_conftest_fixtures.insert(ct_idx, ct.fixtures.iter().copied().collect());
            }
        }

        for fix_idx in 0..self.graph.fixtures.len() {
            let source = self.graph.fixtures[fix_idx].source.clone();
            if source.is_empty() || source.starts_with("<plugin:") {
                continue;
            }
            let conftest_idx = self.ensure_conftest(&source);
            self.graph.fixtures[fix_idx].conftest_idx = Some(conftest_idx);
            // Inverse: conftest -> fixture
            if seen_conftest_fixtures
                .entry(conftest_idx)
                .or_default()
                .insert(fix_idx)
            {
                self.graph.conftests[conftest_idx].fixtures.push(fix_idx);
            }
        }
    }

    fn resolve_fixture_to_plugin_edges(&mut self) {
        // Pre-seed seen-set from existing edges (progressive loading support).
        let mut seen_plugin_fixtures: AHashMap<usize, AHashSet<usize>> = AHashMap::new();
        for (p_idx, p) in self.graph.plugins.iter().enumerate() {
            if !p.fixtures.is_empty() {
                seen_plugin_fixtures.insert(p_idx, p.fixtures.iter().copied().collect());
            }
        }

        for fix_idx in 0..self.graph.fixtures.len() {
            let source = self.graph.fixtures[fix_idx].source.clone();
            if let Some(plugin_name) = source
                .strip_prefix("<plugin:")
                .and_then(|s| s.strip_suffix('>'))
                && let Some(&plugin_idx) = self.plugin_by_name.get(plugin_name)
            {
                self.graph.fixtures[fix_idx].plugin_idx = Some(plugin_idx);
                // Inverse: plugin -> fixture
                if seen_plugin_fixtures
                    .entry(plugin_idx)
                    .or_default()
                    .insert(fix_idx)
                {
                    self.graph.plugins[plugin_idx].fixtures.push(fix_idx);
                }
            }
        }
    }

    fn resolve_parametrize_grouping(&mut self) {
        // Group tests by base name (strip [param_id] suffix).
        let mut groups: HashMap<String, Vec<usize>> = HashMap::new();

        for (idx, test) in self.graph.tests.iter_mut().enumerate() {
            if let Some(bracket_pos) = test.node_id.rfind('[') {
                let base = test.node_id[..bracket_pos].to_string();
                let param_id = test.node_id[bracket_pos + 1..]
                    .trim_end_matches(']')
                    .to_string();
                test.param_id = Some(param_id);
                groups.entry(base).or_default().push(idx);
            }
        }

        // For groups with more than one variant, set param_count and variants.
        for indices in groups.values() {
            if indices.len() > 1 {
                let count = indices.len();
                for &idx in indices {
                    self.graph.tests[idx].param_count = count;
                    self.graph.tests[idx].variants =
                        indices.iter().copied().filter(|&i| i != idx).collect();
                }
            }
        }
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    /// Helper to create a `QueryEntry` from key-value pairs.
    fn entry(pairs: &[(&str, &str)]) -> QueryEntry {
        let fields = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        QueryEntry { fields }
    }

    // ── Empty graph ──────────────────────────────────────────────────────

    #[test]
    fn build_empty_graph() {
        let builder = GraphBuilder::new();
        let graph = builder.build();
        assert!(
            graph.is_empty(),
            "builder with no entries should produce an empty graph"
        );
    }

    // ── add_test_entries ─────────────────────────────────────────────────

    #[test]
    fn add_test_entries_creates_test_nodes() {
        let mut builder = GraphBuilder::new();
        builder.add_test_entries(&[
            entry(&[
                ("name", "tests/test_foo.py::test_bar"),
                ("source", "tests/test_foo.py"),
                ("async", "false"),
                ("mark", ""),
            ]),
            entry(&[
                ("name", "tests/test_foo.py::test_baz"),
                ("source", "tests/test_foo.py"),
                ("async", "true"),
                ("mark", "slow"),
            ]),
        ]);
        let graph = builder.build();
        assert_eq!(
            graph.tests.len(),
            2,
            "two test entries should produce two test nodes"
        );
        assert_eq!(
            graph.tests[0].node_id, "tests/test_foo.py::test_bar",
            "first test node_id should match entry name"
        );
        assert!(!graph.tests[0].is_async, "first test should not be async");
        assert!(graph.tests[1].is_async, "second test should be async");
    }

    // ── add_fixture_entries ──────────────────────────────────────────────

    #[test]
    fn add_fixture_entries_creates_fixture_nodes() {
        let mut builder = GraphBuilder::new();
        builder.add_fixture_entries(&[entry(&[
            ("name", "db"),
            ("source", "tests/conftest.py"),
            ("type", "fixture"),
            ("scope", "session"),
            ("autouse", "false"),
            ("async", "false"),
            ("description", "Database fixture"),
        ])]);
        let graph = builder.build();
        assert_eq!(
            graph.fixtures.len(),
            1,
            "one fixture entry should produce one fixture node"
        );
        assert_eq!(
            graph.fixtures[0].name, "db",
            "fixture name should match entry"
        );
        assert_eq!(
            graph.fixtures[0].scope, "session",
            "fixture scope should match entry"
        );
        assert_eq!(
            graph.fixtures[0].description, "Database fixture",
            "fixture description should match entry"
        );
    }

    // ── add_mark_entries ─────────────────────────────────────────────────

    #[test]
    fn add_mark_entries_creates_mark_nodes() {
        let mut builder = GraphBuilder::new();
        builder.add_mark_entries(&[
            entry(&[("name", "slow"), ("used_in", "tests/test_a.py")]),
            entry(&[("name", "integration"), ("used_in", "")]),
        ]);
        let graph = builder.build();
        assert_eq!(
            graph.marks.len(),
            2,
            "two mark entries should produce two mark nodes"
        );
        assert_eq!(
            graph.marks[0].name, "slow",
            "first mark name should match entry"
        );
        assert_eq!(
            graph.marks[1].name, "integration",
            "second mark name should match entry"
        );
    }

    // ── add_plugin_entries ───────────────────────────────────────────────

    #[test]
    fn add_plugin_entries_creates_plugin_nodes() {
        let mut builder = GraphBuilder::new();
        builder.add_plugin_entries(&[entry(&[
            ("name", "capture"),
            ("protocol", "CollectorProvider,ReporterProvider"),
        ])]);
        let graph = builder.build();
        assert_eq!(
            graph.plugins.len(),
            1,
            "one plugin entry should produce one plugin node"
        );
        assert_eq!(
            graph.plugins[0].name, "capture",
            "plugin name should match entry"
        );
        assert_eq!(
            graph.plugins[0].protocols,
            vec!["CollectorProvider", "ReporterProvider"],
            "plugin protocols should be split from comma-separated entry"
        );
    }

    // ── Edge resolution ──────────────────────────────────────────────────

    #[test]
    fn resolve_edges_wires_fixture_to_conftest() {
        let mut builder = GraphBuilder::new();
        builder.add_fixture_entries(&[entry(&[
            ("name", "db"),
            ("source", "tests/conftest.py"),
            ("type", "fixture"),
            ("scope", "function"),
            ("autouse", "false"),
            ("async", "false"),
            ("description", ""),
        ])]);
        builder.resolve_edges();
        let graph = builder.build();

        assert!(
            graph.fixtures[0].conftest_idx.is_some(),
            "fixture from conftest should have conftest_idx set"
        );
        let conftest_idx = graph.fixtures[0].conftest_idx.unwrap();
        assert_eq!(
            graph.conftests[conftest_idx].path, "tests/conftest.py",
            "conftest node should have matching path"
        );
        assert!(
            graph.conftests[conftest_idx].fixtures.contains(&0),
            "conftest should reference the fixture in its fixtures list"
        );
    }

    #[test]
    fn resolve_edges_wires_fixture_to_plugin() {
        let mut builder = GraphBuilder::new();
        builder.add_plugin_entries(&[entry(&[("name", "capture"), ("protocol", "")])]);
        builder.add_fixture_entries(&[entry(&[
            ("name", "std_capture"),
            ("source", "<plugin:capture>"),
            ("type", "fixture"),
            ("scope", "function"),
            ("autouse", "false"),
            ("async", "false"),
            ("description", ""),
        ])]);
        builder.resolve_edges();
        let graph = builder.build();

        assert_eq!(
            graph.fixtures[0].plugin_idx,
            Some(0),
            "fixture from plugin should have plugin_idx set"
        );
        assert!(
            graph.plugins[0].fixtures.contains(&0),
            "plugin should reference the fixture in its fixtures list"
        );
    }

    #[test]
    fn resolve_edges_parametrize_grouping() {
        let mut builder = GraphBuilder::new();
        builder.add_test_entries(&[
            entry(&[
                ("name", "test_foo.py::test_add[1]"),
                ("source", "test_foo.py"),
                ("async", "false"),
                ("mark", ""),
            ]),
            entry(&[
                ("name", "test_foo.py::test_add[2]"),
                ("source", "test_foo.py"),
                ("async", "false"),
                ("mark", ""),
            ]),
            entry(&[
                ("name", "test_foo.py::test_add[3]"),
                ("source", "test_foo.py"),
                ("async", "false"),
                ("mark", ""),
            ]),
            entry(&[
                ("name", "test_foo.py::test_solo"),
                ("source", "test_foo.py"),
                ("async", "false"),
                ("mark", ""),
            ]),
        ]);
        builder.resolve_edges();
        let graph = builder.build();

        // Parametrized tests should be grouped.
        assert_eq!(
            graph.tests[0].param_count, 3,
            "parametrized test with 3 variants should have param_count = 3"
        );
        assert_eq!(
            graph.tests[0].param_id,
            Some("1".to_string()),
            "first variant should have param_id '1'"
        );
        assert_eq!(
            graph.tests[0].variants.len(),
            2,
            "first variant should list 2 sibling variants (excluding self)"
        );

        // Non-parametrized test should have no group data.
        assert_eq!(
            graph.tests[3].param_count, 0,
            "non-parametrized test should have param_count = 0"
        );
        assert!(
            graph.tests[3].param_id.is_none(),
            "non-parametrized test should have no param_id"
        );
        assert!(
            graph.tests[3].variants.is_empty(),
            "non-parametrized test should have no variants"
        );
    }

    #[test]
    fn resolve_edges_wires_test_to_mark() {
        let mut builder = GraphBuilder::new();
        builder.add_test_entries(&[
            entry(&[
                ("name", "test_foo.py::test_bar"),
                ("source", "test_foo.py"),
                ("async", "false"),
                ("mark", "slow,integration"),
            ]),
            entry(&[
                ("name", "test_foo.py::test_baz"),
                ("source", "test_foo.py"),
                ("async", "false"),
                ("mark", "slow"),
            ]),
        ]);
        builder.add_mark_entries(&[
            entry(&[("name", "slow"), ("used_in", "test_foo.py")]),
            entry(&[("name", "integration"), ("used_in", "test_foo.py")]),
        ]);
        builder.resolve_edges();
        let graph = builder.build();

        // test_bar should have 2 marks (slow, integration)
        assert_eq!(
            graph.tests[0].marks.len(),
            2,
            "test with 'slow,integration' mark field should have 2 mark edges"
        );
        // test_baz should have 1 mark (slow)
        assert_eq!(
            graph.tests[1].marks.len(),
            1,
            "test with 'slow' mark field should have 1 mark edge"
        );
        // Mark 'slow' should be used_by 2 tests
        let slow_idx = graph.tests[0].marks[0];
        assert_eq!(
            graph.marks[slow_idx].used_by.len(),
            2,
            "mark 'slow' should be used_by both tests"
        );
    }

    #[test]
    fn resolve_edges_creates_mark_if_not_already_added() {
        let mut builder = GraphBuilder::new();
        // Add test with mark but don't add_mark_entries for it.
        builder.add_test_entries(&[entry(&[
            ("name", "test_foo.py::test_bar"),
            ("source", "test_foo.py"),
            ("async", "false"),
            ("mark", "custom_mark"),
        ])]);
        builder.resolve_edges();
        let graph = builder.build();

        assert_eq!(
            graph.marks.len(),
            1,
            "resolve_edges should auto-create mark nodes for marks referenced by tests"
        );
        assert_eq!(
            graph.marks[0].name, "custom_mark",
            "auto-created mark should have the correct name"
        );
        assert_eq!(
            graph.marks[0].used_by.len(),
            1,
            "auto-created mark should track its consumer test"
        );
    }

    // ── Deduplication ────────────────────────────────────────────────────

    #[test]
    fn deduplication_by_name() {
        let mut builder = GraphBuilder::new();
        let fx_entry = entry(&[
            ("name", "db"),
            ("source", "tests/conftest.py"),
            ("type", "fixture"),
            ("scope", "function"),
            ("autouse", "false"),
            ("async", "false"),
            ("description", ""),
        ]);
        builder.add_fixture_entries(&[fx_entry.clone(), fx_entry]);
        let graph = builder.build();
        assert_eq!(
            graph.fixtures.len(),
            1,
            "adding the same fixture twice should not create duplicates"
        );
    }

    #[test]
    fn deduplication_test_by_node_id() {
        let mut builder = GraphBuilder::new();
        let test_entry = entry(&[
            ("name", "test_foo.py::test_bar"),
            ("source", "test_foo.py"),
            ("async", "false"),
            ("mark", ""),
        ]);
        builder.add_test_entries(&[test_entry.clone(), test_entry]);
        let graph = builder.build();
        assert_eq!(
            graph.tests.len(),
            1,
            "adding the same test twice should not create duplicates"
        );
    }

    #[test]
    fn deduplication_mark_by_name() {
        let mut builder = GraphBuilder::new();
        let mark_entry = entry(&[("name", "slow"), ("used_in", "test_a.py")]);
        builder.add_mark_entries(&[mark_entry.clone(), mark_entry]);
        let graph = builder.build();
        assert_eq!(
            graph.marks.len(),
            1,
            "adding the same mark twice should not create duplicates"
        );
    }

    #[test]
    fn deduplication_plugin_by_name() {
        let mut builder = GraphBuilder::new();
        let plugin_entry = entry(&[("name", "capture"), ("protocol", "")]);
        builder.add_plugin_entries(&[plugin_entry.clone(), plugin_entry]);
        let graph = builder.build();
        assert_eq!(
            graph.plugins.len(),
            1,
            "adding the same plugin twice should not create duplicates"
        );
    }

    // ── Conftest auto-creation ───────────────────────────────────────────

    #[test]
    fn fixtures_from_same_conftest_share_conftest_node() {
        let mut builder = GraphBuilder::new();
        builder.add_fixture_entries(&[
            entry(&[
                ("name", "fixture_a"),
                ("source", "tests/conftest.py"),
                ("type", "fixture"),
                ("scope", "function"),
                ("autouse", "false"),
                ("async", "false"),
                ("description", ""),
            ]),
            entry(&[
                ("name", "fixture_b"),
                ("source", "tests/conftest.py"),
                ("type", "fixture"),
                ("scope", "function"),
                ("autouse", "false"),
                ("async", "false"),
                ("description", ""),
            ]),
        ]);
        builder.resolve_edges();
        let graph = builder.build();
        assert_eq!(
            graph.conftests.len(),
            1,
            "two fixtures from the same conftest path must resolve to one conftest \
             node — a duplicated node would split one file's fixtures across two \
             graph nodes and break navigation between them"
        );
        assert_eq!(
            graph.conftests[0].fixtures.len(),
            2,
            "the shared conftest node should list both fixtures, not just whichever \
             one happened to create the node first"
        );
    }

    #[test]
    fn fixture_plugin_source_does_not_create_conftest() {
        let mut builder = GraphBuilder::new();
        builder.add_fixture_entries(&[entry(&[
            ("name", "std_capture"),
            ("source", "<plugin:capture>"),
            ("type", "fixture"),
            ("scope", "function"),
            ("autouse", "false"),
            ("async", "false"),
            ("description", ""),
        ])]);
        builder.resolve_edges();
        let graph = builder.build();
        assert!(
            graph.fixtures[0].conftest_idx.is_none(),
            "plugin-sourced fixture should not have conftest_idx"
        );
    }

    // ── from_graph (progressive loading) ────────────────────────────────

    #[test]
    fn from_graph_preserves_existing_nodes() {
        let mut builder = GraphBuilder::new();
        builder.add_test_entries(&[entry(&[
            ("name", "tests/test_foo.py::test_bar"),
            ("source", "tests/test_foo.py"),
            ("async", "false"),
            ("mark", "slow"),
        ])]);
        builder.add_mark_entries(&[entry(&[("name", "slow"), ("used_in", "test_foo.py")])]);
        builder.resolve_edges();
        let graph = builder.build();

        // Reconstruct from graph and add fixtures.
        let mut builder2 = GraphBuilder::from_graph(graph);
        builder2.add_fixture_entries(&[entry(&[
            ("name", "db"),
            ("source", "tests/conftest.py"),
            ("type", "fixture"),
            ("scope", "function"),
            ("autouse", "false"),
            ("async", "false"),
            ("description", ""),
        ])]);
        builder2.resolve_edges();
        let merged = builder2.build();

        assert_eq!(
            merged.tests.len(),
            1,
            "from_graph should preserve existing test nodes"
        );
        assert_eq!(
            merged.marks.len(),
            1,
            "from_graph should preserve existing mark nodes"
        );
        assert_eq!(
            merged.fixtures.len(),
            1,
            "from_graph should allow adding new fixture nodes"
        );
    }

    #[test]
    fn from_graph_deduplicates_on_merge() {
        let mut builder = GraphBuilder::new();
        builder.add_test_entries(&[entry(&[
            ("name", "test_a.py::test_one"),
            ("source", "test_a.py"),
            ("async", "false"),
            ("mark", ""),
        ])]);
        builder.resolve_edges();
        let graph = builder.build();

        let mut builder2 = GraphBuilder::from_graph(graph);
        // Adding the same test again should be a no-op.
        builder2.add_test_entries(&[entry(&[
            ("name", "test_a.py::test_one"),
            ("source", "test_a.py"),
            ("async", "false"),
            ("mark", ""),
        ])]);
        let merged = builder2.build();

        assert_eq!(
            merged.tests.len(),
            1,
            "from_graph should deduplicate when re-adding existing nodes"
        );
    }

    #[test]
    fn from_graph_resolves_fixture_to_conftest_edges() {
        let mut builder = GraphBuilder::new();
        builder.add_fixture_entries(&[entry(&[
            ("name", "existing"),
            ("source", "tests/conftest.py"),
            ("type", "fixture"),
            ("scope", "function"),
            ("autouse", "false"),
            ("async", "false"),
            ("description", ""),
        ])]);
        builder.resolve_edges();
        let graph = builder.build();

        // Phase 2: add another fixture from the same conftest.
        let mut builder2 = GraphBuilder::from_graph(graph);
        builder2.add_fixture_entries(&[entry(&[
            ("name", "thing"),
            ("source", "tests/conftest.py"),
            ("type", "fixture"),
            ("scope", "function"),
            ("autouse", "false"),
            ("async", "false"),
            ("description", ""),
        ])]);
        builder2.resolve_edges();
        let merged = builder2.build();

        assert_eq!(
            merged.conftests.len(),
            1,
            "fixture from same conftest should reuse existing conftest node"
        );
        assert!(
            merged.fixtures[0].conftest_idx.is_some(),
            "fixture should be linked to the conftest node"
        );
        assert!(
            merged.conftests[0].fixtures.contains(&0),
            "conftest should reference the phase-1 fixture"
        );
        assert!(
            merged.conftests[0].fixtures.contains(&1),
            "conftest should also reference the phase-2 fixture added via from_graph"
        );
    }

    // ── Missing-field / required-field validation ─────────────────────

    #[test]
    fn test_entry_missing_name_produces_empty_node_id() {
        let mut builder = GraphBuilder::new();
        let entry = QueryEntry {
            fields: [("source".to_string(), "test.py".to_string())]
                .into_iter()
                .collect(),
        };
        builder.add_test_entries(&[entry]);
        let graph = builder.build();
        assert_eq!(
            graph.tests.len(),
            1,
            "builder should create a test node even when 'name' is missing — \
             progressive loading may produce partial entries"
        );
        assert_eq!(
            graph.tests[0].node_id, "",
            "missing 'name' field should default to empty string — \
             unwrap_or_default produces \"\" for absent keys"
        );
    }

    #[test]
    fn fixture_entry_missing_name_produces_empty_name() {
        let mut builder = GraphBuilder::new();
        let entry = QueryEntry {
            fields: [("scope".to_string(), "function".to_string())]
                .into_iter()
                .collect(),
        };
        builder.add_fixture_entries(&[entry]);
        let graph = builder.build();
        assert_eq!(
            graph.fixtures.len(),
            1,
            "builder should create a fixture node even when 'name' is missing — \
             progressive loading may produce partial entries"
        );
        assert_eq!(
            graph.fixtures[0].name, "",
            "missing 'name' field should default to empty string — \
             unwrap_or_default produces \"\" for absent keys"
        );
    }

    #[test]
    fn two_nameless_test_entries_deduplicate_on_empty_key() {
        let mut builder = GraphBuilder::new();
        let entry_a = QueryEntry {
            fields: [("source".to_string(), "a.py".to_string())]
                .into_iter()
                .collect(),
        };
        let entry_b = QueryEntry {
            fields: [("source".to_string(), "b.py".to_string())]
                .into_iter()
                .collect(),
        };
        builder.add_test_entries(&[entry_a, entry_b]);
        let graph = builder.build();
        assert_eq!(
            graph.tests.len(),
            1,
            "two entries with missing 'name' both default to \"\" — \
             deduplication on the empty key should keep only one node"
        );
    }

    // ── add_fixture_dep_entries ────────────────────────────────────────────

    #[test]
    fn fixture_dep_entries_wire_test_to_fixture_edges() {
        let mut builder = GraphBuilder::new();
        builder.add_test_entries(&[entry(&[
            ("name", "test_a.py::test_one"),
            ("source", "test_a.py"),
            ("async", "false"),
            ("mark", ""),
        ])]);
        builder.add_fixture_entries(&[entry(&[
            ("name", "db"),
            ("source", "conftest.py"),
            ("type", "fixture"),
            ("scope", "function"),
            ("autouse", "false"),
            ("async", "false"),
            ("description", ""),
        ])]);
        builder.add_fixture_dep_entries(
            &[entry(&[
                ("test_node_id", "test_a.py::test_one"),
                ("fixture_names", "db"),
            ])],
            "",
        );
        builder.resolve_edges();
        let graph = builder.build();

        assert_eq!(
            graph.tests[0].fixture_deps,
            vec![0],
            "test should have fixture dep pointing to fixture index 0 — \
             add_fixture_dep_entries must wire the forward edge"
        );
        assert_eq!(
            graph.fixtures[0].consumers.len(),
            1,
            "fixture should have exactly one consumer — \
             add_fixture_dep_entries must wire the inverse edge"
        );
        assert_eq!(
            graph.fixtures[0].consumers[0],
            super::super::NodeRef::new(super::super::NodeKind::Test, 0),
            "fixture consumer should reference test at index 0"
        );
    }

    #[test]
    fn fixture_dep_entries_multiple_fixtures_per_test() {
        let mut builder = GraphBuilder::new();
        builder.add_test_entries(&[entry(&[
            ("name", "test_a.py::test_multi"),
            ("source", "test_a.py"),
            ("async", "false"),
            ("mark", ""),
        ])]);
        builder.add_fixture_entries(&[
            entry(&[
                ("name", "db"),
                ("source", "conftest.py"),
                ("type", "fixture"),
                ("scope", "function"),
                ("autouse", "false"),
                ("async", "false"),
                ("description", ""),
            ]),
            entry(&[
                ("name", "cache"),
                ("source", "conftest.py"),
                ("type", "fixture"),
                ("scope", "function"),
                ("autouse", "false"),
                ("async", "false"),
                ("description", ""),
            ]),
        ]);
        builder.add_fixture_dep_entries(
            &[entry(&[
                ("test_node_id", "test_a.py::test_multi"),
                ("fixture_names", "db,cache"),
            ])],
            "",
        );
        builder.resolve_edges();
        let graph = builder.build();

        assert_eq!(
            graph.tests[0].fixture_deps.len(),
            2,
            "test depending on two fixtures should have 2 fixture_deps — \
             comma-separated names must be split and resolved individually"
        );
        assert!(
            graph.fixtures[0].consumers.len() == 1 && graph.fixtures[1].consumers.len() == 1,
            "each fixture should have exactly one consumer — both fixtures are used by one test"
        );
    }

    #[test]
    fn fixture_dep_entries_unknown_test_silently_skipped() {
        let mut builder = GraphBuilder::new();
        builder.add_fixture_entries(&[entry(&[
            ("name", "db"),
            ("source", "conftest.py"),
            ("type", "fixture"),
            ("scope", "function"),
            ("autouse", "false"),
            ("async", "false"),
            ("description", ""),
        ])]);
        // No test with this node_id exists.
        builder.add_fixture_dep_entries(
            &[entry(&[
                ("test_node_id", "test_b.py::test_missing"),
                ("fixture_names", "db"),
            ])],
            "",
        );
        builder.resolve_edges();
        let graph = builder.build();

        assert!(
            graph.fixtures[0].consumers.is_empty(),
            "fixture should have no consumers when the referencing test does not exist — \
             unknown test_node_id entries must be silently skipped"
        );
    }

    #[test]
    fn fixture_dep_entries_unknown_fixture_silently_skipped() {
        let mut builder = GraphBuilder::new();
        builder.add_test_entries(&[entry(&[
            ("name", "test_a.py::test_one"),
            ("source", "test_a.py"),
            ("async", "false"),
            ("mark", ""),
        ])]);
        // No fixture named "nonexistent" exists.
        builder.add_fixture_dep_entries(
            &[entry(&[
                ("test_node_id", "test_a.py::test_one"),
                ("fixture_names", "nonexistent"),
            ])],
            "",
        );
        builder.resolve_edges();
        let graph = builder.build();

        assert!(
            graph.tests[0].fixture_deps.is_empty(),
            "test should have no fixture_deps when the named fixture does not exist — \
             unknown fixture names must be silently skipped"
        );
    }

    #[test]
    fn fixture_dep_entries_deduplicates_edges() {
        let mut builder = GraphBuilder::new();
        builder.add_test_entries(&[entry(&[
            ("name", "test_a.py::test_one"),
            ("source", "test_a.py"),
            ("async", "false"),
            ("mark", ""),
        ])]);
        builder.add_fixture_entries(&[entry(&[
            ("name", "db"),
            ("source", "conftest.py"),
            ("type", "fixture"),
            ("scope", "function"),
            ("autouse", "false"),
            ("async", "false"),
            ("description", ""),
        ])]);
        // Two entries referencing the same test→fixture pair.
        builder.add_fixture_dep_entries(
            &[
                entry(&[
                    ("test_node_id", "test_a.py::test_one"),
                    ("fixture_names", "db"),
                ]),
                entry(&[
                    ("test_node_id", "test_a.py::test_one"),
                    ("fixture_names", "db"),
                ]),
            ],
            "",
        );
        builder.resolve_edges();
        let graph = builder.build();

        assert_eq!(
            graph.tests[0].fixture_deps.len(),
            1,
            "duplicate fixture dep entries should not create duplicate edges — \
             the contains check must prevent double-wiring"
        );
        assert_eq!(
            graph.fixtures[0].consumers.len(),
            1,
            "duplicate consumer entries should not create duplicate inverse edges"
        );
    }

    #[test]
    fn fixture_dep_entries_deduplicates_across_repeated_calls() {
        let mut builder = GraphBuilder::new();
        builder.add_test_entries(&[entry(&[
            ("name", "test_a.py::test_one"),
            ("source", "test_a.py"),
            ("async", "false"),
            ("mark", ""),
        ])]);
        builder.add_fixture_entries(&[
            entry(&[
                ("name", "db"),
                ("source", "conftest.py"),
                ("type", "fixture"),
                ("scope", "function"),
                ("autouse", "false"),
                ("async", "false"),
                ("description", ""),
            ]),
            entry(&[
                ("name", "cache"),
                ("source", "conftest.py"),
                ("type", "fixture"),
                ("scope", "function"),
                ("autouse", "false"),
                ("async", "false"),
                ("description", ""),
            ]),
        ]);
        // First call: wire test→db and test→cache.
        builder.add_fixture_dep_entries(
            &[entry(&[
                ("test_node_id", "test_a.py::test_one"),
                ("fixture_names", "db,cache"),
            ])],
            "",
        );
        // Second call with overlapping entries: db again + cache again.
        builder.add_fixture_dep_entries(
            &[entry(&[
                ("test_node_id", "test_a.py::test_one"),
                ("fixture_names", "db,cache"),
            ])],
            "",
        );
        builder.resolve_edges();
        let graph = builder.build();

        assert_eq!(
            graph.tests[0].fixture_deps.len(),
            2,
            "calling add_fixture_dep_entries twice with the same edges should not \
             create duplicates — HashSet dedup must span across calls"
        );
        assert_eq!(
            graph.fixtures[0].consumers.len(),
            1,
            "db fixture should have exactly one consumer despite two add_fixture_dep_entries calls"
        );
        assert_eq!(
            graph.fixtures[1].consumers.len(),
            1,
            "cache fixture should have exactly one consumer despite two add_fixture_dep_entries calls"
        );
    }

    #[test]
    fn fixture_dep_entries_in_progressive_loading() {
        // Simulate phase-1 (tests) then phase-2 (fixtures + deps) via from_graph.
        let mut builder = GraphBuilder::new();
        builder.add_test_entries(&[
            entry(&[
                ("name", "test_a.py::test_one"),
                ("source", "test_a.py"),
                ("async", "false"),
                ("mark", ""),
            ]),
            entry(&[
                ("name", "test_a.py::test_two"),
                ("source", "test_a.py"),
                ("async", "false"),
                ("mark", ""),
            ]),
        ]);
        builder.resolve_edges();
        let graph = builder.build();

        // Phase 2: reconstruct builder and add fixtures + deps.
        let mut builder2 = GraphBuilder::from_graph(graph);
        builder2.add_fixture_entries(&[entry(&[
            ("name", "db"),
            ("source", "conftest.py"),
            ("type", "fixture"),
            ("scope", "function"),
            ("autouse", "false"),
            ("async", "false"),
            ("description", ""),
        ])]);
        builder2.add_fixture_dep_entries(
            &[
                entry(&[
                    ("test_node_id", "test_a.py::test_one"),
                    ("fixture_names", "db"),
                ]),
                entry(&[
                    ("test_node_id", "test_a.py::test_two"),
                    ("fixture_names", "db"),
                ]),
            ],
            "",
        );
        builder2.resolve_edges();
        let merged = builder2.build();

        assert_eq!(
            merged.tests[0].fixture_deps,
            vec![0],
            "first test should have fixture dep wired during phase-2 merge"
        );
        assert_eq!(
            merged.tests[1].fixture_deps,
            vec![0],
            "second test should have fixture dep wired during phase-2 merge"
        );
        assert_eq!(
            merged.fixtures[0].consumers.len(),
            2,
            "fixture consumed by both tests should have 2 consumers after phase-2 merge — \
             progressive loading must support wiring edges across rebuild boundaries"
        );
    }
}
