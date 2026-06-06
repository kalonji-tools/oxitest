//! Single-item inspect card formatter for the unified query system.
//!
//! [`format_inspect`] renders a detailed card for one [`QueryEntry`],
//! showing resource-specific fields in a human-readable layout.

use crate::python_ast;
use crate::query::resource::{QueryEntry, ResourceKind};
use camino::Utf8Path;

// ── Field sets per resource kind ──────────────────────────────────────────────

/// Return the ordered list of field names to display for a given resource kind.
fn fields_for(resource: ResourceKind) -> &'static [&'static str] {
    match resource {
        ResourceKind::Tests => &["source", "mark", "async"],
        ResourceKind::Fixtures => &["description", "source", "shared", "autouse", "async"],
        ResourceKind::Marks => &["used_in"],
        ResourceKind::Helpers => &["source"],
        ResourceKind::Plugins => &["protocol"],
    }
}

// ── format_inspect ────────────────────────────────────────────────────────────

/// Render a single-item inspect card for `entry`.
///
/// Output layout:
/// ```text
/// ─── {name} ───
///
///   source:      path/to/file.py
///   shared:      true
///   autouse:     true
/// ```
///
/// Fields that are absent or empty in the entry are skipped.
/// If a `_source_code` field is present it is appended after a blank line.
pub(crate) fn format_inspect(
    entry: &QueryEntry,
    resource: ResourceKind,
    _use_color: bool,
) -> String {
    let name = entry.get("name").unwrap_or("<unknown>");
    let mut out = format!("─── {name} ───\n\n");

    for field in fields_for(resource) {
        let Some(val) = entry.get(field) else {
            continue;
        };
        if val.is_empty() {
            continue;
        }
        // Multi-value fields with commas render as a bulleted list
        if val.contains(',') {
            out.push_str(&format!("  {}:\n", field));
            for item in val.split(',') {
                out.push_str(&format!("    - {}\n", item.trim()));
            }
        } else {
            out.push_str(&format!("  {:<14}{val}\n", format!("{field}:")));
        }
    }

    // Extract source code from file if we have source path and a node_id
    if let Some(snippet) = extract_source_snippet(entry) {
        out.push('\n');
        out.push_str(&snippet);
    }

    out
}

/// Extract the source code of a function from its file, given a query entry
/// with `name` (node_id like `path::fn_name`) and `source` (file path) fields.
fn extract_source_snippet(entry: &QueryEntry) -> Option<String> {
    let source_path = entry.get("source")?;
    let name = entry.get("name")?;

    // Extract function name from node_id (after last ::)
    let fn_name = name.rsplit("::").next()?;

    let path = Utf8Path::new(source_path);
    let (source, stmts) = python_ast::parse_file(path)?;
    let line_index = python_ast::build_line_index(&source);
    let lines: Vec<&str> = source.lines().collect();

    // Find the function in the AST and get its line range
    let (start_line, end_line) = find_function_lines(&stmts, fn_name, &line_index)?;

    // Extract source lines (1-based to 0-based, exclude next statement's line)
    let start = start_line.saturating_sub(1) as usize;
    let end = (end_line.saturating_sub(1) as usize).min(lines.len());

    if start >= lines.len() || start >= end {
        return None;
    }

    // Trim trailing blank lines
    let mut snippet = &lines[start..end];
    while snippet.last().is_some_and(|l| l.trim().is_empty()) {
        snippet = &snippet[..snippet.len() - 1];
    }

    if snippet.is_empty() {
        return None;
    }

    Some(snippet.join("\n") + "\n")
}

/// Find the start and end line numbers (1-based) for a function in AST statements.
fn find_function_lines(
    stmts: &[rustpython_parser::ast::Stmt],
    fn_name: &str,
    line_index: &[u32],
) -> Option<(u32, u32)> {
    use rustpython_parser::ast;

    for (i, stmt) in stmts.iter().enumerate() {
        match stmt {
            ast::Stmt::FunctionDef(f) if f.name.as_str() == fn_name => {
                let start = python_ast::offset_to_line(line_index, f.range.start().to_u32());
                let end = next_stmt_line(stmts, i, line_index).unwrap_or_else(|| {
                    python_ast::offset_to_line(line_index, f.range.end().to_u32())
                });
                return Some((start, end));
            }
            ast::Stmt::AsyncFunctionDef(f) if f.name.as_str() == fn_name => {
                let start = python_ast::offset_to_line(line_index, f.range.start().to_u32());
                let end = next_stmt_line(stmts, i, line_index).unwrap_or_else(|| {
                    python_ast::offset_to_line(line_index, f.range.end().to_u32())
                });
                return Some((start, end));
            }
            ast::Stmt::ClassDef(cls) if python_ast::is_test_class(&cls.name) => {
                // Search inside test classes
                if let Some(result) = find_function_lines(&cls.body, fn_name, line_index) {
                    return Some(result);
                }
            }
            _ => {}
        }
    }
    None
}

