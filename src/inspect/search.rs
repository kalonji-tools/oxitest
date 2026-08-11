//! Search engine for `oxitest inspect`: fuzzy matching and DSL auto-detection.

use crate::query::{ast::Expr, compile, eval};

use super::graph::InspectGraph;

// Re-export graph types so callers reference one set of types.
pub use super::graph::NodeRef;

/// The scope to search within.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SearchScope {
    /// Search all nodes in the graph.
    Global,
    /// Search within a specific set of candidate nodes.
    Context(Vec<NodeRef>),
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
pub fn search(graph: &InspectGraph, query: &str, scope: SearchScope) -> Vec<NodeRef> {
    if query.is_empty() {
        return Vec::new();
    }

    let candidates = match scope {
        SearchScope::Global => graph.all_node_refs(),
        SearchScope::Context(refs) => refs,
    };

    // Try DSL auto-detection: if the query looks like a DSL expression
    // (contains parentheses, which are required by the DSL grammar),
    // attempt to compile and evaluate it.
    if looks_like_dsl(query)
        && let Some(expr) = try_compile_dsl(query)
    {
        return candidates
            .into_iter()
            .filter(|r| {
                let entry = graph.node_query_entry(r);
                eval::eval(&expr, &entry)
            })
            .collect();
    }

    // Fallback: case-insensitive substring match on node name.
    let query_lower = query.to_lowercase();
    candidates
        .into_iter()
        .filter(|r| graph.node_name(r).to_lowercase().contains(&query_lower))
        .collect()
}

/// Heuristic: does the query look like a DSL expression?
///
/// The DSL grammar requires `predicate(...)` syntax, so any query
/// containing `(` is a strong signal. We also accept queries containing
/// `&`, `|`, or `!` as boolean operators.
fn looks_like_dsl(query: &str) -> bool {
    query.contains('(') || query.contains('&') || query.contains('|')
}

