//! Node type definitions for the inspect graph.
//!
//! Each struct represents one of the six node kinds that make up the
//! inspect graph.  Fields store domain data; edge fields store indices
//! into the typed vectors held by [`super::InspectGraph`].
//!
//! Many fields are not yet read by production code — they will be consumed
//! by navigation (#1116), detail views (#1117), and search (#1118).
//! Suppress dead-code warnings at the module level until those features land.
#![allow(dead_code)]

// ── FixtureNode ──────────────────────────────────────────────────────────────

/// A fixture definition (conftest or plugin-provided).
#[derive(Debug, Clone)]
pub(crate) struct FixtureNode {
    pub name: String,
    pub binding_type: String,
    pub scope: String,
    pub autouse: bool,
    pub source: String,
    pub is_async: bool,
    pub description: String,
    // Edges
    /// Node references for tests and fixtures that consume this fixture.
    pub consumers: Vec<super::NodeRef>,
    /// Index into `InspectGraph::conftests` if this fixture lives in a conftest.
    pub conftest_idx: Option<usize>,
    /// Index into `InspectGraph::plugins` if this fixture is plugin-provided.
    pub plugin_idx: Option<usize>,
}

// ── TestNode ─────────────────────────────────────────────────────────────────

/// A test function (possibly parametrized).
#[derive(Debug, Clone)]
pub(crate) struct TestNode {
    pub node_id: String,
    pub is_async: bool,
    pub param_id: Option<String>,
    pub param_count: usize,
    /// Indices into `InspectGraph::tests` — sibling variants sharing the
    /// same base function name in a parametrize group.
    pub variants: Vec<usize>,
    // Edges
    /// Indices into `InspectGraph::fixtures`.
    pub fixture_deps: Vec<usize>,
    /// Indices into `InspectGraph::marks`.
    pub marks: Vec<usize>,
}

// ── MarkNode ─────────────────────────────────────────────────────────────────

/// A marker name (e.g. `slow`, `integration`).
#[derive(Debug, Clone)]
pub(crate) struct MarkNode {
    pub name: String,
    /// Indices into `InspectGraph::tests` — tests that carry this mark.
    pub used_by: Vec<usize>,
}

// ── ConftestNode ─────────────────────────────────────────────────────────────

/// A `conftest.py` file.
#[derive(Debug, Clone)]
pub(crate) struct ConftestNode {
    pub path: String,
    /// Indices into `InspectGraph::fixtures` — fixtures defined in this conftest.
    pub fixtures: Vec<usize>,
    /// Indices into `InspectGraph::helpers` — helpers defined in this conftest.
    pub helpers: Vec<usize>,
}

// ── PluginNode ───────────────────────────────────────────────────────────────

/// A registered plugin.
#[derive(Debug, Clone)]
pub(crate) struct PluginNode {
    pub name: String,
    pub protocols: Vec<String>,
    /// Indices into `InspectGraph::fixtures` — fixtures this plugin provides.
    pub fixtures: Vec<usize>,
}

// ── HelperNode ───────────────────────────────────────────────────────────────

/// A registered helper callable (conftest or plugin-provided).
#[derive(Debug, Clone)]
pub(crate) struct HelperNode {
    pub name: String,
    pub signature: String,
    pub docstring: Option<String>,
    pub source: String,
    pub namespace: String,
    /// Index into `InspectGraph::conftests` (if conftest-provided).
    pub conftest_idx: Option<usize>,
    /// Index into `InspectGraph::plugins` (if plugin-provided).
    pub plugin_idx: Option<usize>,
}
