//! Node type definitions for the inspect graph.
//!
//! Each struct represents one of the five node kinds that make up the
//! inspect graph.  Fields store domain data; edge fields store indices
//! into the typed vectors held by [`super::InspectGraph`].

// ── FixtureNode ──────────────────────────────────────────────────────────────

/// A fixture definition (declaration or plugin-provided).
#[derive(Debug, Clone)]
pub struct FixtureNode {
    pub name: String,
    pub binding_type: String,
    /// The caching tier (`each`, `module`, `package`, `process`, `session`).
    /// Prefer [`Self::lifetime`] when showing this to a user — `scope` is the
    /// vocabulary the cache reads, not the one the declaration wrote.
    pub scope: String,
    /// The tier the declaration actually wrote: `function`, `module`, `package`
    /// or `process`. Empty for ambient fixtures, which declare none — `session`
    /// is a `Scope` no `Lifetime` maps to, so builtins can only report `scope`.
    pub lifetime: String,
    /// Anchor package path, rootdir-relative. Empty for ambient fixtures.
    pub anchor: String,
    /// Which declaration home declared this: `fixtures-file`, `package-init`
    /// or `inline`. **Empty means ambient** — a builtin, framework or plugin
    /// fixture — and that emptiness is what stops a declaration node being
    /// built for it (#1722).
    pub home: String,
    pub autouse: bool,
    pub source: String,
    pub is_async: bool,
    pub description: String,
    // Edges
    /// Node references for tests and fixtures that consume this fixture.
    pub consumers: Vec<super::NodeRef>,
    /// Index into `InspectGraph::declarations` if this fixture lives in a declaration file.
    pub declaration_idx: Option<usize>,
    /// Index into `InspectGraph::plugins` if this fixture is plugin-provided.
    pub plugin_idx: Option<usize>,
}

// ── TestNode ─────────────────────────────────────────────────────────────────

/// A test function (possibly parametrized).
#[derive(Debug, Clone)]
pub struct TestNode {
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
pub struct MarkNode {
    pub name: String,
    /// Indices into `InspectGraph::tests` — tests that carry this mark.
    pub used_by: Vec<usize>,
}

// ── DeclarationNode ─────────────────────────────────────────────────────────────

/// A fixture declaration file — one of the three homes ADR-0009 Rule 5 allows:
/// `__fixtures__.py`, `__init__.py`, or inline in a test module.
#[derive(Debug, Clone)]
pub struct DeclarationNode {
    pub path: String,
    /// The anchor these fixtures are scoped to, rootdir-relative — the B1
    /// boundary of ADR-0009 Rule 3. A directory for a `__fixtures__.py` or an
    /// `__init__.py`; for an inline declaration it is the test module itself.
    pub anchor: String,
    /// Which of the three homes this is: `fixtures-file`, `package-init` or
    /// `inline`. Never empty — an ambient fixture builds no declaration node.
    pub home: String,
    /// Indices into `InspectGraph::fixtures` — fixtures defined in this declaration.
    pub fixtures: Vec<usize>,
}

// ── PluginNode ───────────────────────────────────────────────────────────────

/// A registered plugin.
#[derive(Debug, Clone)]
pub struct PluginNode {
    pub name: String,
    pub protocols: Vec<String>,
    /// Indices into `InspectGraph::fixtures` — fixtures this plugin provides.
    pub fixtures: Vec<usize>,
}
