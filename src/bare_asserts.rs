//! Pure-Rust bare-assert detection for strict-mode violation checking.
//!
//! Replaces `importer.py::_collect_bare_asserts()` by parsing Python source
//! files with `rustpython-parser` and finding `assert` statements without
//! a message — entirely in Rust, no PyO3 call needed.

use camino::Utf8Path;
use rustpython_parser::{ast, Parse};

use crate::bridge::RawViolation;
use crate::bridge::ViolationKind;

/// Bare-assert violation found in a test function.
struct BareAssert {
    /// Node ID: `"path::fn_name"` or `"path::ClassName::method_name"`.
    node_id: String,
    /// Sorted line numbers of bare asserts.
    lines: Vec<u32>,
}

/// Build an index mapping byte offsets to 1-based line numbers.
fn build_line_index(source: &str) -> Vec<u32> {
    let mut newlines = vec![0u32]; // line 1 starts at byte 0
    for (i, b) in source.bytes().enumerate() {
        if b == b'\n' {
            newlines.push(i as u32 + 1);
        }
    }
    newlines
}

/// Convert a byte offset to a 1-based line number.
fn offset_to_line(line_index: &[u32], offset: u32) -> u32 {
    match line_index.binary_search(&offset) {
        Ok(i) => i as u32 + 1,
        Err(i) => i as u32,
    }
}

/// Parse a Python test file and return bare-assert violations for test functions.
///
/// Mirrors `importer.py::_collect_bare_asserts()`:
/// - Finds `def test_*` at module level and in `class Test*`
/// - For each test function, walks the body (pruning nested functions) for
///   `assert` statements without a message
/// - Returns one `RawViolation` per test function that has bare asserts
pub(crate) fn collect_bare_asserts(path: &Utf8Path) -> Vec<RawViolation> {
    let source = match std::fs::read_to_string(path.as_std_path()) {
        Ok(s) => s,
        Err(_) => return vec![],
    };

    let stmts = match ast::Suite::parse(&source, path.as_str()) {
        Ok(s) => s,
        Err(_) => return vec![],
    };

    let line_index = build_line_index(&source);

    let mut found = Vec::new();

    for stmt in &stmts {
        match stmt {
            ast::Stmt::FunctionDef(f) if is_test_fn(&f.name) => {
                let lines = walk_bare_asserts(&f.body, &line_index);
                if !lines.is_empty() {
                    found.push(BareAssert {
                        node_id: format!("{path}::{}", f.name),
                        lines,
                    });
                }
            }
            ast::Stmt::AsyncFunctionDef(f) if is_test_fn(&f.name) => {
                let lines = walk_bare_asserts(&f.body, &line_index);
                if !lines.is_empty() {
                    found.push(BareAssert {
                        node_id: format!("{path}::{}", f.name),
                        lines,
                    });
                }
            }
            ast::Stmt::ClassDef(cls) if is_test_class(&cls.name) => {
                for item in &cls.body {
                    match item {
                        ast::Stmt::FunctionDef(f) if is_test_fn(&f.name) => {
                            let lines = walk_bare_asserts(&f.body, &line_index);
                            if !lines.is_empty() {
                                found.push(BareAssert {
                                    node_id: format!("{path}::{}::{}", cls.name, f.name),
                                    lines,
                                });
                            }
                        }
                        ast::Stmt::AsyncFunctionDef(f) if is_test_fn(&f.name) => {
                            let lines = walk_bare_asserts(&f.body, &line_index);
                            if !lines.is_empty() {
                                found.push(BareAssert {
                                    node_id: format!("{path}::{}::{}", cls.name, f.name),
                                    lines,
                                });
                            }
                        }
                        _ => {}
                    }
                }
            }
            _ => {}
        }
    }

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

fn is_test_fn(name: &str) -> bool {
    name.starts_with("test_")
}

fn is_test_class(name: &str) -> bool {
    name.starts_with("Test")
}

/// Walk a function body for bare `assert` statements, pruning nested functions.
///
/// Mirrors `_ast_utils.py::walk_bare_asserts()`: BFS through child nodes,
/// skipping nested FunctionDef/AsyncFunctionDef, collecting Assert nodes
/// where `msg` is None.
fn walk_bare_asserts(body: &[ast::Stmt], line_index: &[u32]) -> Vec<u32> {
    let mut lines = Vec::new();
    let mut queue: Vec<&ast::Stmt> = body.iter().collect();

    while let Some(stmt) = queue.pop() {
        match stmt {
            // Prune: do not recurse into nested functions
            ast::Stmt::FunctionDef(_) | ast::Stmt::AsyncFunctionDef(_) => continue,
            ast::Stmt::Assert(a) if a.msg.is_none() => {
                lines.push(offset_to_line(line_index, a.range.start().to_u32()));
            }
            _ => {}
        }
        // Recurse into child statements of compound nodes
        queue.extend(child_stmts(stmt));
    }

    lines.sort_unstable();
    lines
}

/// Yield the child statement bodies of compound statements.
fn child_stmts(stmt: &ast::Stmt) -> Vec<&ast::Stmt> {
    match stmt {
        ast::Stmt::If(n) => chain(&n.body, &n.orelse),
        ast::Stmt::While(n) => chain(&n.body, &n.orelse),
        ast::Stmt::For(n) => chain(&n.body, &n.orelse),
        ast::Stmt::AsyncFor(n) => chain(&n.body, &n.orelse),
        ast::Stmt::With(n) => n.body.iter().collect(),
        ast::Stmt::AsyncWith(n) => n.body.iter().collect(),
        ast::Stmt::Try(n) => {
            let mut stmts: Vec<&ast::Stmt> = Vec::new();
            stmts.extend(&n.body);
            for handler in &n.handlers {
                let ast::ExceptHandler::ExceptHandler(h) = handler;
                stmts.extend(&h.body);
            }
            stmts.extend(&n.orelse);
            stmts.extend(&n.finalbody);
            stmts
        }
        ast::Stmt::TryStar(n) => {
            let mut stmts: Vec<&ast::Stmt> = Vec::new();
            stmts.extend(&n.body);
            for handler in &n.handlers {
                let ast::ExceptHandler::ExceptHandler(h) = handler;
                stmts.extend(&h.body);
            }
            stmts.extend(&n.orelse);
            stmts.extend(&n.finalbody);
            stmts
        }
        ast::Stmt::Match(n) => n.cases.iter().flat_map(|c| &c.body).collect(),
        ast::Stmt::ClassDef(n) => n.body.iter().collect(),
        _ => vec![],
    }
}

fn chain<'a>(a: &'a [ast::Stmt], b: &'a [ast::Stmt]) -> Vec<&'a ast::Stmt> {
    a.iter().chain(b.iter()).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use camino::Utf8PathBuf;
    use std::io::Write;

    fn write_temp_py(content: &str) -> tempfile::NamedTempFile {
        let mut f = tempfile::Builder::new().suffix(".py").tempfile().unwrap();
        f.write_all(content.as_bytes()).unwrap();
        f
    }

    fn temp_path(f: &tempfile::NamedTempFile) -> Utf8PathBuf {
        Utf8PathBuf::from_path_buf(f.path().to_path_buf()).unwrap()
    }

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
        assert_eq!(violations[0].detail, "3"); // only the bare one
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
