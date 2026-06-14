//! Pure-Rust bare-assert detection for strict-mode violation checking.
//!
//! Replaces `importer.py::_collect_bare_asserts()` by parsing Python source
//! files with `rustpython-parser` and finding `assert` statements without
//! a message — entirely in Rust, no PyO3 call needed.

use camino::Utf8Path;
use rustpython_parser::ast;

use crate::bridge::{RawViolation, ViolationKind};
use crate::python_ast;

/// Bare-assert violation found in a test function.
struct BareAssert {
    /// Node ID: `"path::fn_name"` or `"path::ClassName::method_name"`.
    node_id: String,
    /// Sorted line numbers of bare asserts.
    lines: Vec<u32>,
}

/// Parse a Python test file and return bare-assert violations for test functions.
pub(crate) fn collect_bare_asserts(path: &Utf8Path) -> Vec<RawViolation> {
    let (source, stmts) = match python_ast::parse_file(path) {
        Some(parsed) => parsed,
        None => return vec![],
    };

    let line_index = python_ast::build_line_index(&source);
    to_violations(find_bare_asserts(path, &stmts, &line_index))
}

/// Detect bare-assert violations from a pre-parsed AST.
///
/// Same logic as [`collect_bare_asserts`] but skips file I/O and parsing.
/// Used by `collect_items()` to avoid double-parsing in strict mode.
pub(crate) fn collect_bare_asserts_from_ast(
    path: &Utf8Path,
    source: &str,
    stmts: &[ast::Stmt],
) -> Vec<RawViolation> {
    let line_index = python_ast::build_line_index(source);
    to_violations(find_bare_asserts(path, stmts, &line_index))
}

/// Scan top-level statements for test functions/classes containing bare asserts.
fn find_bare_asserts(path: &Utf8Path, stmts: &[ast::Stmt], line_index: &[u32]) -> Vec<BareAssert> {
    let mut found = Vec::new();

    python_ast::walk_test_defs(stmts, |def, class| {
        let lines = walk_bare_asserts(def.body(), line_index);
        if !lines.is_empty() {
            let node_id = match class {
                Some(cls) => format!("{path}::{}::{}", cls.name, def.name()),
                None => format!("{path}::{}", def.name()),
            };
            found.push(BareAssert { node_id, lines });
        }
    });

    found
}

/// Walk a function body for bare `assert` statements, pruning nested functions.
fn walk_bare_asserts(body: &[ast::Stmt], line_index: &[u32]) -> Vec<u32> {
    let mut lines = Vec::new();
    let mut queue: Vec<&ast::Stmt> = body.iter().collect();

    while let Some(stmt) = queue.pop() {
        match stmt {
            // Prune: do not recurse into nested functions
            ast::Stmt::FunctionDef(_) | ast::Stmt::AsyncFunctionDef(_) => continue,
            ast::Stmt::Assert(a) if a.msg.is_none() => {
                lines.push(python_ast::offset_to_line(
                    line_index,
                    a.range.start().to_u32(),
                ));
            }
            _ => {}
        }
        // Recurse into child statements of compound nodes
        queue.extend(python_ast::compound_children(stmt));
    }

    lines.sort_unstable();
    lines
}

