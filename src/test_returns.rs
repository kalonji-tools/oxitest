//! Strict check: a test function returns `None`.
//!
//! The static third of #2067. A `return <value>` in a test body is a smell
//! rather than a proven defect — the body **did** run — so unlike the
//! collection and runtime guards this one is silent when strict is off.
//!
//! `yield` is deliberately **not** this check's business. A generator test is
//! refused at collection with a message about generators; reporting it here as
//! well would name one defect twice under two names.
//!
//! `return` and `return None` are both the rule being kept, so neither counts.
//! Flagging the explicit spelling would punish the clearer of the two.

use camino::Utf8Path;
use rustpython_parser::ast;

use crate::bridge::{RawViolation, ViolationKind};
use crate::python_ast;

/// Parse a Python test file and return `test-returns-value` violations.
pub fn collect_test_returns(path: &Utf8Path) -> Vec<RawViolation> {
    let (source, stmts) = match python_ast::parse_file(path) {
        Some(parsed) => parsed,
        None => return vec![],
    };
    collect_test_returns_from_ast(path, &source, &stmts)
}

/// Detect violations from a pre-parsed AST, so strict mode does not re-parse.
pub fn collect_test_returns_from_ast(
    path: &Utf8Path,
    source: &str,
    stmts: &[ast::Stmt],
) -> Vec<RawViolation> {
    let line_index = python_ast::build_line_index(source);
    let mut out = Vec::new();

    python_ast::walk_test_defs(stmts, |def, class| {
        let lines = walk_value_returns(def.body(), &line_index);
        if lines.is_empty() {
            return;
        }
        let node_id = match class {
            Some(cls) => format!("{path}::{}::{}", cls.name, def.name()),
            None => format!("{path}::{}", def.name()),
        };
        out.push(RawViolation {
            node_id,
            kind: ViolationKind::TestReturnsValue,
            detail: lines
                .iter()
                .map(std::string::ToString::to_string)
                .collect::<Vec<_>>()
                .join(" "),
        });
    });

    out
}

/// Walk a function body for `return <value>`, pruning nested functions.
fn walk_value_returns(body: &[ast::Stmt], line_index: &[u32]) -> Vec<u32> {
    let mut lines = Vec::new();
    let mut queue: Vec<&ast::Stmt> = body.iter().collect();

    while let Some(stmt) = queue.pop() {
        match stmt {
            // Prune: a nested function's return is its own. Counting it would
            // flag the correct use of a closure inside a test.
            ast::Stmt::FunctionDef(_) | ast::Stmt::AsyncFunctionDef(_) => continue,
            ast::Stmt::Return(ret) => {
                if let Some(value) = ret.value.as_deref()
                    && !is_none_literal(value)
                {
                    lines.push(python_ast::offset_to_line(
                        line_index,
                        ret.range.start().to_u32(),
                    ));
                }
            }
            _ => {}
        }
        queue.extend(python_ast::compound_children(stmt));
    }

    lines.sort_unstable();
    lines
}

/// True for the `None` constant, so `return None` is not reported.
const fn is_none_literal(value: &ast::Expr) -> bool {
    matches!(value, ast::Expr::Constant(c) if c.value.is_none())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::python_ast::tests::{temp_path, write_temp_py};

    fn found(src: &str) -> Vec<RawViolation> {
        let f = write_temp_py(src);
        collect_test_returns(&temp_path(&f))
    }

    #[test]
    fn bare_return_is_not_a_violation() {
        assert!(
            found("def test_it():\n    return\n").is_empty(),
            "a bare `return` yields None, which is the rule being kept rather than a breach of it"
        );
    }

    #[test]
    fn explicit_return_none_is_not_a_violation() {
        assert!(
            found("def test_it():\n    return None\n").is_empty(),
            "`return None` states the rule explicitly and must stay legal, or the check punishes the clearer of the two spellings"
        );
    }

    #[test]
    fn returning_a_comparison_is_a_violation() {
        let violations = found("def test_it():\n    return 1 == 2\n");
        assert_eq!(
            violations.len(),
            1,
            "an assertion written as a return is the canonical mistake this check exists for"
        );
        assert_eq!(
            violations[0].kind,
            ViolationKind::TestReturnsValue,
            "a wrong kind routes the violation to another message and another errors.md entry"
        );
        assert_eq!(
            violations[0].detail, "2",
            "the detail carries the line number, and the reporter prints it as the location the user must open"
        );
    }

    #[test]
    fn a_non_test_function_is_ignored() {
        assert!(
            found("def helper():\n    return 1\n").is_empty(),
            "the rule is about test functions; flagging helpers would make every module-level utility a violation"
        );
    }

    #[test]
    fn a_nested_function_is_pruned() {
        assert!(
            found("def test_it():\n    def inner():\n        return 1\n    inner()\n").is_empty(),
            "a nested function's return is its own, so counting it would flag the correct use of a closure"
        );
    }

    #[test]
    fn a_test_method_in_a_test_class_is_reported() {
        let violations = found("class TestFoo:\n    def test_bar(self):\n        return 1 == 2\n");
        assert_eq!(
            violations.len(),
            1,
            "walk_test_defs descends into Test* classes, and the same mistake in a method is the same mistake"
        );
        assert!(
            violations[0].node_id.contains("TestFoo::test_bar"),
            "the node id must locate the method rather than the class, or the user opens the wrong line"
        );
    }

    #[test]
    fn an_async_test_is_reported() {
        assert_eq!(
            found("async def test_it():\n    return 1 == 2\n").len(),
            1,
            "leaving the async form out would make the check depend on a keyword that is irrelevant to the rule"
        );
    }

    #[test]
    fn a_return_inside_a_branch_is_reported() {
        let violations = found("def test_it():\n    if True:\n        return 1 == 2\n");
        assert_eq!(
            violations.len(),
            1,
            "a return nested in a compound statement is still the function's return, and a top-level-only walk would miss the common case"
        );
        assert_eq!(
            violations[0].detail, "3",
            "the reported line must be the return itself, not the enclosing `if`"
        );
    }

    #[test]
    fn a_syntax_error_returns_empty() {
        assert!(
            found("def test_broken(\n").is_empty(),
            "an unparsable file is the parser's error to report; reporting here as well would double the diagnostic"
        );
    }

    #[test]
    fn a_generator_test_is_not_this_check() {
        assert!(
            found("def test_it():\n    yield\n").is_empty(),
            "a generator test is refused at collection with its own message, so flagging it here would name one defect twice"
        );
    }
}
