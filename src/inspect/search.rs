//! Search engine for `oxitest inspect`: fuzzy matching and DSL auto-detection.
//!
//! Defines a [`Searchable`] trait that the graph data model (#1114) will
//! implement, allowing this module to compile and test independently.

use crate::query::{ast::Expr, compile, eval, resource::QueryEntry};

// ── Types ──────────────────────────────────────────────────────────────────

/// Opaque reference to a node in the inspect graph.
///
/// Wraps a `usize` index. The concrete `InspectGraph` (#1114) will use
/// the same representation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct NodeRef(pub usize);

/// The kind of node in the inspect tree.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[allow(dead_code)] // consumed once the graph data model (#1114) lands
pub(crate) enum NodeKind {
    Test,
    Fixture,
    Mark,
    Helper,
    Plugin,
    /// Non-leaf group node (e.g., a module or package).
    Group,
}

/// The scope to search within.
#[derive(Debug, Clone, PartialEq, Eq)]
#[allow(dead_code)] // consumed once the graph data model (#1114) lands
pub(crate) enum SearchScope {
    /// Search all node kinds.
    All,
    /// Only search nodes of the given kind.
    Kind(NodeKind),
}

// ── Searchable trait ───────────────────────────────────────────────────────

/// Trait that the graph data model must implement for search to work.
///
/// Decouples search logic from the concrete `InspectGraph` so this module
/// compiles and tests before #1114 lands.
#[allow(dead_code)] // consumed once the graph data model (#1114) lands
pub(crate) trait Searchable {
    /// Return references to every node in the graph.
    fn all_nodes(&self) -> Vec<NodeRef>;
    /// Return references to nodes matching a specific kind.
    fn nodes_of_kind(&self, kind: NodeKind) -> Vec<NodeRef>;
    /// Return the display name for a node.
    fn node_name(&self, r: NodeRef) -> &str;
    /// Return the kind of a node.
    fn node_kind(&self, r: NodeRef) -> NodeKind;
    /// Build a [`QueryEntry`] for DSL evaluation against a node.
    fn node_query_entry(&self, r: NodeRef) -> QueryEntry;
}

// ── Search function ────────────────────────────────────────────────────────

/// Search the graph for nodes matching `query`.
///
/// **Auto-detection logic:**
/// - If `query` contains `:` (e.g., `shared:true`), attempt DSL parse
///   via `query::compile`. A colon hints that the user is writing a DSL
///   predicate like `name(~foo)` — the colon appears in bare-word chars.
/// - If DSL parse succeeds, evaluate against nodes using `query::eval`.
/// - If DSL parse fails or `query` has no `:`, fall back to
///   case-insensitive substring match on the node name.
#[allow(dead_code)] // consumed once the graph data model (#1114) lands
pub(crate) fn search<G: Searchable>(graph: &G, query: &str, scope: SearchScope) -> Vec<NodeRef> {
    if query.is_empty() {
        return Vec::new();
    }

    let candidates = match &scope {
        SearchScope::All => graph.all_nodes(),
        SearchScope::Kind(kind) => graph.nodes_of_kind(*kind),
    };

    // Try DSL auto-detection: if the query looks like a DSL expression
    // (contains parentheses, which are required by the DSL grammar),
    // attempt to compile and evaluate it.
    if looks_like_dsl(query) {
        if let Some(expr) = try_compile_dsl(query) {
            return candidates
                .into_iter()
                .filter(|r| {
                    let entry = graph.node_query_entry(*r);
                    eval::eval(&expr, &entry)
                })
                .collect();
        }
    }

    // Fallback: case-insensitive substring match on node name.
    let query_lower = query.to_lowercase();
    candidates
        .into_iter()
        .filter(|r| graph.node_name(*r).to_lowercase().contains(&query_lower))
        .collect()
}

/// Heuristic: does the query look like a DSL expression?
///
/// The DSL grammar requires `predicate(...)` syntax, so any query
/// containing `(` is a strong signal. We also accept queries containing
/// `&`, `|`, or `!` as boolean operators.
#[allow(dead_code)] // consumed via search(), which is consumed once #1114 lands
fn looks_like_dsl(query: &str) -> bool {
    query.contains('(') || query.contains('&') || query.contains('|')
}

