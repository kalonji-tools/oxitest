//! Shared Python AST utilities for Rust-side analysis.
//!
//! Consolidates file reading, parsing, and naming predicates used by
//! [`crate::import_graph`], [`crate::bare_asserts`], and [`crate::prescan`].

use camino::Utf8Path;
use rustpython_parser::{Parse, ast};

/// Read and parse a Python file into its AST statements.
///
/// Returns `None` on read error or syntax error — callers should fall through
/// to Python-side handling, which provides proper diagnostics.
pub(crate) fn parse_file(path: &Utf8Path) -> Option<(String, Vec<ast::Stmt>)> {
    let source = std::fs::read_to_string(path.as_std_path()).ok()?;
    let stmts = ast::Suite::parse(&source, path.as_str()).ok()?;
    Some((source, stmts))
}

/// Check whether a name follows the `test_*` convention.
pub(crate) fn is_test_fn(name: &str) -> bool {
    name.starts_with("test_")
}

/// Check whether a name follows the `Test*` class convention.
pub(crate) fn is_test_class(name: &str) -> bool {
    name.starts_with("Test")
}

/// Returns true for the `"oxi"` or `"oxitest"` namespace identifiers.
pub(crate) fn is_oxitest_namespace(s: &str) -> bool {
    s == "oxi" || s == "oxitest"
}

