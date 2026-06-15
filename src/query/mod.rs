pub(crate) mod ast;
pub(crate) mod bridge;
pub(crate) mod compile;
pub(crate) mod eval;
pub(crate) mod extract;
pub(crate) mod format;
pub(crate) mod fzf;
pub(crate) mod highlight;
pub(crate) mod inspect;
pub(crate) mod resource;

use crate::config;
use resource::{QueryEntry, ResourceKind};

/// Determine whether the query requires a Python session (i.e., SessionPhase).
///
/// Fixtures and plugins always need Python. Tests, marks, and helpers are
/// instant-tier unless the DSL expression references predicates that require
/// Python data (shared, autouse, protocol).
pub(crate) fn needs_python(resource: ResourceKind, expr_str: Option<&str>) -> bool {
    match resource {
        ResourceKind::Fixtures | ResourceKind::Plugins => true,
        ResourceKind::Tests | ResourceKind::Marks | ResourceKind::Helpers => {
            if let Some(s) = expr_str {
                match compile::lex(s).and_then(compile::parse) {
                    Ok(expr) => return expr_needs_python(&expr),
                    Err(e) => {
                        tracing::warn!(expr = s, error = %e, "DSL parse failed, assuming Python needed");
                        return true;
                    }
                }
            }
            false
        }
    }
}

/// Walk the expression AST checking for predicates that need Python.
fn expr_needs_python(expr: &ast::Expr) -> bool {
    match expr {
        ast::Expr::And(a, b) | ast::Expr::Or(a, b) => expr_needs_python(a) || expr_needs_python(b),
        ast::Expr::Not(inner) => expr_needs_python(inner),
        ast::Expr::Predicate { name, .. } => {
            matches!(name.as_str(), "shared" | "autouse" | "protocol")
        }
    }
}

/// Return the default columns to display for a given resource kind.
pub(crate) fn default_columns(resource: ResourceKind) -> Vec<&'static str> {
    match resource {
        ResourceKind::Tests => vec!["name"],
        ResourceKind::Fixtures => vec!["name"],
        ResourceKind::Marks => vec!["name"],
        ResourceKind::Helpers => vec!["name", "source"],
        ResourceKind::Plugins => vec!["name"],
    }
}

/// Collect entries for the given resource kind.
///
/// For instant-tier resources (tests, marks, helpers), this performs pure-Rust
/// AST extraction. For full-tier resources (fixtures, plugins), this delegates
/// to the Python bridge via [`extract_fixture_entries`] and
/// [`extract_plugin_entries`].
pub(crate) fn collect_entries(
    py: pyo3::Python<'_>,
    resource: ResourceKind,
    test_files: &[camino::Utf8PathBuf],
    conftest_files: &[camino::Utf8PathBuf],
    session: Option<&crate::bridge::FixtureSession>,
    cfg: &config::Config,
) -> Result<Vec<QueryEntry>, String> {
    match resource {
        ResourceKind::Tests => Ok(extract::extract_test_entries(test_files)),
        ResourceKind::Marks => Ok(extract::extract_mark_entries(
            test_files,
            &cfg.markers.registered_markers,
        )),
        ResourceKind::Helpers => Ok(extract::extract_helper_entries(conftest_files)),
        ResourceKind::Fixtures => match session {
            Some(s) => extract_fixture_entries(py, s),
            None => Ok(vec![]),
        },
        ResourceKind::Plugins => match session {
            Some(s) => extract_plugin_entries(py, s),
            None => Ok(vec![]),
        },
    }
}

fn extract_fixture_entries(
    py: pyo3::Python<'_>,
    session: &crate::bridge::FixtureSession,
) -> Result<Vec<QueryEntry>, String> {
    let raw = self::bridge::fixture_entries(session, py).map_err(|e| e.to_string())?;
    Ok(raw
        .into_iter()
        .map(|fields| QueryEntry { fields })
        .collect())
}