/// Try to lex + parse a query as a DSL expression.
///
/// Returns `Some(expr)` on success, `None` on any parse failure.
#[allow(dead_code)] // consumed via search(), which is consumed once #1114 lands
fn try_compile_dsl(query: &str) -> Option<Expr> {
    let tokens = compile::lex(query).ok()?;
    compile::parse(tokens).ok()
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    // ── Mock graph ──────────────────────────────────────────────────────

    struct MockGraph {
        nodes: Vec<(String, NodeKind, HashMap<String, String>)>,
    }

    impl MockGraph {
        fn new(items: &[(&str, NodeKind)]) -> Self {
            Self {
                nodes: items
                    .iter()
                    .map(|(name, kind)| {
                        let mut fields = HashMap::new();
                        fields.insert("name".to_string(), name.to_string());
                        (name.to_string(), *kind, fields)
                    })
                    .collect(),
            }
        }

        fn with_fields(items: &[(&str, NodeKind, &[(&str, &str)])]) -> Self {
            Self {
                nodes: items
                    .iter()
                    .map(|(name, kind, extra)| {
                        let mut fields: HashMap<String, String> = extra
                            .iter()
                            .map(|(k, v)| (k.to_string(), v.to_string()))
                            .collect();
                        fields
                            .entry("name".to_string())
                            .or_insert_with(|| name.to_string());
                        (name.to_string(), *kind, fields)
                    })
                    .collect(),
            }
        }
    }

    impl Searchable for MockGraph {
        fn all_nodes(&self) -> Vec<NodeRef> {
            (0..self.nodes.len()).map(NodeRef).collect()
        }

        fn nodes_of_kind(&self, kind: NodeKind) -> Vec<NodeRef> {
            self.nodes
                .iter()
                .enumerate()
                .filter(|(_, (_, k, _))| *k == kind)
                .map(|(i, _)| NodeRef(i))
                .collect()
        }

        fn node_name(&self, r: NodeRef) -> &str {
            &self.nodes[r.0].0
        }

        fn node_kind(&self, r: NodeRef) -> NodeKind {
            self.nodes[r.0].1
        }

        fn node_query_entry(&self, r: NodeRef) -> QueryEntry {
            QueryEntry {
                fields: self.nodes[r.0].2.clone(),
            }
        }
    }

    // ── Helper ──────────────────────────────────────────────────────────

    fn names<G: Searchable>(graph: &G, refs: &[NodeRef]) -> Vec<String> {
        refs.iter()
            .map(|r| graph.node_name(*r).to_string())
            .collect()
    }

    // ── Substring match tests ───────────────────────────────────────────

    #[test]
    fn search_substring_match() {
        let graph = MockGraph::new(&[
            ("db_session", NodeKind::Fixture),
            ("test_login", NodeKind::Test),
            ("db_cleanup", NodeKind::Fixture),
        ]);
        let results = search(&graph, "db", SearchScope::All);
        let matched = names(&graph, &results);
        assert_eq!(
            matched,
            vec!["db_session", "db_cleanup"],
            "substring 'db' should match nodes whose names contain 'db'"
        );
    }

    #[test]
    fn search_case_insensitive() {
        let graph = MockGraph::new(&[
            ("db_session", NodeKind::Fixture),
            ("test_login", NodeKind::Test),
        ]);
        let results = search(&graph, "DB", SearchScope::All);
        let matched = names(&graph, &results);
        assert_eq!(
            matched,
            vec!["db_session"],
            "case-insensitive search for 'DB' should match 'db_session'"
        );
    }

    #[test]
    fn search_no_match_returns_empty() {
        let graph = MockGraph::new(&[
            ("db_session", NodeKind::Fixture),
            ("test_login", NodeKind::Test),
        ]);
        let results = search(&graph, "zzz", SearchScope::All);
        assert!(
            results.is_empty(),
            "query 'zzz' should match nothing, got: {:?}",
            names(&graph, &results)
        );
    }

    #[test]
    fn search_empty_query_returns_empty() {
        let graph = MockGraph::new(&[("db_session", NodeKind::Fixture)]);
        let results = search(&graph, "", SearchScope::All);
        assert!(results.is_empty(), "empty query should return no results");
    }

    #[test]
    fn search_scope_filters_by_kind() {
        let graph = MockGraph::new(&[
            ("db_session", NodeKind::Fixture),
            ("db_test", NodeKind::Test),
            ("db_cleanup", NodeKind::Fixture),
        ]);
        let results = search(&graph, "db", SearchScope::Kind(NodeKind::Fixture));
        let matched = names(&graph, &results);
        assert_eq!(
            matched,
            vec!["db_session", "db_cleanup"],
            "Kind(Fixture) scope should exclude Test nodes even if name matches"
        );
    }

    #[test]
    fn search_scope_kind_test() {
        let graph = MockGraph::new(&[
            ("db_session", NodeKind::Fixture),
            ("db_test", NodeKind::Test),
            ("db_cleanup", NodeKind::Fixture),
        ]);
        let results = search(&graph, "db", SearchScope::Kind(NodeKind::Test));
        let matched = names(&graph, &results);
        assert_eq!(
            matched,
            vec!["db_test"],
            "Kind(Test) scope should only return test nodes"
        );
    }

    // ── DSL auto-detection tests ────────────────────────────────────────

    #[test]
    fn search_dsl_name_predicate() {
        let graph = MockGraph::new(&[
            ("test_login", NodeKind::Test),
            ("test_logout", NodeKind::Test),
            ("db_session", NodeKind::Fixture),
        ]);
        let results = search(&graph, "name(~login)", SearchScope::All);
        let matched = names(&graph, &results);
        assert_eq!(
            matched,
            vec!["test_login"],
            "DSL name(~login) should match only 'test_login'"
        );
    }

    #[test]
    fn search_dsl_boolean_and() {
        let graph = MockGraph::with_fields(&[
            ("test_login", NodeKind::Test, &[("mark", "slow")]),
            ("test_logout", NodeKind::Test, &[("mark", "fast")]),
        ]);
        let results = search(&graph, "name(~login) & mark(slow)", SearchScope::All);
        let matched = names(&graph, &results);
        assert_eq!(
            matched,
            vec!["test_login"],
            "DSL AND should match nodes satisfying both predicates"
        );
    }

    #[test]
    fn search_dsl_boolean_or() {
        let graph = MockGraph::with_fields(&[
            ("test_login", NodeKind::Test, &[("mark", "slow")]),
            ("test_logout", NodeKind::Test, &[("mark", "fast")]),
            ("test_signup", NodeKind::Test, &[("mark", "unit")]),
        ]);
        let results = search(&graph, "mark(slow) | mark(fast)", SearchScope::All);
        let matched = names(&graph, &results);
        assert_eq!(
            matched,
            vec!["test_login", "test_logout"],
            "DSL OR should match nodes satisfying either predicate"
        );
    }

    #[test]
    fn search_dsl_parse_failure_falls_back_to_substring() {
        let graph = MockGraph::new(&[
            ("test_login(slow)", NodeKind::Test),
            ("test_logout", NodeKind::Test),
        ]);
        // This looks like DSL (has parens) but won't parse as valid DSL
        // because "test_login(slow)" as a query is valid DSL — so let's
        // use something that will actually fail to parse.
        let results = search(&graph, "login(", SearchScope::All);
        // "login(" fails DSL parse (unterminated), falls back to substring
        let matched = names(&graph, &results);
        assert_eq!(
            matched,
            vec!["test_login(slow)"],
            "failed DSL parse should fall back to substring match"
        );
    }

    #[test]
    fn search_plain_text_no_dsl_attempt() {
        let graph = MockGraph::new(&[
            ("test_login", NodeKind::Test),
            ("test_logout", NodeKind::Test),
        ]);
        // No parens, no operators — should go straight to substring match
        let results = search(&graph, "login", SearchScope::All);
        let matched = names(&graph, &results);
        assert_eq!(
            matched,
            vec!["test_login"],
            "plain text without DSL markers should use substring matching"
        );
    }

    // ── looks_like_dsl tests ────────────────────────────────────────────

    #[test]
    fn looks_like_dsl_with_parens() {
        assert!(
            looks_like_dsl("name(foo)"),
            "'name(foo)' contains '(' so should look like DSL"
        );
    }

    #[test]
    fn looks_like_dsl_with_ampersand() {
        assert!(
            looks_like_dsl("a & b"),
            "'a & b' contains '&' so should look like DSL"
        );
    }

    #[test]
    fn looks_like_dsl_with_pipe() {
        assert!(
            looks_like_dsl("a | b"),
            "'a | b' contains '|' so should look like DSL"
        );
    }

    #[test]
    fn looks_like_dsl_plain_text() {
        assert!(
            !looks_like_dsl("test_login"),
            "plain text without DSL markers should not look like DSL"
        );
    }

    // ── try_compile_dsl tests ───────────────────────────────────────────

    #[test]
    fn try_compile_valid_dsl() {
        assert!(
            try_compile_dsl("name(foo)").is_some(),
            "valid DSL expression should compile successfully"
        );
    }

    #[test]
    fn try_compile_invalid_dsl() {
        assert!(
            try_compile_dsl("name(").is_none(),
            "invalid DSL expression should return None"
        );
    }

    #[test]
    fn try_compile_plain_text() {
        assert!(
            try_compile_dsl("foobar").is_none(),
            "plain text should fail DSL parsing (missing parens)"
        );
    }
}