fn to_violations(found: Vec<BareAssert>) -> Vec<RawViolation> {
    found
        .into_iter()
        .map(|ba| RawViolation {
            node_id: ba.node_id,
            kind: ViolationKind::BareAssert,
            detail: ba
                .lines
                .iter()
                .map(|l| l.to_string())
                .collect::<Vec<_>>()
                .join(" "),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::python_ast::tests::{temp_path, write_temp_py};

    #[test]
    fn no_bare_asserts() {
        let f = write_temp_py("def test_ok():\n    assert 1 == 1, 'msg'\n");
        let violations = collect_bare_asserts(&temp_path(&f));
        assert!(violations.is_empty());
    }

    #[test]
    fn single_bare_assert() {
        let f = write_temp_py("def test_it():\n    assert True\n");
        let violations = collect_bare_asserts(&temp_path(&f));
        assert_eq!(violations.len(), 1);
        assert_eq!(violations[0].kind, ViolationKind::BareAssert);
        assert_eq!(violations[0].detail, "2");
    }

    #[test]
    fn multiple_bare_asserts() {
        let f = write_temp_py("def test_it():\n    assert True\n    assert False\n");
        let violations = collect_bare_asserts(&temp_path(&f));
        assert_eq!(violations.len(), 1);
        assert_eq!(violations[0].detail, "2 3");
    }

    #[test]
    fn skips_non_test_functions() {
        let f = write_temp_py("def helper():\n    assert True\n");
        let violations = collect_bare_asserts(&temp_path(&f));
        assert!(violations.is_empty());
    }

    #[test]
    fn async_test_function() {
        let f = write_temp_py("async def test_async():\n    assert True\n");
        let violations = collect_bare_asserts(&temp_path(&f));
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn class_method() {
        let f = write_temp_py("class TestFoo:\n    def test_bar(self):\n        assert True\n");
        let violations = collect_bare_asserts(&temp_path(&f));
        assert_eq!(violations.len(), 1);
        assert!(violations[0].node_id.contains("TestFoo::test_bar"));
    }

    #[test]
    fn prunes_nested_functions() {
        let f = write_temp_py("def test_it():\n    def helper():\n        assert True\n    pass\n");
        let violations = collect_bare_asserts(&temp_path(&f));
        assert!(violations.is_empty());
    }

    #[test]
    fn assert_inside_if() {
        let f = write_temp_py("def test_it():\n    if True:\n        assert True\n");
        let violations = collect_bare_asserts(&temp_path(&f));
        assert_eq!(violations.len(), 1);
        assert_eq!(violations[0].detail, "3");
    }

    #[test]
    fn assert_with_message_not_flagged() {
        let f = write_temp_py("def test_it():\n    assert True, 'has message'\n    assert False\n");
        let violations = collect_bare_asserts(&temp_path(&f));
        assert_eq!(violations.len(), 1);
        assert_eq!(violations[0].detail, "3");
    }

    #[test]
    fn syntax_error_returns_empty() {
        let f = write_temp_py("def test_broken(\n");
        let violations = collect_bare_asserts(&temp_path(&f));
        assert!(violations.is_empty());
    }

    #[test]
    fn nonexistent_file_returns_empty() {
        let violations = collect_bare_asserts(Utf8Path::new("/nonexistent/file.py"));
        assert!(violations.is_empty());
    }

    #[test]
    fn from_ast_matches_from_file() {
        let f = write_temp_py(
            "def test_a():\n    assert True\n\nclass TestB:\n    def test_c(self):\n        assert False\n",
        );
        let path = temp_path(&f);
        let from_file = collect_bare_asserts(&path);
        let (source, stmts) = python_ast::parse_file(&path).unwrap();
        let from_ast = collect_bare_asserts_from_ast(&path, &source, &stmts);
        assert_eq!(from_file.len(), from_ast.len());
        for (a, b) in from_file.iter().zip(from_ast.iter()) {
            assert_eq!(a.node_id, b.node_id);
            assert_eq!(a.detail, b.detail);
        }
    }

    #[test]
    fn skips_non_test_class() {
        let f = write_temp_py("class Helper:\n    def test_bar(self):\n        assert True\n");
        let violations = collect_bare_asserts(&temp_path(&f));
        assert!(violations.is_empty());
    }

    #[test]
    fn assert_inside_try() {
        let f = write_temp_py(
            "def test_it():\n    try:\n        assert True\n    except:\n        pass\n",
        );
        let violations = collect_bare_asserts(&temp_path(&f));
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn multiple_test_functions() {
        let f = write_temp_py(
            "def test_a():\n    assert True\n\ndef test_b():\n    assert 1 == 1, 'ok'\n",
        );
        let violations = collect_bare_asserts(&temp_path(&f));
        assert_eq!(violations.len(), 1);
        assert!(violations[0].node_id.contains("test_a"));
    }
}
