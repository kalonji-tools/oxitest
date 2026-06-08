//! Shared Python AST utilities for Rust-side analysis.
//!
//! Consolidates file reading, parsing, and naming predicates used by
//! [`crate::import_graph`] and [`crate::bare_asserts`], plus the pre-scan
//! check ([`prescan_with_ast`]) that lets the pipeline skip files with no
//! tests before invoking Python and optionally retains the parsed AST for
//! downstream reuse (e.g. bare-assert detection in strict mode).

use camino::Utf8Path;
use rustpython_parser::{ast, Parse};

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

/// Check whether a statement is a sync or async `test_*` function definition.
pub(crate) fn is_test_function(stmt: &ast::Stmt) -> bool {
    match stmt {
        ast::Stmt::FunctionDef(f) => is_test_fn(&f.name),
        ast::Stmt::AsyncFunctionDef(f) => is_test_fn(&f.name),
        _ => false,
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

    for stmt in &stmts {
        if is_test_function(stmt) {
            return Some(true);
        }
        if let ast::Stmt::ClassDef(cls) = stmt {
            if is_test_class(&cls.name) && cls.body.iter().any(is_test_function) {
                return Some(true);
            }
        }
    }

    Some(false)
}

/// Count the number of test functions in a parsed module.
#[cfg(test)]
pub(crate) fn count_tests(stmts: &[ast::Stmt]) -> usize {
    let mut count = 0;
    for stmt in stmts {
        if is_test_function(stmt) {
            count += 1;
        } else if let ast::Stmt::ClassDef(cls) = stmt {
            if is_test_class(&cls.name) {
                count += cls.body.iter().filter(|s| is_test_function(s)).count();
            }
        }
    }
    count
}

/// Count the parametrize case multiplier for a single function statement.
///
/// Inspects decorator lists for `oxi.parametrize(...)`, `oxi.mark.parametrize(...)`,
/// or `oxitest.mark.parametrize(...)` calls. When the second argument is a list
/// or tuple literal, returns the product of all detected case counts. Falls back
/// to 1 per undetectable decorator (dynamic expressions, name references).
///
/// Returns 1 if the function has no parametrize decorators.
pub(crate) fn count_parametrize_cases(stmt: &ast::Stmt) -> usize {
    let decorators = match stmt {
        ast::Stmt::FunctionDef(f) => &f.decorator_list,
        ast::Stmt::AsyncFunctionDef(f) => &f.decorator_list,
        _ => return 1,
    };

    let mut product = 1;
    for dec in decorators {
        if let ast::Expr::Call(call) = &dec {
            if is_parametrize_call(&call.func) {
                let cases = extract_case_count(&call.args);
                product *= cases;
            }
        }
    }
    product
}

/// Check if a call target is one of the recognized parametrize forms.
fn is_parametrize_call(func: &ast::Expr) -> bool {
    if let ast::Expr::Attribute(attr) = func {
        if attr.attr.as_str() == "parametrize" {
            if let ast::Expr::Name(n) = &*attr.value {
                let s = n.id.as_str();
                if s == "oxi" || s == "oxitest" {
                    return true;
                }
            }
            if let ast::Expr::Attribute(inner) = &*attr.value {
                if inner.attr.as_str() == "mark" {
                    if let ast::Expr::Name(n) = &*inner.value {
                        let s = n.id.as_str();
                        if s == "oxi" || s == "oxitest" {
                            return true;
                        }
                    }
                }
            }
        }
    }
    false
}

/// Extract the case count from parametrize arguments.
fn extract_case_count(args: &[ast::Expr]) -> usize {
    if args.len() < 2 {
        return 1;
    }
    match &args[1] {
        ast::Expr::List(list) => list.elts.len().max(1),
        ast::Expr::Tuple(tuple) => tuple.elts.len().max(1),
        _ => 1,
    }
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
    let ns = name_node.id.as_str();
    if ns != "oxi" && ns != "oxitest" {
        return None;
    }

    Some(mark_name.to_string())
}

/// Collect mark names from a Test* class's method decorators into `marks`.
fn collect_mark_names(stmt: &ast::Stmt, marks: &mut std::collections::BTreeSet<String>) {
    let ast::Stmt::ClassDef(cls) = stmt else {
        return;
    };
    if !is_test_class(&cls.name) {
        return;
    }
    for method in &cls.body {
        let decorators = match method {
            ast::Stmt::FunctionDef(f) if is_test_fn(&f.name) => &f.decorator_list,
            ast::Stmt::AsyncFunctionDef(f) if is_test_fn(&f.name) => &f.decorator_list,
            _ => continue,
        };
        for dec in decorators {
            if let Some(name) = extract_mark_name(dec) {
                marks.insert(name);
            }
        }
    }
}

/// Extract all unique mark names from test functions and Test* class methods
/// in the given statement list.
///
/// Skips `parametrize`. Returns a sorted, deduplicated `Vec<String>`.
pub(crate) fn extract_decorator_marks(stmts: &[ast::Stmt]) -> Vec<String> {
    let mut marks = std::collections::BTreeSet::new();

    for stmt in stmts {
        match stmt {
            ast::Stmt::FunctionDef(f) if is_test_fn(&f.name) => {
                for dec in &f.decorator_list {
                    if let Some(name) = extract_mark_name(dec) {
                        marks.insert(name);
                    }
                }
            }
            ast::Stmt::AsyncFunctionDef(f) if is_test_fn(&f.name) => {
                for dec in &f.decorator_list {
                    if let Some(name) = extract_mark_name(dec) {
                        marks.insert(name);
                    }
                }
            }
            cls @ ast::Stmt::ClassDef(_) => {
                collect_mark_names(cls, &mut marks);
            }
            _ => {}
        }
    }

    marks.into_iter().collect()
}

/// Extract public, non-test, non-dunder function names from a module's statements.
///
/// Includes top-level function definitions whose names:
/// - do not start with `_`
/// - do not start with `test_`
///
/// Returns a sorted `Vec<String>`.
pub(crate) fn extract_helpers(stmts: &[ast::Stmt]) -> Vec<String> {
    let mut names = std::collections::BTreeSet::new();

    for stmt in stmts {
        let fn_name = match stmt {
            ast::Stmt::FunctionDef(f) => f.name.as_str(),
            ast::Stmt::AsyncFunctionDef(f) => f.name.as_str(),
            _ => continue,
        };
        if fn_name.starts_with('_') {
            continue;
        }
        if is_test_fn(fn_name) {
            continue;
        }
        names.insert(fn_name.to_string());
    }

    names.into_iter().collect()
}

/// Marker metadata extracted from a decorator without Python import.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct PrescanMarker {
    pub(crate) name: String,
    pub(crate) has_dynamic_args: bool,
}