/// Unified view of `FunctionDef` and `AsyncFunctionDef`.
///
/// Erases the sync/async distinction so callers don't need separate match arms.
pub(crate) enum FnDef<'a> {
    Sync(&'a ast::StmtFunctionDef),
    Async(&'a ast::StmtAsyncFunctionDef),
}

impl<'a> FnDef<'a> {
    pub(crate) fn try_from_stmt(stmt: &'a ast::Stmt) -> Option<Self> {
        match stmt {
            ast::Stmt::FunctionDef(f) => Some(Self::Sync(f)),
            ast::Stmt::AsyncFunctionDef(f) => Some(Self::Async(f)),
            _ => None,
        }
    }

    pub(crate) fn name(&self) -> &str {
        match self {
            Self::Sync(f) => &f.name,
            Self::Async(f) => &f.name,
        }
    }

    pub(crate) fn body(&self) -> &[ast::Stmt] {
        match self {
            Self::Sync(f) => &f.body,
            Self::Async(f) => &f.body,
        }
    }

    pub(crate) fn decorator_list(&self) -> &[ast::Expr] {
        match self {
            Self::Sync(f) => &f.decorator_list,
            Self::Async(f) => &f.decorator_list,
        }
    }

    pub(crate) fn args(&self) -> &ast::Arguments {
        match self {
            Self::Sync(f) => &f.args,
            Self::Async(f) => &f.args,
        }
    }

    pub(crate) fn range(&self) -> rustpython_parser::text_size::TextRange {
        match self {
            Self::Sync(f) => f.range,
            Self::Async(f) => f.range,
        }
    }

    pub(crate) fn is_async(&self) -> bool {
        matches!(self, Self::Async(_))
    }
}

/// Walk top-level test functions and test-class methods, calling `visit` for each.
///
/// Handles the common "top-level `test_*` functions + `Test*` class methods" pattern
/// that every AST consumer repeats. Non-test functions, non-test classes, and
/// non-test methods inside test classes are all skipped.
pub(crate) fn walk_test_defs(
    stmts: &[ast::Stmt],
    mut visit: impl FnMut(&FnDef<'_>, Option<&ast::StmtClassDef>),
) {
    for stmt in stmts {
        if let Some(def) = FnDef::try_from_stmt(stmt) {
            if is_test_fn(def.name()) {
                visit(&def, None);
            }
        } else if let ast::Stmt::ClassDef(cls) = stmt
            && is_test_class(&cls.name)
        {
            for method in &cls.body {
                if let Some(def) = FnDef::try_from_stmt(method)
                    && is_test_fn(def.name())
                {
                    visit(&def, Some(cls));
                }
            }
        }
    }
}

/// Build an index mapping byte offsets to 1-based line numbers.
pub(crate) fn build_line_index(source: &str) -> Vec<u32> {
    let mut newlines = vec![0u32]; // line 1 starts at byte 0
    for (i, b) in source.bytes().enumerate() {
        if b == b'\n' {
            newlines.push(i as u32 + 1);
        }
    }
    newlines
}

/// Convert a byte offset to a 1-based line number using a pre-built index.
pub(crate) fn offset_to_line(line_index: &[u32], offset: u32) -> u32 {
    match line_index.binary_search(&offset) {
        Ok(i) => i as u32 + 1,
        Err(i) => i as u32,
    }
}

/// Yield the child statements of a compound statement.
///
/// For `if`/`while`/`for`: body + orelse.
/// For `try`/`try*`: body + handler bodies + orelse + finalbody.
/// For `with`/`class`/`match`/`function`: body.
/// For simple statements: empty.
pub(crate) fn compound_children(stmt: &ast::Stmt) -> Vec<&ast::Stmt> {
    match stmt {
        ast::Stmt::If(n) => chain(&n.body, &n.orelse),
        ast::Stmt::While(n) => chain(&n.body, &n.orelse),
        ast::Stmt::For(n) => chain(&n.body, &n.orelse),
        ast::Stmt::AsyncFor(n) => chain(&n.body, &n.orelse),
        ast::Stmt::With(n) => n.body.iter().collect(),
        ast::Stmt::AsyncWith(n) => n.body.iter().collect(),
        ast::Stmt::Try(n) => try_children(&n.body, &n.handlers, &n.orelse, &n.finalbody),
        ast::Stmt::TryStar(n) => try_children(&n.body, &n.handlers, &n.orelse, &n.finalbody),
        ast::Stmt::Match(n) => n.cases.iter().flat_map(|c| &c.body).collect(),
        ast::Stmt::ClassDef(n) => n.body.iter().collect(),
        ast::Stmt::FunctionDef(n) => n.body.iter().collect(),
        ast::Stmt::AsyncFunctionDef(n) => n.body.iter().collect(),
        _ => vec![],
    }
}

fn chain<'a>(a: &'a [ast::Stmt], b: &'a [ast::Stmt]) -> Vec<&'a ast::Stmt> {
    a.iter().chain(b.iter()).collect()
}

fn try_children<'a>(
    body: &'a [ast::Stmt],
    handlers: &'a [ast::ExceptHandler],
    orelse: &'a [ast::Stmt],
    finalbody: &'a [ast::Stmt],
) -> Vec<&'a ast::Stmt> {
    let mut stmts: Vec<&ast::Stmt> = Vec::new();
    stmts.extend(body);
    for handler in handlers {
        let ast::ExceptHandler::ExceptHandler(h) = handler;
        stmts.extend(&h.body);
    }
    stmts.extend(orelse);
    stmts.extend(finalbody);
    stmts
}

/// Check whether a Python file contains any test functions.
///
/// Returns `Some(true)` if tests found, `Some(false)` if none found.
/// Returns `None` on read error or syntax error (caller should fall through
/// to Python collection, which handles these cases with proper diagnostics).
#[cfg(test)]
pub(crate) fn has_test_functions(path: &Utf8Path) -> Option<bool> {
    let (_, stmts) = parse_file(path)?;
    let mut found = false;
    walk_test_defs(&stmts, |_, _| found = true);
    Some(found)
}

/// Count the number of test functions in a parsed module.
#[cfg(test)]
pub(crate) fn count_tests(stmts: &[ast::Stmt]) -> usize {
    let mut count = 0;
    walk_test_defs(stmts, |_, _| count += 1);
    count
}

