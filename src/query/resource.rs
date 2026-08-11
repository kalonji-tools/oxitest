//! Resource kinds and query entries for the unified query system.
//!
//! [`ResourceKind`] enumerates the resource types that can be queried
//! and defines which predicate names are valid for each.
//! [`QueryEntry`] is a flat field map representing one queryable resource.

use std::collections::HashMap;

// ── ResourceKind ──────────────────────────────────────────────────────────────

/// The kinds of resources that the query DSL can filter.
#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum ResourceKind {
    /// Test functions and parametrize cases.
    Tests,
    /// Fixture definitions.
    Fixtures,
    /// Registered marker names.
    Marks,
    /// Registered plugins.
    Plugins,
}

impl ResourceKind {
    /// Return the predicate names that are valid for this resource kind.
    pub(crate) const fn valid_predicates(&self) -> &'static [&'static str] {
        match self {
            Self::Tests => &["name", "source", "mark", "async", "uses"],
            Self::Fixtures => &["name", "source", "autouse", "async", "uses"],
            Self::Marks => &["name", "used_in"],
            Self::Plugins => &["name", "protocol"],
        }
    }

    /// Return the lowercase string name used in error messages.
    pub(crate) const fn as_str(&self) -> &'static str {
        match self {
            Self::Tests => "tests",
            Self::Fixtures => "fixtures",
            Self::Marks => "marks",
            Self::Plugins => "plugins",
        }
    }
}

// ── QueryEntry ────────────────────────────────────────────────────────────────

/// A flat key→value map representing one queryable resource.
///
/// Values for multi-valued fields (e.g. marks) are comma-separated strings
/// so the evaluator can split and check each value independently.
#[derive(Debug, Clone, PartialEq)]
pub struct QueryEntry {
    /// The field map.
    pub(crate) fields: HashMap<String, String>,
}

impl QueryEntry {
    /// Look up a field by name, returning `None` if it does not exist.
    pub(crate) fn get(&self, key: &str) -> Option<&str> {
        self.fields.get(key).map(String::as_str)
    }
}