/// Per-test-function metadata extracted from AST without Python import.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct PrescanItem {
    pub(crate) fn_name: String,
    pub(crate) lineno: u32,
    pub(crate) is_async: bool,
    pub(crate) markers: Vec<PrescanMarker>,
    pub(crate) param_ids: Vec<String>,
    pub(crate) fixture_params: Vec<String>,
    pub(crate) is_class_method: bool,
    pub(crate) class_name: Option<String>,
}

/// Result of pre-scanning a Python file for test functions.
#[derive(Debug)]
pub(crate) enum PrescanResult {
    /// File has test functions; the parsed AST is available for reuse.
    HasTests {
        source: String,
        stmts: Vec<ast::Stmt>,
        /// Raw test function count (without parametrize expansion). Only read in tests.
        #[allow(dead_code)]
        test_count: usize,
        /// Test count adjusted for static parametrize multipliers.
        #[allow(dead_code)]
        adjusted_test_count: usize,
        /// Per-item metadata extracted from the AST.
        items: Vec<PrescanItem>,
        /// Whether the module uses dynamic patterns that prevent lazy collection.
        has_dynamic_collection: bool,
        /// Module-level marks from `oxi_mark = mark.NAME` assignments.
        module_markers: Vec<String>,
    },
    /// File has no test functions.
    NoTests,
    /// File could not be read or parsed (caller should fall through to Python).
    Unavailable,
}