/// Extract the mark name from a single decorator expression.
///
/// Recognises `oxi.mark.NAME` and `oxitest.mark.NAME` in both bare decorator
/// form (`@oxi.mark.slow`) and call form (`@oxi.mark.slow()`).
/// Returns `None` for `parametrize` or unrecognised patterns.
pub(crate) fn extract_mark_name(dec: &ast::Expr) -> Option<String> {
    // Unwrap call form: @oxi.mark.slow(...) → look at the func
    let attr_expr = match dec {
        ast::Expr::Call(call) => &call.func,
        other => other,
    };

    // Must be an attribute access: oxi.mark.NAME
    let ast::Expr::Attribute(outer) = attr_expr else {
        return None;
    };
    let mark_name = outer.attr.as_str();
    if mark_name == "parametrize" {
        return None;
    }

    // The value of `oxi.mark.NAME` is `oxi.mark` — another attribute
    let ast::Expr::Attribute(inner) = &*outer.value else {
        return None;
    };
    if inner.attr.as_str() != "mark" {
        return None;
    }

    // The value of `oxi.mark` must be `oxi` or `oxitest`
    let ast::Expr::Name(name_node) = &*inner.value else {
        return None;
    };
    if !is_oxitest_namespace(name_node.id.as_str()) {
        return None;
    }

    Some(mark_name.to_string())
}

/// Extract all unique mark names from test functions and Test* class methods
/// in the given statement list.
///
/// Skips `parametrize`. Returns a sorted, deduplicated `Vec<String>`.
pub(crate) fn extract_decorator_marks(stmts: &[ast::Stmt]) -> Vec<String> {
    let mut marks = std::collections::BTreeSet::new();
    walk_test_defs(stmts, |def, _| {
        for dec in def.decorator_list() {
            if let Some(name) = extract_mark_name(dec) {
                marks.insert(name);
            }
        }
    });
    marks.into_iter().collect()
}

/// Information about a helper function extracted from the AST.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct HelperInfo {
    /// The function name.
    pub name: String,
    /// The docstring, if present (first statement is a string literal).
    pub docstring: Option<String>,
    /// The formatted signature, e.g. `"make_db(name: str, shared: bool = False)"`.
    pub signature: String,
}

/// Extract public, non-test, non-dunder helper functions from a module's statements.
///
/// Includes top-level function definitions whose names:
/// - do not start with `_`
/// - do not start with `test_`
///
/// Each helper includes its name, docstring (if any), and formatted signature.
/// Results are sorted by name.
///
/// The `source` parameter is the original Python source text, used to extract
/// annotation and default-value text from AST ranges.
pub(crate) fn extract_helpers(stmts: &[ast::Stmt], source: &str) -> Vec<HelperInfo> {
    let mut helpers = std::collections::BTreeMap::new();
    for stmt in stmts {
        if let Some(def) = FnDef::try_from_stmt(stmt) {
            let fn_name = def.name();
            if !fn_name.starts_with('_') && !is_test_fn(fn_name) {
                let docstring = extract_fn_docstring(def.body());
                let signature = format_signature(fn_name, def.args(), source);
                helpers.insert(
                    fn_name.to_string(),
                    HelperInfo {
                        name: fn_name.to_string(),
                        docstring,
                        signature,
                    },
                );
            }
        }
    }
    helpers.into_values().collect()
}

/// Extract a docstring from a function body.
///
/// If the first statement is an expression statement containing a string literal,
/// that's the docstring (Python convention). Otherwise returns `None`.
/// Multi-line docstrings are captured in full with leading/trailing whitespace trimmed.
fn extract_fn_docstring(body: &[ast::Stmt]) -> Option<String> {
    if let Some(ast::Stmt::Expr(expr)) = body.first()
        && let ast::Expr::Constant(ast::ExprConstant {
            value: ast::Constant::Str(s),
            ..
        }) = &*expr.value
    {
        let trimmed = s.trim().to_string();
        if trimmed.is_empty() {
            return None;
        }
        return Some(trimmed);
    }
    None
}