/// Get the visual start line of the next statement (used as the end boundary).
///
/// For decorated functions/classes, the statement's `range().start()` points
/// at `def`/`class`, not the first `@` decorator. We check decorator positions
/// to find the true visual start.
fn next_stmt_line(
    stmts: &[rustpython_parser::ast::Stmt],
    current_idx: usize,
    line_index: &[u32],
) -> Option<u32> {
    use rustpython_parser::ast::{self, Ranged};
    let next = stmts.get(current_idx + 1)?;
    let stmt_line = python_ast::offset_to_line(line_index, next.range().start().to_u32());

    // Check if the next statement has decorators that start earlier
    let first_decorator_line = match next {
        ast::Stmt::FunctionDef(f) => f.decorator_list.first(),
        ast::Stmt::AsyncFunctionDef(f) => f.decorator_list.first(),
        ast::Stmt::ClassDef(c) => c.decorator_list.first(),
        _ => None,
    }
    .map(|d| python_ast::offset_to_line(line_index, d.range().start().to_u32()));

    Some(first_decorator_line.unwrap_or(stmt_line).min(stmt_line))
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn entry(pairs: &[(&str, &str)]) -> QueryEntry {
        let fields: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        QueryEntry { fields }
    }

    #[test]
    fn inspect_test_entry() {
        let e = entry(&[
            ("name", "test_foo"),
            ("source", "tests/test_a.py"),
            ("mark", "slow"),
            ("async", "false"),
        ]);
        let out = format_inspect(&e, ResourceKind::Tests, false);
        assert!(out.contains("─── test_foo ───"), "header missing: {out:?}");
        assert!(out.contains("source:"), "source field missing: {out:?}");
        assert!(
            out.contains("tests/test_a.py"),
            "source value missing: {out:?}"
        );
        assert!(out.contains("mark:"), "mark field missing: {out:?}");
        assert!(out.contains("slow"), "mark value missing: {out:?}");
    }

    #[test]
    fn inspect_fixture_entry() {
        let e = entry(&[
            ("name", "db_session"),
            ("source", "conftest.py"),
            ("shared", "true"),
            ("autouse", "true"),
            ("async", "false"),
        ]);
        let out = format_inspect(&e, ResourceKind::Fixtures, false);
        assert!(out.contains("shared:"), "shared field missing: {out:?}");
        assert!(out.contains("true"), "shared value missing: {out:?}");
        assert!(out.contains("autouse:"), "autouse field missing: {out:?}");
        assert!(out.contains("true"), "autouse value missing: {out:?}");
    }

    #[test]
    fn inspect_missing_fields_skipped() {
        // Entry with only a name — no other fields should appear
        let e = entry(&[("name", "test_bare")]);
        let out = format_inspect(&e, ResourceKind::Tests, false);
        assert!(
            out.starts_with("─── test_bare ───"),
            "header wrong: {out:?}"
        );
        // No field lines should be present (source/mark/async all absent)
        assert!(!out.contains("source:"), "unexpected source field: {out:?}");
        assert!(!out.contains("mark:"), "unexpected mark field: {out:?}");
        assert!(!out.contains("async:"), "unexpected async field: {out:?}");
    }

    // ── Snapshot tests ────────────────────────────────────────────────────────

    #[test]
    fn snap_inspect_test_entry() {
        let e = entry(&[
            ("name", "test_network_call"),
            ("source", "tests/test_api.py"),
            ("mark", "slow,network"),
            ("async", "true"),
        ]);
        insta::assert_snapshot!(format_inspect(&e, ResourceKind::Tests, false));
    }

    #[test]
    fn snap_inspect_fixture_entry() {
        let e = entry(&[
            ("name", "db_session"),
            ("source", "conftest.py"),
            ("shared", "true"),
            ("autouse", "true"),
            ("async", "false"),
        ]);
        insta::assert_snapshot!(format_inspect(&e, ResourceKind::Fixtures, false));
    }
}