fn extract_plugin_entries(
    py: pyo3::Python<'_>,
    session: &crate::bridge::FixtureSession,
) -> Result<Vec<QueryEntry>, String> {
    let raw = self::bridge::plugin_entries(session, py).map_err(|e| e.to_string())?;
    Ok(raw
        .into_iter()
        .map(|fields| QueryEntry { fields })
        .collect())
}

/// Run a query and return the formatted output string.
///
/// Handles the full pipeline: collect entries, parse/validate/apply DSL filter,
/// handle --inspect, --count, --format, and default columnar output.
#[allow(clippy::too_many_arguments)]
pub(crate) fn run_query(
    py: pyo3::Python<'_>,
    args: &config::QueryArgs,
    test_files: &[camino::Utf8PathBuf],
    conftest_files: &[camino::Utf8PathBuf],
    session: Option<&crate::bridge::FixtureSession>,
    cfg: &config::Config,
    is_tty: bool,
    use_color: bool,
) -> Result<String, String> {
    // 1. Collect entries
    let mut entries = collect_entries(py, args.resource, test_files, conftest_files, session, cfg)?;

    // 2. Parse and apply DSL expression filter
    let parsed_expr = if let Some(ref expr_str) = args.expression {
        let tokens = compile::lex(expr_str).map_err(|e| e.to_string())?;
        let expr = compile::parse(tokens).map_err(|e| e.to_string())?;
        eval::validate_predicates(&expr, &args.resource).map_err(|e| e.to_string())?;
        Some(expr)
    } else {
        None
    };

    if let Some(ref expr) = parsed_expr {
        entries.retain(|e| eval::eval(expr, e));
    }

    // 3. Handle --inspect
    if let Some(ref id) = args.inspect {
        let found = entries
            .iter()
            .find(|e| e.get("name").is_some_and(|n| n.contains(id.as_str())));
        return match found {
            Some(entry) => Ok(inspect::format_inspect(entry, args.resource, use_color)),
            None => Err(format!("no {} matching '{id}'", args.resource.as_str())),
        };
    }

    // 4. Handle --count
    if args.count {
        let count = entries.len();
        let word = args.resource.as_str();
        return Ok(format!("{count} {word}\n"));
    }

    // 5. Handle --format=jsonl
    if let Some(config::QueryFormat::Jsonl) = args.format {
        return Ok(format::format_jsonl(&entries));
    }

    // 6. Default output: columnar (TTY) or tab-delimited (piped)
    let columns = default_columns(args.resource);
    if is_tty {
        Ok(format::format_columnar(&entries, &columns))
    } else {
        Ok(format::format_tab(&entries, &columns))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn needs_python_fixtures_always_true() {
        assert!(needs_python(ResourceKind::Fixtures, None));
    }

    #[test]
    fn needs_python_plugins_always_true() {
        assert!(needs_python(ResourceKind::Plugins, None));
    }

    #[test]
    fn needs_python_tests_without_expr_is_false() {
        assert!(!needs_python(ResourceKind::Tests, None));
    }

    #[test]
    fn needs_python_tests_with_name_expr_is_false() {
        assert!(!needs_python(ResourceKind::Tests, Some("name(~foo)")));
    }

    #[test]
    fn needs_python_tests_with_shared_expr_is_true() {
        assert!(needs_python(ResourceKind::Tests, Some("shared()")));
    }

    #[test]
    fn needs_python_marks_without_expr_is_false() {
        assert!(!needs_python(ResourceKind::Marks, None));
    }

    #[test]
    fn needs_python_with_invalid_expression_returns_true() {
        assert!(needs_python(ResourceKind::Tests, Some("unclosed(")));
    }

    #[test]
    fn default_columns_tests() {
        let cols = default_columns(ResourceKind::Tests);
        assert!(cols.contains(&"name"));
        assert_eq!(cols.len(), 1, "tests should only show name");
    }

    #[test]
    fn default_columns_fixtures() {
        let cols = default_columns(ResourceKind::Fixtures);
        assert!(cols.contains(&"name"));
    }
}