/// Format a function signature string from its name and AST arguments.
///
/// Produces output like `"make_db(name: str, shared: bool = False)"`.
/// Uses the original source text to extract annotation and default-value text
/// via AST node ranges.
fn format_signature(fn_name: &str, args: &ast::Arguments, source: &str) -> String {
    let mut parts: Vec<String> = args
        .args
        .iter()
        .chain(&args.kwonlyargs)
        .map(|awd| format_param(awd, source))
        .collect();
    let _ = &mut parts; // suppress unused_mut if chain is empty
    format!("{}({})", fn_name, parts.join(", "))
}

/// Format a single parameter with optional annotation and default value.
fn format_param(awd: &ast::ArgWithDefault, source: &str) -> String {
    use rustpython_parser::ast::Ranged;

    let mut part = awd.def.arg.to_string();

    if let Some(ref annotation) = awd.def.annotation {
        let range = annotation.range();
        let start = range.start().to_usize();
        let end = range.end().to_usize();
        if start < source.len() && end <= source.len() {
            part.push_str(": ");
            part.push_str(&source[start..end]);
        }
    }

    if let Some(ref default) = awd.default {
        let range = default.range();
        let start = range.start().to_usize();
        let end = range.end().to_usize();
        if start < source.len() && end <= source.len() {
            part.push_str(" = ");
            part.push_str(&source[start..end]);
        }
    }

    part
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use camino::Utf8PathBuf;
    use std::io::Write;

    /// Create a temporary `.py` file with the given content.
    pub(crate) fn write_temp_py(content: &str) -> tempfile::NamedTempFile {
        let mut f = tempfile::Builder::new().suffix(".py").tempfile().unwrap();
        f.write_all(content.as_bytes()).unwrap();
        f
    }

    /// Convert a `NamedTempFile` path to a `Utf8PathBuf`.
    pub(crate) fn temp_path(f: &tempfile::NamedTempFile) -> Utf8PathBuf {
        Utf8PathBuf::from_path_buf(f.path().to_path_buf()).unwrap()
    }

    // ── parse_file ────────────────────────────────────────────────────

    #[test]
    fn parse_file_valid() {
        let f = write_temp_py("def test_foo():\n    pass\n");
        let result = parse_file(&temp_path(&f));
        assert!(result.is_some());
        let (source, stmts) = result.unwrap();
        assert!(source.contains("test_foo"));
        assert!(!stmts.is_empty());
    }

    #[test]
    fn parse_file_syntax_error() {
        let f = write_temp_py("def broken(\n");
        assert!(parse_file(&temp_path(&f)).is_none());
    }

    #[test]
    fn parse_file_nonexistent() {
        assert!(parse_file(Utf8Path::new("/nonexistent/file.py")).is_none());
    }

    // ── is_test_fn ────────────────────────────────────────────────────

    #[test]
    fn test_fn_positive() {
        assert!(is_test_fn("test_something"));
        assert!(is_test_fn("test_"));
    }

    #[test]
    fn test_fn_negative() {
        assert!(!is_test_fn("helper"));
        assert!(!is_test_fn("Test"));
        assert!(!is_test_fn("testing"));
        assert!(!is_test_fn(""));
    }

    // ── is_test_class ─────────────────────────────────────────────────

    #[test]
    fn test_class_positive() {
        assert!(is_test_class("TestFoo"));
        assert!(is_test_class("Test"));
        assert!(is_test_class("TestCase"));
    }

    #[test]
    fn test_class_negative() {
        assert!(!is_test_class("test_foo"));
        assert!(!is_test_class("Helper"));
        assert!(!is_test_class(""));
    }

    // ── build_line_index / offset_to_line ────────────────────────────

    #[test]
    fn line_index_single_line() {
        let idx = build_line_index("hello\n");
        assert_eq!(offset_to_line(&idx, 0), 1);
        assert_eq!(offset_to_line(&idx, 5), 1);
    }

    #[test]
    fn line_index_multi_line() {
        let idx = build_line_index("aaa\nbbb\nccc\n");
        assert_eq!(offset_to_line(&idx, 0), 1);
        assert_eq!(offset_to_line(&idx, 4), 2);
        assert_eq!(offset_to_line(&idx, 8), 3);
    }

    #[test]
    fn line_index_empty_source() {
        let idx = build_line_index("");
        assert_eq!(idx.len(), 1);
    }

    // ── compound_children ────────────────────────────────────────────

    #[test]
    fn compound_children_of_if() {
        let f = write_temp_py("if True:\n    x = 1\nelse:\n    y = 2\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        let children = compound_children(&stmts[0]);
        assert_eq!(children.len(), 2);
    }

    #[test]
    fn compound_children_of_try() {
        let f = write_temp_py("try:\n    a = 1\nexcept:\n    b = 2\nfinally:\n    c = 3\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        let children = compound_children(&stmts[0]);
        assert_eq!(children.len(), 3);
    }

    #[test]
    fn compound_children_of_simple_stmt() {
        let f = write_temp_py("x = 1\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        let children = compound_children(&stmts[0]);
        assert!(children.is_empty());
    }

    // ── has_test_functions ──────────────────────────────────────────

    #[test]
    fn prescan_empty_file() {
        let f = write_temp_py("");
        assert_eq!(has_test_functions(&temp_path(&f)), Some(false));
    }

    #[test]
    fn prescan_single_test_function() {
        let f = write_temp_py("def test_foo():\n    pass\n");
        assert_eq!(has_test_functions(&temp_path(&f)), Some(true));
    }

    #[test]
    fn prescan_multiple_test_functions() {
        let f = write_temp_py("def test_a(): pass\ndef test_b(): pass\ndef helper(): pass\n");
        assert_eq!(has_test_functions(&temp_path(&f)), Some(true));
    }

    #[test]
    fn prescan_async_test_function() {
        let f = write_temp_py("async def test_async(): pass\n");
        assert_eq!(has_test_functions(&temp_path(&f)), Some(true));
    }

    #[test]
    fn prescan_test_class_with_methods() {
        let f = write_temp_py(
            "class TestFoo:\n    def test_bar(self): pass\n    def test_baz(self): pass\n",
        );
        assert_eq!(has_test_functions(&temp_path(&f)), Some(true));
    }

    #[test]
    fn prescan_non_test_class_ignored() {
        let f = write_temp_py("class Helper:\n    def test_bar(self): pass\n");
        assert_eq!(has_test_functions(&temp_path(&f)), Some(false));
    }

    #[test]
    fn prescan_non_test_functions_ignored() {
        let f = write_temp_py("def helper(): pass\ndef setup(): pass\n");
        assert_eq!(has_test_functions(&temp_path(&f)), Some(false));
    }

    #[test]
    fn prescan_mixed_module_and_class() {
        let f = write_temp_py(
            "def test_top(): pass\nclass TestGroup:\n    def test_inner(self): pass\n",
        );
        assert_eq!(has_test_functions(&temp_path(&f)), Some(true));
    }

    #[test]
    fn prescan_syntax_error_returns_none() {
        let f = write_temp_py("def broken(\n");
        assert_eq!(has_test_functions(&temp_path(&f)), None);
    }

    #[test]
    fn prescan_nonexistent_file_returns_none() {
        assert_eq!(
            has_test_functions(Utf8Path::new("/nonexistent/file.py")),
            None,
        );
    }

    #[test]
    fn prescan_file_with_only_imports() {
        let f = write_temp_py("import os\nfrom pathlib import Path\n");
        assert_eq!(has_test_functions(&temp_path(&f)), Some(false));
    }

    // ── count_tests ──────────────────────────────────────────────────

    #[test]
    fn count_tests_single_function() {
        let f = write_temp_py("def test_foo(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(count_tests(&stmts), 1);
    }

    #[test]
    fn count_tests_multiple_functions() {
        let f = write_temp_py("def test_a(): pass\ndef test_b(): pass\ndef helper(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(count_tests(&stmts), 2);
    }

    #[test]
    fn count_tests_async_function() {
        let f = write_temp_py("async def test_async(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(count_tests(&stmts), 1);
    }

    #[test]
    fn count_tests_class_methods() {
        let f = write_temp_py(
            "class TestFoo:\n    def test_a(self): pass\n    def test_b(self): pass\n    def helper(self): pass\n",
        );
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(count_tests(&stmts), 2);
    }

    #[test]
    fn count_tests_mixed_module_and_class() {
        let f = write_temp_py(
            "def test_top(): pass\nclass TestGroup:\n    def test_inner(self): pass\n",
        );
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(count_tests(&stmts), 2);
    }

    #[test]
    fn count_tests_non_test_class_ignored() {
        let f = write_temp_py("class Helper:\n    def test_bar(self): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(count_tests(&stmts), 0);
    }

    #[test]
    fn count_tests_empty_file() {
        let f = write_temp_py("");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(count_tests(&stmts), 0);
    }

    #[test]
    fn count_tests_no_tests() {
        let f = write_temp_py("def helper(): pass\ndef setup(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(count_tests(&stmts), 0);
    }

    // ── extract_decorator_marks ──────────────────────────────────────────────

    #[test]
    fn extract_marks_from_decorator() {
        let f = write_temp_py("import oxitest as oxi\n\n@oxi.mark.slow\ndef test_it(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(extract_decorator_marks(&stmts), vec!["slow"]);
    }

    #[test]
    fn extract_marks_multiple() {
        let f = write_temp_py(
            "import oxitest as oxi\n\n@oxi.mark.slow\ndef test_a(): pass\n\n@oxi.mark.integration\ndef test_b(): pass\n",
        );
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        let marks = extract_decorator_marks(&stmts);
        assert!(marks.contains(&"slow".to_string()));
        assert!(marks.contains(&"integration".to_string()));
    }

    #[test]
    fn extract_marks_skip_parametrize() {
        let f = write_temp_py(
            "import oxitest as oxi\n\n@oxi.mark.parametrize(\"x\", [1, 2])\ndef test_it(x): pass\n",
        );
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(extract_decorator_marks(&stmts), Vec::<String>::new());
    }

    #[test]
    fn extract_marks_deduplicates() {
        let f = write_temp_py(
            "import oxitest as oxi\n\n@oxi.mark.slow\ndef test_a(): pass\n\n@oxi.mark.slow\ndef test_b(): pass\n",
        );
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        let marks = extract_decorator_marks(&stmts);
        assert_eq!(marks, vec!["slow"]);
    }

    #[test]
    fn extract_marks_from_class_methods() {
        let f = write_temp_py(
            "import oxitest as oxi\n\nclass TestGroup:\n    @oxi.mark.slow\n    def test_method(self): pass\n",
        );
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        let marks = extract_decorator_marks(&stmts);
        assert_eq!(marks, vec!["slow"]);
    }

    #[test]
    fn extract_marks_oxitest_prefix() {
        let f = write_temp_py("import oxitest\n\n@oxitest.mark.slow\ndef test_it(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(extract_decorator_marks(&stmts), vec!["slow"]);
    }

    #[test]
    fn extract_marks_call_form() {
        // @oxi.mark.slow() as a call (with no args)
        let f = write_temp_py("import oxitest as oxi\n\n@oxi.mark.slow()\ndef test_it(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(extract_decorator_marks(&stmts), vec!["slow"]);
    }

    // ── extract_helpers ──────────────────────────────────────────────────────

    #[test]
    fn extract_helpers_public_functions() {
        let f = write_temp_py(
            "def make_thing(): pass\ndef _private(): pass\ndef another_helper(): pass\n",
        );
        let (source, stmts) = parse_file(&temp_path(&f)).unwrap();
        let helpers = extract_helpers(&stmts, &source);
        let names: Vec<&str> = helpers.iter().map(|h| h.name.as_str()).collect();
        assert!(
            names.contains(&"make_thing"),
            "public helper 'make_thing' should be included"
        );
        assert!(
            names.contains(&"another_helper"),
            "public helper 'another_helper' should be included"
        );
        assert!(
            !names.contains(&"_private"),
            "private function '_private' should be excluded"
        );
    }

    #[test]
    fn extract_helpers_skips_test_functions() {
        let f = write_temp_py("def test_foo(): pass\ndef helper(): pass\n");
        let (source, stmts) = parse_file(&temp_path(&f)).unwrap();
        let helpers = extract_helpers(&stmts, &source);
        let names: Vec<&str> = helpers.iter().map(|h| h.name.as_str()).collect();
        assert!(
            !names.contains(&"test_foo"),
            "test function 'test_foo' should be excluded"
        );
        assert!(
            names.contains(&"helper"),
            "helper function 'helper' should be included"
        );
    }

    #[test]
    fn extract_helpers_skips_dunder() {
        let f = write_temp_py("def __helpers_namespace__(): pass\ndef public_fn(): pass\n");
        let (source, stmts) = parse_file(&temp_path(&f)).unwrap();
        let helpers = extract_helpers(&stmts, &source);
        let names: Vec<&str> = helpers.iter().map(|h| h.name.as_str()).collect();
        assert!(
            !names.contains(&"__helpers_namespace__"),
            "dunder function should be excluded"
        );
        assert!(
            names.contains(&"public_fn"),
            "public function should be included"
        );
    }

    #[test]
    fn extract_helpers_with_docstring() {
        let f =
            write_temp_py("def make_db():\n    \"\"\"Create a test database.\"\"\"\n    pass\n");
        let (source, stmts) = parse_file(&temp_path(&f)).unwrap();
        let helpers = extract_helpers(&stmts, &source);
        assert_eq!(helpers.len(), 1, "should extract exactly one helper");
        assert_eq!(
            helpers[0].docstring.as_deref(),
            Some("Create a test database."),
            "docstring should be extracted and trimmed"
        );
    }

    #[test]
    fn extract_helpers_without_docstring() {
        let f = write_temp_py("def make_db():\n    pass\n");
        let (source, stmts) = parse_file(&temp_path(&f)).unwrap();
        let helpers = extract_helpers(&stmts, &source);
        assert_eq!(helpers.len(), 1, "should extract exactly one helper");
        assert_eq!(
            helpers[0].docstring, None,
            "function without docstring should return None"
        );
    }

    #[test]
    fn extract_helpers_multiline_docstring() {
        let f = write_temp_py(
            "def make_db():\n    \"\"\"\n    Create a test database.\n\n    Returns a connection object.\n    \"\"\"\n    pass\n",
        );
        let (source, stmts) = parse_file(&temp_path(&f)).unwrap();
        let helpers = extract_helpers(&stmts, &source);
        assert_eq!(helpers.len(), 1, "should extract exactly one helper");
        let doc = helpers[0]
            .docstring
            .as_ref()
            .expect("multiline docstring should be captured");
        assert!(
            doc.contains("Create a test database."),
            "docstring should contain first line content"
        );
        assert!(
            doc.contains("Returns a connection object."),
            "docstring should contain subsequent content"
        );
    }

    #[test]
    fn extract_helpers_signature_with_params() {
        let f = write_temp_py("def make_db(name: str, shared: bool):\n    pass\n");
        let (source, stmts) = parse_file(&temp_path(&f)).unwrap();
        let helpers = extract_helpers(&stmts, &source);
        assert_eq!(helpers.len(), 1, "should extract exactly one helper");
        assert_eq!(
            helpers[0].signature, "make_db(name: str, shared: bool)",
            "signature should include parameter names and annotations"
        );
    }

    #[test]
    fn extract_helpers_signature_no_params() {
        let f = write_temp_py("def make_db():\n    pass\n");
        let (source, stmts) = parse_file(&temp_path(&f)).unwrap();
        let helpers = extract_helpers(&stmts, &source);
        assert_eq!(helpers.len(), 1, "should extract exactly one helper");
        assert_eq!(
            helpers[0].signature, "make_db()",
            "no-param function should have empty parens"
        );
    }

    #[test]
    fn extract_helpers_signature_defaults() {
        let f = write_temp_py("def make_db(name: str, shared: bool = False):\n    pass\n");
        let (source, stmts) = parse_file(&temp_path(&f)).unwrap();
        let helpers = extract_helpers(&stmts, &source);
        assert_eq!(helpers.len(), 1, "should extract exactly one helper");
        assert_eq!(
            helpers[0].signature, "make_db(name: str, shared: bool = False)",
            "default values should be included in signature"
        );
    }

    // ── FnDef ──────────────────────────────────────────────────────────

    #[test]
    fn fn_def_from_sync_function() {
        let f = write_temp_py("def foo(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        let def = FnDef::try_from_stmt(&stmts[0]).unwrap();
        assert_eq!(def.name(), "foo");
        assert!(!def.is_async());
    }

    #[test]
    fn fn_def_from_async_function() {
        let f = write_temp_py("async def bar(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        let def = FnDef::try_from_stmt(&stmts[0]).unwrap();
        assert_eq!(def.name(), "bar");
        assert!(def.is_async());
    }

    #[test]
    fn fn_def_from_non_function_returns_none() {
        let f = write_temp_py("x = 1\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert!(FnDef::try_from_stmt(&stmts[0]).is_none());
    }

    #[test]
    fn fn_def_from_class_returns_none() {
        let f = write_temp_py("class Foo: pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert!(FnDef::try_from_stmt(&stmts[0]).is_none());
    }

    // ── walk_test_defs ─────────────────────────────────────────────────

    #[test]
    fn walk_test_defs_top_level_functions() {
        let f = write_temp_py("def test_a(): pass\nasync def test_b(): pass\ndef helper(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        let mut visited = vec![];
        walk_test_defs(&stmts, |def, cls| {
            visited.push((def.name().to_string(), def.is_async(), cls.is_some()));
        });
        assert_eq!(
            visited,
            vec![
                ("test_a".into(), false, false),
                ("test_b".into(), true, false),
            ]
        );
    }

    #[test]
    fn walk_test_defs_class_methods() {
        let f = write_temp_py(
            "class TestGroup:\n    def test_a(self): pass\n    async def test_b(self): pass\n    def helper(self): pass\n",
        );
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        let mut visited = vec![];
        walk_test_defs(&stmts, |def, cls| {
            visited.push((def.name().to_string(), cls.unwrap().name.to_string()));
        });
        assert_eq!(
            visited,
            vec![
                ("test_a".into(), "TestGroup".into()),
                ("test_b".into(), "TestGroup".into()),
            ]
        );
    }

    #[test]
    fn walk_test_defs_skips_non_test_class() {
        let f = write_temp_py("class Helper:\n    def test_method(self): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        let mut count = 0;
        walk_test_defs(&stmts, |_, _| count += 1);
        assert_eq!(count, 0);
    }

    #[test]
    fn walk_test_defs_mixed() {
        let f = write_temp_py(
            "def test_top(): pass\nclass TestGroup:\n    def test_inner(self): pass\ndef not_a_test(): pass\n",
        );
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        let mut names = vec![];
        walk_test_defs(&stmts, |def, _| names.push(def.name().to_string()));
        assert_eq!(names, vec!["test_top", "test_inner"]);
    }
}
