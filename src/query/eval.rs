//! DSL validation and evaluation for the unified query system.
//!
//! Validates predicate names against resource kinds and evaluates
//! expression trees against [`crate::query::resource::QueryEntry`] field maps.

use regex::Regex;

use super::ast::{DslError, Expr, Matcher};
use super::resource::ResourceKind;

// ── Evaluator ─────────────────────────────────────────────────────────────────

/// Check whether any comma-separated value in `field_value` matches `pattern`.
///
/// A value matches if it equals `pattern` exactly or contains it as a substring.
fn match_field_value(field_value: &str, pattern: &str) -> bool {
    field_value.split(',').any(|v| {
        let v = v.trim();
        v == pattern || v.contains(pattern)
    })
}

/// Evaluate an [`Expr`] against a [`crate::query::resource::QueryEntry`].
///
/// Returns `true` if the entry matches the expression.
pub(crate) fn eval(expr: &Expr, entry: &crate::query::resource::QueryEntry) -> bool {
    match expr {
        Expr::And(a, b) => eval(a, entry) && eval(b, entry),
        Expr::Or(a, b) => eval(a, entry) || eval(b, entry),
        Expr::Not(inner) => !eval(inner, entry),
        Expr::Predicate { name, matcher } => {
            let Some(field_val) = entry.get(name) else {
                return false;
            };
            match matcher {
                Matcher::Any => !field_val.is_empty() && field_val != "false",
                Matcher::Contains(s) => match_field_value(field_val, s),
                Matcher::Exact(s) => field_val.split(',').any(|v| v.trim() == s.as_str()),
                Matcher::Regex(pattern) => {
                    // Compile at eval time; errors are surfaced via validate_predicates
                    // before eval is called in production, so unwrap is safe here for
                    // patterns that passed validation.
                    match Regex::new(pattern) {
                        Ok(re) => field_val.split(',').any(|v| re.is_match(v.trim())),
                        Err(_) => false,
                    }
                }
            }
        }
    }
}