/// Extract a `PrescanMarker` from a single decorator expression.
///
/// Reuses [`extract_mark_name`] to identify the mark, then checks whether any
/// arguments are non-literal (dynamic).
fn extract_prescan_marker(dec: &ast::Expr) -> Option<PrescanMarker> {
    let name = extract_mark_name(dec)?;
    let has_dynamic_args = match dec {
        ast::Expr::Call(call) => {
            call.args.iter().any(|a| !is_literal_expr(a))
                || call.keywords.iter().any(|kw| !is_literal_expr(&kw.value))
        }
        _ => false,
    };
    Some(PrescanMarker {
        name,
        has_dynamic_args,
    })
}

/// Check whether an expression is a literal (constant) value.
fn is_literal_expr(expr: &ast::Expr) -> bool {
    matches!(expr, ast::Expr::Constant(_))
}

/// Extract keyword argument names from `@oxi.parametrize(case1=..., case2=...)`.
fn extract_parametrize_kwarg_names(decorators: &[ast::Expr]) -> Vec<String> {
    let mut ids = Vec::new();
    for dec in decorators {
        if let ast::Expr::Call(call) = dec {
            if is_parametrize_call(&call.func) {
                for kw in &call.keywords {
                    if let Some(ref arg) = kw.arg {
                        ids.push(arg.to_string());
                    }
                }
            }
        }
    }
    ids
}

/// Extract parameter names that have a `Fixture[T]` annotation.
fn extract_fixture_param_names(args: &ast::Arguments) -> Vec<String> {
    let mut names = Vec::new();
    for arg_with_default in args.args.iter().chain(args.kwonlyargs.iter()) {
        if let Some(ref annotation) = arg_with_default.def.annotation {
            if is_fixture_annotation(annotation) {
                names.push(arg_with_default.def.arg.to_string());
            }
        }
    }
    names
}

/// Check whether an annotation expression is `Fixture[T]` or `oxitest.Fixture[T]`.
fn is_fixture_annotation(expr: &ast::Expr) -> bool {
    match expr {
        ast::Expr::Subscript(sub) => {
            match &*sub.value {
                // Fixture[T]
                ast::Expr::Name(n) => n.id.as_str() == "Fixture",
                // oxitest.Fixture[T]
                ast::Expr::Attribute(attr) => {
                    attr.attr.as_str() == "Fixture"
                        && matches!(&*attr.value, ast::Expr::Name(n) if n.id.as_str() == "oxitest")
                }
                _ => false,
            }
        }
        _ => false,
    }
}

/// Detect dynamic patterns that prevent lazy collection.
///
/// Scans top-level statements for:
/// - `exec()`, `eval()`, `globals()[]` calls
/// - `__getattr__` definitions
/// - star imports from non-stdlib modules
/// - `type()` metaclass creation
fn detect_dynamic_collection(stmts: &[ast::Stmt]) -> bool {
    for stmt in stmts {
        match stmt {
            ast::Stmt::Expr(expr) => {
                if is_dynamic_call(&expr.value) || is_globals_subscript(&expr.value) {
                    return true;
                }
            }
            ast::Stmt::Assign(assign) => {
                if is_dynamic_call(&assign.value)
                    || is_globals_subscript(&assign.value)
                    || is_type_metaclass_call(&assign.value)
                {
                    return true;
                }
                // Check if any target is globals()[...]
                for target in &assign.targets {
                    if is_globals_subscript(target) {
                        return true;
                    }
                }
            }
            ast::Stmt::FunctionDef(f) if f.name.as_str() == "__getattr__" => {
                return true;
            }
            ast::Stmt::AsyncFunctionDef(f) if f.name.as_str() == "__getattr__" => {
                return true;
            }
            ast::Stmt::ImportFrom(imp)
                if imp.names.len() == 1 && imp.names[0].name.as_str() == "*" =>
            {
                // Star import: from foo import *
                let module = imp.module.as_ref().map(|m| m.as_str()).unwrap_or("");
                if !is_stdlib_module(module) {
                    return true;
                }
            }
            _ => {}
        }
    }
    false
}