/// Try to lex + parse a query as a DSL expression.
///
/// Returns `Some(expr)` on success, `None` on any parse failure.
fn try_compile_dsl(query: &str) -> Option<Expr> {
    let tokens = compile::lex(query).ok()?;
    compile::parse(tokens).ok()
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::super::graph::{
        InspectGraph, NodeKind,
        nodes::{FixtureNode, MarkNode, TestNode},
    };
    use super::*;

    // ── Graph builder helpers ────────────────────────────────────────────
    //
    // Build InspectGraph instances directly for search tests, without going
    // through GraphBuilder (which wires cross-references we don't need here).

    fn fixture(name: &str) -> FixtureNode {
        FixtureNode {
            name: name.to_string(),
            binding_type: String::new(),
            scope: "function".to_string(),
            autouse: false,
            source: String::new(),
            is_async: false,
            description: String::new(),
            consumers: vec![],
            declaration_idx: None,
            plugin_idx: None,
        }
    }

    fn test_node(node_id: &str) -> TestNode {
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

    fn test_node_with_mark(node_id: &str, mark_idx: usize) -> TestNode {
        TestNode {
            node_id: node_id.to_string(),
            is_async: false,
            param_id: None,
            param_count: 0,
            variants: vec![],
            fixture_deps: vec![],
            marks: vec![mark_idx],
        }
    }

    fn mark(name: &str) -> MarkNode {
        MarkNode {
            name: name.to_string(),
            used_by: vec![],
        }
    }

    fn names(graph: &InspectGraph, refs: &[NodeRef]) -> Vec<String> {
        refs.iter()
            .map(|r| graph.node_name(r).to_string())
            .collect()
    }

    // ── Substring match tests ───────────────────────────────────────────

    #[test]
    fn search_substring_match() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(fixture("db_session"));
        graph.tests.push(test_node("test_login"));
        graph.fixtures.push(fixture("db_cleanup"));
        let results = search(&graph, "db", SearchScope::Global);
        let matched = names(&graph, &results);
        assert_eq!(
            matched,
            vec!["db_session", "db_cleanup"],
            "substring 'db' should match nodes whose names contain 'db'"
        );
    }

    #[test]
    fn search_case_insensitive() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(fixture("db_session"));
        graph.tests.push(test_node("test_login"));
        let results = search(&graph, "DB", SearchScope::Global);
        let matched = names(&graph, &results);
        assert_eq!(
            matched,
            vec!["db_session"],
            "case-insensitive search for 'DB' should match 'db_session'"
        );
    }

    #[test]
    fn search_no_match_returns_empty() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(fixture("db_session"));
        graph.tests.push(test_node("test_login"));
        let results = search(&graph, "zzz", SearchScope::Global);
        assert!(
            results.is_empty(),
            "query 'zzz' should match nothing, got: {:?}",
            names(&graph, &results)
        );
    }

    #[test]
    fn search_empty_query_returns_empty() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(fixture("db_session"));
        let results = search(&graph, "", SearchScope::Global);
        assert!(results.is_empty(), "empty query should return no results");
    }

    #[test]
    fn search_context_scope_filters_to_candidates() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(fixture("db_session"));
        graph.tests.push(test_node("db_test"));
        graph.fixtures.push(fixture("db_cleanup"));
        // Context scope with only fixture refs — test nodes excluded.
        let candidates = graph.nodes_of_kind(NodeKind::Fixture);
        let results = search(&graph, "db", SearchScope::Context(candidates));
        let matched = names(&graph, &results);
        assert_eq!(
            matched,
            vec!["db_session", "db_cleanup"],
            "Context scope with fixture candidates should exclude Test nodes even if name matches"
        );
    }

    #[test]
    fn search_context_scope_test_only() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(fixture("db_session"));
        graph.tests.push(test_node("db_test"));
        graph.fixtures.push(fixture("db_cleanup"));
        // Context scope with only test refs.
        let candidates = graph.nodes_of_kind(NodeKind::Test);
        let results = search(&graph, "db", SearchScope::Context(candidates));
        let matched = names(&graph, &results);
        assert_eq!(
            matched,
            vec!["db_test"],
            "Context scope with test candidates should only return test nodes"
        );
    }

    #[test]
    fn search_context_scope_empty_candidates() {
        let mut graph = InspectGraph::default();
        graph.fixtures.push(fixture("db_session"));
        let results = search(&graph, "db", SearchScope::Context(vec![]));
        assert!(
            results.is_empty(),
            "Context scope with empty candidates should always return no results"
        );
    }

    // ── DSL auto-detection tests ────────────────────────────────────────

    #[test]
    fn search_dsl_name_predicate() {
        let mut graph = InspectGraph::default();
        graph.tests.push(test_node("test_login"));
        graph.tests.push(test_node("test_logout"));
        graph.fixtures.push(fixture("db_session"));
        let results = search(&graph, "name(~login)", SearchScope::Global);
        let matched = names(&graph, &results);
        assert_eq!(
            matched,
            vec!["test_login"],
            "DSL name(~login) should match only 'test_login'"
        );
    }

    #[test]
    fn search_dsl_boolean_and() {
        // mark index 0 = "slow", mark index 1 = "fast"
        let mut graph = InspectGraph::default();
        graph.marks.push(mark("slow"));
        graph.marks.push(mark("fast"));
        graph.tests.push(test_node_with_mark("test_login", 0));
        graph.tests.push(test_node_with_mark("test_logout", 1));
        let results = search(&graph, "name(~login) & mark(slow)", SearchScope::Global);
        let matched = names(&graph, &results);
        assert_eq!(
            matched,
            vec!["test_login"],
            "DSL AND should match nodes satisfying both predicates"
        );
    }

    #[test]
    fn search_dsl_boolean_or() {
        let mut graph = InspectGraph::default();
        graph.marks.push(mark("slow"));
        graph.marks.push(mark("fast"));
        graph.marks.push(mark("unit"));
        graph.tests.push(test_node_with_mark("test_login", 0));
        graph.tests.push(test_node_with_mark("test_logout", 1));
        graph.tests.push(test_node_with_mark("test_signup", 2));
        let results = search(&graph, "mark(slow) | mark(fast)", SearchScope::Global);
        let matched = names(&graph, &results);
        assert_eq!(
            matched,
            vec!["test_login", "test_logout"],
            "DSL OR should match nodes satisfying either predicate"
        );
    }

    #[test]
    fn search_dsl_parse_failure_falls_back_to_substring() {
        let mut graph = InspectGraph::default();
        // Use a fixture whose name contains "login(" — fixtures use `name` field.
        graph.fixtures.push(fixture("test_login(slow)"));
        graph.tests.push(test_node("test_logout"));
        let results = search(&graph, "login(", SearchScope::Global);
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
        let mut graph = InspectGraph::default();
        graph.tests.push(test_node("test_login"));
        graph.tests.push(test_node("test_logout"));
        // No parens, no operators — should go straight to substring match
        let results = search(&graph, "login", SearchScope::Global);
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