/// Walk the expression tree and validate that every predicate name is allowed
/// for the given resource kind.
///
/// # Errors
///
/// Returns [`DslError::InvalidPredicate`] for the first invalid predicate found,
/// or [`DslError::InvalidRegex`] if a regex pattern fails to compile.
pub(crate) fn validate_predicates(expr: &Expr, resource: &ResourceKind) -> Result<(), DslError> {
    match expr {
        Expr::And(a, b) | Expr::Or(a, b) => {
            validate_predicates(a, resource)?;
            validate_predicates(b, resource)?;
            Ok(())
        }
        Expr::Not(inner) => validate_predicates(inner, resource),
        Expr::Predicate { name, matcher } => {
            let valid = resource.valid_predicates();
            if !valid.contains(&name.as_str()) {
                return Err(DslError::InvalidPredicate {
                    predicate: name.clone(),
                    resource: resource.as_str().to_string(),
                });
            }
            // Also validate regex patterns compile
            if let Matcher::Regex(pattern) = matcher {
                Regex::new(pattern)
                    .map_err(|e| DslError::InvalidRegex(pattern.clone(), e.to_string()))?;
            }
            Ok(())
        }
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::query::compile;
    use crate::query::resource::{QueryEntry, ResourceKind};
    use std::collections::HashMap;

    // ── Evaluator tests ───────────────────────────────────────────────────────

    fn entry(pairs: &[(&str, &str)]) -> QueryEntry {
        let fields: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        QueryEntry { fields }
    }

    fn lex_and_parse(input: &str) -> Result<Expr, DslError> {
        let tokens = compile::lex(input)?;
        compile::parse(tokens)
    }

    fn eval_str(input: &str, e: &QueryEntry) -> bool {
        let expr = lex_and_parse(input).unwrap();
        eval(&expr, e)
    }

    #[test]
    fn eval_contains_match() {
        assert!(eval_str("name(foo)", &entry(&[("name", "foobar")])));
    }

    #[test]
    fn eval_contains_no_match() {
        assert!(!eval_str("name(foo)", &entry(&[("name", "bar")])));
    }

    #[test]
    fn eval_exact_match() {
        assert!(eval_str("name(=foo)", &entry(&[("name", "foo")])));
    }

    #[test]
    fn eval_exact_no_match() {
        assert!(!eval_str("name(=foo)", &entry(&[("name", "foobar")])));
    }

    #[test]
    fn eval_regex_match() {
        assert!(eval_str(
            "name(/^test_/)",
            &entry(&[("name", "test_something")])
        ));
    }

    #[test]
    fn eval_boolean_true() {
        assert!(eval_str("async()", &entry(&[("async", "true")])));
    }

    #[test]
    fn eval_boolean_false() {
        assert!(!eval_str("async()", &entry(&[("async", "false")])));
    }

    #[test]
    fn eval_boolean_missing_field() {
        assert!(!eval_str("async()", &entry(&[])));
    }

    #[test]
    fn eval_and() {
        let e = entry(&[("name", "foo"), ("mark", "slow")]);
        assert!(eval_str("name(foo) & mark(slow)", &e));
        assert!(!eval_str("name(foo) & mark(fast)", &e));
    }

    #[test]
    fn eval_and_short_circuit() {
        // Missing field means false; AND short-circuits
        let e = entry(&[("name", "foo")]);
        assert!(!eval_str("name(foo) & mark(slow)", &e));
    }

    #[test]
    fn eval_or() {
        let e = entry(&[("name", "foo")]);
        assert!(eval_str("name(foo) | mark(slow)", &e));
        assert!(!eval_str("name(bar) | mark(slow)", &e));
    }

    #[test]
    fn eval_not() {
        let e = entry(&[("name", "foo")]);
        assert!(!eval_str("!name(foo)", &e));
        assert!(eval_str("!name(bar)", &e));
    }

    #[test]
    fn eval_multi_value_contains() {
        // field "mark" has two comma-separated values
        let e = entry(&[("mark", "slow,integration")]);
        assert!(eval_str("mark(~slow)", &e));
        assert!(eval_str("mark(~integration)", &e));
        assert!(!eval_str("mark(~fast)", &e));
    }

    #[test]
    fn eval_multi_value_exact() {
        let e = entry(&[("mark", "slow,integration")]);
        assert!(eval_str("mark(=slow)", &e));
        assert!(eval_str("mark(=integration)", &e));
        assert!(!eval_str("mark(=slo)", &e));
    }

    // ── Validation tests ──────────────────────────────────────────────────────

    #[test]
    fn validate_valid_predicate() {
        let expr = lex_and_parse("name(foo)").unwrap();
        assert!(validate_predicates(&expr, &ResourceKind::Tests).is_ok());
    }

    #[test]
    fn validate_invalid_predicate() {
        // "shared" is valid for Fixtures but not for Tests
        let expr = lex_and_parse("shared()").unwrap();
        assert!(matches!(
            validate_predicates(&expr, &ResourceKind::Tests),
            Err(DslError::InvalidPredicate { .. })
        ));
    }

    #[test]
    fn validate_shared_valid_for_fixtures() {
        let expr = lex_and_parse("shared()").unwrap();
        assert!(validate_predicates(&expr, &ResourceKind::Fixtures).is_ok());
    }

    #[test]
    fn validate_protocol_valid_for_plugins() {
        let expr = lex_and_parse("protocol(reporter)").unwrap();
        assert!(validate_predicates(&expr, &ResourceKind::Plugins).is_ok());
    }

    // ── match_field_value tests ───────────────────────────────────────────────

    #[test]
    fn match_field_value_exact() {
        assert!(match_field_value("slow", "slow"));
    }

    #[test]
    fn match_field_value_in_comma_list() {
        assert!(match_field_value("slow,fast,unit", "fast"));
    }

    #[test]
    fn match_field_value_no_match() {
        assert!(!match_field_value("slow,fast", "integration"));
    }

    #[test]
    fn match_field_value_substring() {
        assert!(match_field_value("slow_test", "slow"));
    }

    #[test]
    fn match_field_value_trims_whitespace() {
        assert!(match_field_value("slow, fast , unit", "fast"));
    }
}