/// Check if an expression is a call to `exec()` or `eval()`.
fn is_dynamic_call(expr: &ast::Expr) -> bool {
    if let ast::Expr::Call(call) = expr {
        if let ast::Expr::Name(n) = &*call.func {
            let s = n.id.as_str();
            return s == "exec" || s == "eval";
        }
    }
    false
}

/// Check if an expression is `globals()[...]`.
fn is_globals_subscript(expr: &ast::Expr) -> bool {
    if let ast::Expr::Subscript(sub) = expr {
        if let ast::Expr::Call(call) = &*sub.value {
            if let ast::Expr::Name(n) = &*call.func {
                return n.id.as_str() == "globals";
            }
        }
    }
    false
}

/// Check if an expression is `type("Name", (bases,), {...})` — metaclass creation.
fn is_type_metaclass_call(expr: &ast::Expr) -> bool {
    if let ast::Expr::Call(call) = expr {
        if let ast::Expr::Name(n) = &*call.func {
            if n.id.as_str() == "type" && call.args.len() >= 3 {
                return true;
            }
        }
    }
    false
}

/// Quick check whether a module name belongs to the Python standard library.
fn is_stdlib_module(module: &str) -> bool {
    // Top-level module name only
    let top = module.split('.').next().unwrap_or(module);
    matches!(
        top,
        "os" | "sys"
            | "io"
            | "re"
            | "json"
            | "math"
            | "time"
            | "datetime"
            | "pathlib"
            | "collections"
            | "functools"
            | "itertools"
            | "typing"
            | "abc"
            | "copy"
            | "enum"
            | "dataclasses"
            | "contextlib"
            | "subprocess"
            | "threading"
            | "multiprocessing"
            | "unittest"
            | "logging"
            | "warnings"
            | "traceback"
            | "inspect"
            | "textwrap"
            | "string"
            | "struct"
            | "hashlib"
            | "hmac"
            | "secrets"
            | "tempfile"
            | "shutil"
            | "glob"
            | "fnmatch"
            | "stat"
            | "fileinput"
            | "csv"
            | "configparser"
            | "argparse"
            | "getopt"
            | "socket"
            | "http"
            | "urllib"
            | "email"
            | "html"
            | "xml"
            | "pdb"
            | "profile"
            | "timeit"
            | "dis"
            | "ast"
            | "types"
            | "weakref"
            | "array"
            | "bisect"
            | "heapq"
            | "queue"
            | "pprint"
            | "decimal"
            | "fractions"
            | "random"
            | "statistics"
            | "operator"
            | "pickle"
            | "shelve"
            | "sqlite3"
            | "zlib"
            | "gzip"
            | "bz2"
            | "lzma"
            | "zipfile"
            | "tarfile"
            | "signal"
            | "mmap"
            | "ctypes"
            | "concurrent"
            | "asyncio"
            | "token"
            | "tokenize"
            | "keyword"
            | "difflib"
            | "uuid"
            | "base64"
            | "binascii"
            | "codecs"
            | "locale"
            | "gettext"
            | "unicodedata"
            | "stringprep"
            | "readline"
            | "rlcompleter"
            | "platform"
            | "errno"
            | "faulthandler"
            | "atexit"
            | "builtins"
            | "_thread"
            | "__future__"
    )
}

/// Extract module-level marks from `oxi_mark = mark.NAME` or `oxi_mark = [mark.NAME, ...]`
/// assignments.
fn extract_module_marks(stmts: &[ast::Stmt]) -> Vec<String> {
    let mut marks = Vec::new();
    for stmt in stmts {
        if let ast::Stmt::Assign(assign) = stmt {
            // Check target is `oxi_mark`
            if assign.targets.len() == 1 {
                if let ast::Expr::Name(n) = &assign.targets[0] {
                    if n.id.as_str() == "oxi_mark" {
                        // Handle single mark: oxi_mark = mark.slow
                        if let Some(name) = extract_mark_from_value(&assign.value) {
                            marks.push(name);
                        }
                        // Handle list form: oxi_mark = [mark.slow, mark.fast]
                        if let ast::Expr::List(list) = &*assign.value {
                            for elt in &list.elts {
                                if let Some(name) = extract_mark_from_value(elt) {
                                    marks.push(name);
                                }
                            }
                        }
                        // Handle tuple form: oxi_mark = (mark.slow, mark.fast)
                        if let ast::Expr::Tuple(tuple) = &*assign.value {
                            for elt in &tuple.elts {
                                if let Some(name) = extract_mark_from_value(elt) {
                                    marks.push(name);
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    marks
}

/// Extract a mark name from `mark.NAME`, `oxi.mark.NAME`, or call forms thereof.
fn extract_mark_from_value(expr: &ast::Expr) -> Option<String> {
    // Handle call form: mark.slow() -> unwrap to mark.slow
    let attr_expr = match expr {
        ast::Expr::Call(call) => &*call.func,
        other => other,
    };
    if let ast::Expr::Attribute(attr) = attr_expr {
        let mark_name = attr.attr.as_str();
        match &*attr.value {
            // mark.NAME
            ast::Expr::Name(n) if n.id.as_str() == "mark" => {
                return Some(mark_name.to_string());
            }
            // oxi.mark.NAME or oxitest.mark.NAME
            ast::Expr::Attribute(inner) if inner.attr.as_str() == "mark" => {
                if let ast::Expr::Name(n) = &*inner.value {
                    let ns = n.id.as_str();
                    if ns == "oxi" || ns == "oxitest" {
                        return Some(mark_name.to_string());
                    }
                }
            }
            _ => {}
        }
    }
    None
}

/// Build a [`PrescanItem`] from any function-def node (sync or async).
///
/// `$f` must be either `ast::StmtFunctionDef` or `ast::StmtAsyncFunctionDef` — both
/// expose `.name`, `.decorator_list`, `.args`, and `.range`.
macro_rules! build_prescan_item {
    ($f:expr, $is_async:expr, $is_class_method:expr, $class_name:expr, $line_index:expr) => {{
        let markers: Vec<PrescanMarker> = $f
            .decorator_list
            .iter()
            .filter_map(extract_prescan_marker)
            .collect();
        let param_ids = extract_parametrize_kwarg_names(&$f.decorator_list);
        let fixture_params = extract_fixture_param_names(&$f.args);
        let lineno = offset_to_line($line_index, $f.range.start().to_u32());
        PrescanItem {
            fn_name: $f.name.to_string(),
            lineno,
            is_async: $is_async,
            markers,
            param_ids,
            fixture_params,
            is_class_method: $is_class_method,
            class_name: $class_name,
        }
    }};
}

/// Pre-scan a Python file and optionally retain the parsed AST for reuse.
///
/// When `keep_ast` is true and the file has tests, the parsed source and AST
/// are returned inside `HasTests` so downstream analysis (e.g. bare-assert
/// detection) can skip re-parsing. When `keep_ast` is false, the source and
/// stmts are empty (saves memory when the AST won't be reused).
pub(crate) fn prescan_with_ast(path: &Utf8Path, keep_ast: bool) -> PrescanResult {
    let parsed = match parse_file(path) {
        Some(p) => p,
        None => return PrescanResult::Unavailable,
    };

    let line_index = build_line_index(&parsed.0);
    let mut test_count = 0;
    let mut adjusted_test_count = 0;
    let mut items = Vec::new();

    for stmt in &parsed.1 {
        if is_test_function(stmt) {
            test_count += 1;
            adjusted_test_count += count_parametrize_cases(stmt);
            match stmt {
                ast::Stmt::FunctionDef(f) => {
                    items.push(build_prescan_item!(f, false, false, None, &line_index));
                }
                ast::Stmt::AsyncFunctionDef(f) => {
                    items.push(build_prescan_item!(f, true, false, None, &line_index));
                }
                _ => {}
            }
        } else if let ast::Stmt::ClassDef(cls) = stmt {
            if is_test_class(&cls.name) {
                let class_name = cls.name.to_string();
                for method in &cls.body {
                    if is_test_function(method) {
                        test_count += 1;
                        adjusted_test_count += count_parametrize_cases(method);
                        match method {
                            ast::Stmt::FunctionDef(f) => {
                                items.push(build_prescan_item!(
                                    f,
                                    false,
                                    true,
                                    Some(class_name.clone()),
                                    &line_index
                                ));
                            }
                            ast::Stmt::AsyncFunctionDef(f) => {
                                items.push(build_prescan_item!(
                                    f,
                                    true,
                                    true,
                                    Some(class_name.clone()),
                                    &line_index
                                ));
                            }
                            _ => {}
                        }
                    }
                }
            }
        }
    }

    if test_count == 0 {
        return PrescanResult::NoTests;
    }

    let has_dynamic_collection = detect_dynamic_collection(&parsed.1);
    let module_markers = extract_module_marks(&parsed.1);

    if keep_ast {
        PrescanResult::HasTests {
            source: parsed.0,
            stmts: parsed.1,
            test_count,
            adjusted_test_count,
            items,
            has_dynamic_collection,
            module_markers,
        }
    } else {
        PrescanResult::HasTests {
            source: String::new(),
            stmts: Vec::new(),
            test_count,
            adjusted_test_count,
            items,
            has_dynamic_collection,
            module_markers,
        }
    }
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

    // ── is_test_function ──────────────────────────────────────────────

    #[test]
    fn is_test_function_sync() {
        let f = write_temp_py("def test_foo(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert!(is_test_function(&stmts[0]));
    }

    #[test]
    fn is_test_function_async() {
        let f = write_temp_py("async def test_bar(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert!(is_test_function(&stmts[0]));
    }

    #[test]
    fn is_test_function_non_test() {
        let f = write_temp_py("def helper(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert!(!is_test_function(&stmts[0]));
    }

    #[test]
    fn is_test_function_class() {
        let f = write_temp_py("class TestFoo: pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert!(!is_test_function(&stmts[0]));
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

    // ── has_test_functions (prescan) ───────────────────────────────

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

    // ── prescan_with_ast test_count ─────────────────────────────────

    #[test]
    fn prescan_with_ast_populates_test_count() {
        let f = write_temp_py("def test_a(): pass\ndef test_b(): pass\ndef helper(): pass\n");
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests { test_count, .. } => assert_eq!(test_count, 2),
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_with_ast_test_count_includes_class_methods() {
        let f = write_temp_py(
            "def test_top(): pass\nclass TestGroup:\n    def test_a(self): pass\n    def test_b(self): pass\n",
        );
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests { test_count, .. } => assert_eq!(test_count, 3),
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    // ── count_parametrize_cases ─────────────────────────────────────

    #[test]
    fn parametrize_list_literal() {
        let f = write_temp_py(
            "import oxitest as oxi\n\n@oxi.parametrize(\"x\", [1, 2, 3])\ndef test_it(x): pass\n",
        );
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(count_parametrize_cases(&stmts[1]), 3);
    }

    #[test]
    fn parametrize_tuple_literal() {
        let f = write_temp_py(
            "import oxitest as oxi\n\n@oxi.parametrize(\"x\", (1, 2))\ndef test_it(x): pass\n",
        );
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(count_parametrize_cases(&stmts[1]), 2);
    }

    #[test]
    fn parametrize_dynamic_fallback() {
        let f = write_temp_py(
            "import oxitest as oxi\n\n@oxi.parametrize(\"x\", MY_CASES)\ndef test_it(x): pass\n",
        );
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(count_parametrize_cases(&stmts[1]), 1);
    }

    #[test]
    fn parametrize_stacked_product() {
        let f = write_temp_py(
            "import oxitest as oxi\n\n@oxi.parametrize(\"a\", [1, 2])\n@oxi.parametrize(\"b\", [10, 20, 30])\ndef test_it(a, b): pass\n",
        );
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        // stmts[0] = import, stmts[1] = decorated function
        assert_eq!(count_parametrize_cases(&stmts[1]), 6);
    }

    #[test]
    fn parametrize_no_decorator() {
        let f = write_temp_py("def test_plain(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(count_parametrize_cases(&stmts[0]), 1);
    }

    #[test]
    fn parametrize_non_parametrize_decorator_ignored() {
        let f = write_temp_py("import oxitest as oxi\n\n@oxi.mark.slow\ndef test_it(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(count_parametrize_cases(&stmts[1]), 1);
    }

    #[test]
    fn parametrize_mark_parametrize_form() {
        let f = write_temp_py(
            "import oxitest as oxi\n\n@oxi.mark.parametrize(\"x\", [1, 2, 3])\ndef test_it(x): pass\n",
        );
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        assert_eq!(count_parametrize_cases(&stmts[1]), 3);
    }

    #[test]
    fn prescan_with_ast_adjusted_count_with_parametrize() {
        let f = write_temp_py(
            "import oxitest as oxi\n\n@oxi.parametrize(\"x\", [1, 2, 3])\ndef test_it(x): pass\n\ndef test_plain(): pass\n",
        );
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests {
                test_count,
                adjusted_test_count,
                ..
            } => {
                assert_eq!(test_count, 2);
                assert_eq!(adjusted_test_count, 4); // 3 param cases + 1 plain
            }
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_with_ast_adjusted_count_no_parametrize() {
        let f = write_temp_py("def test_a(): pass\ndef test_b(): pass\n");
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests {
                test_count,
                adjusted_test_count,
                ..
            } => {
                assert_eq!(test_count, 2);
                assert_eq!(adjusted_test_count, 2);
            }
            other => panic!("expected HasTests, got {other:?}"),
        }
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
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        let helpers = extract_helpers(&stmts);
        assert!(helpers.contains(&"make_thing".to_string()));
        assert!(helpers.contains(&"another_helper".to_string()));
        assert!(!helpers.contains(&"_private".to_string()));
    }

    #[test]
    fn extract_helpers_skips_test_functions() {
        let f = write_temp_py("def test_foo(): pass\ndef helper(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        let helpers = extract_helpers(&stmts);
        assert!(!helpers.contains(&"test_foo".to_string()));
        assert!(helpers.contains(&"helper".to_string()));
    }

    #[test]
    fn extract_helpers_skips_dunder() {
        let f = write_temp_py("def __helpers_namespace__(): pass\ndef public_fn(): pass\n");
        let (_, stmts) = parse_file(&temp_path(&f)).unwrap();
        let helpers = extract_helpers(&stmts);
        assert!(!helpers.contains(&"__helpers_namespace__".to_string()));
        assert!(helpers.contains(&"public_fn".to_string()));
    }

    // ── prescan items ──────────────────────────────────────────────────────

    #[test]
    fn prescan_items_extracts_function_metadata() {
        let f = write_temp_py(
            "import oxitest as oxi\n\n@oxi.mark.slow\ndef test_sync(): pass\n\n@oxi.mark.xfail(reason=\"wip\")\nasync def test_async(): pass\n",
        );
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests { items, .. } => {
                assert_eq!(items.len(), 2);
                // sync function
                assert_eq!(items[0].fn_name, "test_sync");
                assert!(!items[0].is_async);
                assert!(!items[0].is_class_method);
                assert_eq!(items[0].markers.len(), 1);
                assert_eq!(items[0].markers[0].name, "slow");
                assert!(!items[0].markers[0].has_dynamic_args);
                // async function
                assert_eq!(items[1].fn_name, "test_async");
                assert!(items[1].is_async);
                assert_eq!(items[1].markers.len(), 1);
                assert_eq!(items[1].markers[0].name, "xfail");
                // reason="wip" is a literal string constant
                assert!(!items[1].markers[0].has_dynamic_args);
            }
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_items_extracts_class_methods() {
        let f = write_temp_py(
            "class TestGroup:\n    def test_a(self): pass\n    async def test_b(self): pass\n",
        );
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests { items, .. } => {
                assert_eq!(items.len(), 2);
                assert_eq!(items[0].fn_name, "test_a");
                assert!(items[0].is_class_method);
                assert_eq!(items[0].class_name, Some("TestGroup".to_string()));
                assert!(!items[0].is_async);

                assert_eq!(items[1].fn_name, "test_b");
                assert!(items[1].is_class_method);
                assert_eq!(items[1].class_name, Some("TestGroup".to_string()));
                assert!(items[1].is_async);
            }
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_items_extracts_parametrize_case_ids() {
        let f = write_temp_py(
            "import oxitest as oxi\n\n@oxi.parametrize(positive=1, negative=-1)\ndef test_it(x): pass\n",
        );
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests { items, .. } => {
                assert_eq!(items.len(), 1);
                assert_eq!(items[0].param_ids, vec!["positive", "negative"]);
            }
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_items_extracts_fixture_param_annotations() {
        let f = write_temp_py(
            "from oxitest import Fixture\n\ndef test_it(db: Fixture[str], name: str): pass\n",
        );
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests { items, .. } => {
                assert_eq!(items.len(), 1);
                assert_eq!(items[0].fixture_params, vec!["db"]);
            }
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_items_detects_dynamic_marker_args() {
        let f = write_temp_py(
            "import oxitest as oxi\n\n@oxi.mark.skip(when=SOME_VAR)\ndef test_it(): pass\n",
        );
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests { items, .. } => {
                assert_eq!(items.len(), 1);
                assert_eq!(items[0].markers.len(), 1);
                assert_eq!(items[0].markers[0].name, "skip");
                assert!(items[0].markers[0].has_dynamic_args);
            }
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_detects_dynamic_collection_exec() {
        let f = write_temp_py("exec('x = 1')\ndef test_it(): pass\n");
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests {
                has_dynamic_collection,
                ..
            } => assert!(has_dynamic_collection),
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_detects_dynamic_collection_getattr() {
        let f = write_temp_py("def __getattr__(name): ...\ndef test_it(): pass\n");
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests {
                has_dynamic_collection,
                ..
            } => assert!(has_dynamic_collection),
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_detects_dynamic_collection_star_import() {
        let f = write_temp_py("from mylib import *\ndef test_it(): pass\n");
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests {
                has_dynamic_collection,
                ..
            } => assert!(has_dynamic_collection),
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_no_dynamic_flag_for_clean_file() {
        let f = write_temp_py("import os\nfrom typing import *\ndef test_it(): pass\n");
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests {
                has_dynamic_collection,
                ..
            } => assert!(!has_dynamic_collection),
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_detects_dynamic_collection_globals_assignment() {
        let f = write_temp_py("globals()['test_dynamic'] = lambda: None\ndef test_it(): pass\n");
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests {
                has_dynamic_collection,
                ..
            } => assert!(has_dynamic_collection),
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_detects_type_metaclass_creation() {
        let f = write_temp_py(
            "TestDynamic = type('TestDynamic', (), {'test_it': lambda self: None})\ndef test_anchor(): pass\n",
        );
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests {
                has_dynamic_collection,
                ..
            } => assert!(has_dynamic_collection),
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_extracts_module_level_marks() {
        let f = write_temp_py(
            "import oxitest as oxi\nfrom oxitest import mark\n\noxi_mark = mark.slow\noxi_mark = oxi.mark.integration\n\ndef test_it(): pass\n",
        );
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests { module_markers, .. } => {
                assert_eq!(module_markers, vec!["slow", "integration"]);
            }
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_extracts_module_level_marks_call_form() {
        let f = write_temp_py(
            "import oxitest as oxi\nfrom oxitest import mark\n\noxi_mark = mark.slow()\n\ndef test_something():\n    pass\n",
        );
        let result = prescan_with_ast(&temp_path(&f), true);
        let PrescanResult::HasTests { module_markers, .. } = result else {
            panic!("expected HasTests");
        };
        assert_eq!(module_markers, vec!["slow"]);
    }

    #[test]
    fn prescan_detects_dynamic_collection_eval() {
        let content = r#"
result = eval("1 + 1")

def test_it():
    pass
"#;
        let f = write_temp_py(content);
        let result = prescan_with_ast(&temp_path(&f), true);
        let PrescanResult::HasTests {
            has_dynamic_collection,
            ..
        } = result
        else {
            panic!("expected HasTests");
        };
        assert!(has_dynamic_collection);
    }
}
