//! Prescan engine — AST-based metadata extraction for lazy collection.
//!
//! Scans Python files without importing them, extracting per-test metadata
//! ([`PrescanItem`]) that the pipeline uses for filtering before import.
//! Heavy work lives here; leaf AST utilities live in [`crate::python_ast`].

use camino::{Utf8Path, Utf8PathBuf};
use rustpython_parser::ast;

use crate::python_ast;

// ── Public types ────────────────────────────────────────────────────────

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
    /// Estimated execution time in milliseconds, derived from AST analysis.
    pub(crate) body_weight_ms: f64,
}

/// Per-module prescan result used in the pipeline state.
#[derive(Debug)]
pub(crate) struct PrescanModule {
    pub(crate) path: Utf8PathBuf,
    pub(crate) items: Vec<PrescanItem>,
    pub(crate) has_dynamic_collection: bool,
}

/// Payload extracted from a Python file that has test functions.
#[derive(Debug)]
pub(crate) struct PrescanPayload {
    pub(crate) source: String,
    pub(crate) stmts: Vec<ast::Stmt>,
    pub(crate) items: Vec<PrescanItem>,
    pub(crate) has_dynamic_collection: bool,
    pub(crate) module_markers: Vec<String>,
}

/// Result of pre-scanning a Python file for test functions.
#[derive(Debug)]
pub(crate) enum PrescanResult {
    /// File has test functions; the parsed AST is available for reuse.
    HasTests(PrescanPayload),
    /// File has no test functions.
    NoTests,
    /// File could not be read or parsed (caller should fall through to Python).
    Unavailable,
}

// ── Marker helpers ──────────────────────────────────────────────────────

/// Extract a `PrescanMarker` from a single decorator expression.
///
/// Reuses [`python_ast::extract_mark_name`] to identify the mark, then checks whether any
/// arguments are non-literal (dynamic).
fn extract_prescan_marker(dec: &ast::Expr) -> Option<PrescanMarker> {
    let name = python_ast::extract_mark_name(dec)?;
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

// ── Dynamic collection detection ────────────────────────────────────────

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

// ── Module marks ────────────────────────────────────────────────────────

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

// ── Heavy imports & body weight ─────────────────────────────────────────

/// Heavy-import packages whose presence in a module adds weight to every test.
const HEAVY_IMPORT_PACKAGES: &[&str] = &[
    "requests",
    "httpx",
    "aiohttp",
    "sqlalchemy",
    "django",
    "boto3",
    "psycopg2",
    "pymongo",
    "selenium",
    "playwright",
];

/// Detect `time.sleep(N)` or `asyncio.sleep(N)` calls from an expression.
///
/// Accepts a bare `Expr` (the call expression itself, already unwrapped from any
/// surrounding `Await` or `Stmt::Expr` by the caller).
///
/// Returns the sleep weight in milliseconds:
/// - Literal float/int N → N × 1000 ms
/// - Dynamic arg → 50 ms
/// - No sleep call → 0 ms
fn detect_sleep_call(expr: &ast::Expr) -> f64 {
    let call = match expr {
        ast::Expr::Call(c) => c,
        _ => return 0.0,
    };

    // Match `time.sleep(...)` or `asyncio.sleep(...)`
    let is_known_sleep = match &*call.func {
        ast::Expr::Attribute(attr) => {
            attr.attr.as_str() == "sleep"
                && match &*attr.value {
                    ast::Expr::Name(name) => {
                        if name.id.as_str() != "time" && name.id.as_str() != "asyncio" {
                            return 0.0;
                        }
                        true
                    }
                    _ => false,
                }
        }
        _ => false,
    };

    if !is_known_sleep {
        return 0.0;
    }

    // Evaluate the argument
    if let Some(arg) = call.args.first() {
        match arg {
            ast::Expr::Constant(c) => match &c.value {
                ast::Constant::Float(f) => return f * 1000.0,
                ast::Constant::Int(i) => {
                    // Convert BigInt to f64
                    let n: f64 = i.to_string().parse().unwrap_or(0.0);
                    return n * 1000.0;
                }
                _ => return 50.0,
            },
            _ => return 50.0,
        }
    }
    50.0
}

/// Compute the body weight for a single test function body.
///
/// Formula per test:
/// ```text
/// body_weight_ms = 2.0 (base)
///     + sleep_weight (from body walk)
///     + if is_async { 10.0 } else { 0.0 }
///     + fixture_params.len() * 3.0
///     + stmt_count / 10.0
///     + heavy_import_weight (20.0 if module imports a heavy package)
/// ```
fn compute_body_weight(
    body: &[ast::Stmt],
    is_async: bool,
    fixture_count: usize,
    heavy_import_weight: f64,
) -> f64 {
    let mut sleep_weight = 0.0;
    let mut stmt_count = 0usize;

    let mut queue: Vec<&ast::Stmt> = body.iter().collect();
    while let Some(stmt) = queue.pop() {
        // Prune nested functions
        match stmt {
            ast::Stmt::FunctionDef(_) | ast::Stmt::AsyncFunctionDef(_) => continue,
            _ => {}
        }
        stmt_count += 1;
        // Detect sleep in both `time.sleep(N)` and `await asyncio.sleep(N)`
        if let ast::Stmt::Expr(expr_stmt) = stmt {
            let call_expr = match &*expr_stmt.value {
                ast::Expr::Await(aw) => &*aw.value,
                other => other,
            };
            sleep_weight += detect_sleep_call(call_expr);
        }
        queue.extend(python_ast::compound_children(stmt));
    }

    2.0 + sleep_weight
        + if is_async { 10.0 } else { 0.0 }
        + fixture_count as f64 * 3.0
        + stmt_count as f64 / 10.0
        + heavy_import_weight
}

/// Detect heavy third-party imports in the top-level module statements.
///
/// Returns 20.0 if any heavy package is found, 0.0 otherwise.
pub(crate) fn detect_heavy_imports(stmts: &[ast::Stmt]) -> f64 {
    for stmt in stmts {
        match stmt {
            ast::Stmt::Import(imp) => {
                for alias in &imp.names {
                    let top = alias.name.as_str().split('.').next().unwrap_or("");
                    if HEAVY_IMPORT_PACKAGES.contains(&top) {
                        return 20.0;
                    }
                }
            }
            ast::Stmt::ImportFrom(imp) => {
                let module = imp.module.as_ref().map(|m| m.as_str()).unwrap_or("");
                let top = module.split('.').next().unwrap_or("");
                if HEAVY_IMPORT_PACKAGES.contains(&top) {
                    return 20.0;
                }
            }
            _ => {}
        }
    }
    0.0
}

// ── build_prescan_item macro ────────────────────────────────────────────

/// Build a [`PrescanItem`] from any function-def node (sync or async).
///
/// `$f` must be either `ast::StmtFunctionDef` or `ast::StmtAsyncFunctionDef` — both
/// expose `.name`, `.decorator_list`, `.args`, and `.range`.
macro_rules! build_prescan_item {
    ($f:expr, $is_async:expr, $is_class_method:expr, $class_name:expr, $line_index:expr, $heavy_import_weight:expr) => {{
        let markers: Vec<PrescanMarker> = $f
            .decorator_list
            .iter()
            .filter_map(extract_prescan_marker)
            .collect();
        let param_ids = extract_parametrize_kwarg_names(&$f.decorator_list);
        let fixture_params = extract_fixture_param_names(&$f.args);
        let lineno = python_ast::offset_to_line($line_index, $f.range.start().to_u32());
        let body_weight_ms = compute_body_weight(
            &$f.body,
            $is_async,
            fixture_params.len(),
            $heavy_import_weight,
        );
        PrescanItem {
            fn_name: $f.name.to_string(),
            lineno,
            is_async: $is_async,
            markers,
            param_ids,
            fixture_params,
            is_class_method: $is_class_method,
            class_name: $class_name,
            body_weight_ms,
        }
    }};
}

// ── Main entry point ────────────────────────────────────────────────────

/// Pre-scan a Python file and optionally retain the parsed AST for reuse.
///
/// When `keep_ast` is true and the file has tests, the parsed source and AST
/// are returned inside `HasTests` so downstream analysis (e.g. bare-assert
/// detection) can skip re-parsing. When `keep_ast` is false, the source and
/// stmts are empty (saves memory when the AST won't be reused).
pub(crate) fn prescan_with_ast(path: &Utf8Path, keep_ast: bool) -> PrescanResult {
    let parsed = match python_ast::parse_file(path) {
        Some(p) => p,
        None => return PrescanResult::Unavailable,
    };

    let line_index = python_ast::build_line_index(&parsed.0);
    let mut test_count = 0;
    let mut items = Vec::new();

    let heavy_import_weight = detect_heavy_imports(&parsed.1);

    for stmt in &parsed.1 {
        if python_ast::is_test_function(stmt) {
            test_count += 1;
            match stmt {
                ast::Stmt::FunctionDef(f) => {
                    items.push(build_prescan_item!(
                        f,
                        false,
                        false,
                        None,
                        &line_index,
                        heavy_import_weight
                    ));
                }
                ast::Stmt::AsyncFunctionDef(f) => {
                    items.push(build_prescan_item!(
                        f,
                        true,
                        false,
                        None,
                        &line_index,
                        heavy_import_weight
                    ));
                }
                _ => {}
            }
        } else if let ast::Stmt::ClassDef(cls) = stmt {
            if python_ast::is_test_class(&cls.name) {
                let class_name = cls.name.to_string();
                for method in &cls.body {
                    if python_ast::is_test_function(method) {
                        test_count += 1;
                        match method {
                            ast::Stmt::FunctionDef(f) => {
                                items.push(build_prescan_item!(
                                    f,
                                    false,
                                    true,
                                    Some(class_name.clone()),
                                    &line_index,
                                    heavy_import_weight
                                ));
                            }
                            ast::Stmt::AsyncFunctionDef(f) => {
                                items.push(build_prescan_item!(
                                    f,
                                    true,
                                    true,
                                    Some(class_name.clone()),
                                    &line_index,
                                    heavy_import_weight
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
        PrescanResult::HasTests(PrescanPayload {
            source: parsed.0,
            stmts: parsed.1,
            items,
            has_dynamic_collection,
            module_markers,
        })
    } else {
        PrescanResult::HasTests(PrescanPayload {
            source: String::new(),
            stmts: Vec::new(),
            items,
            has_dynamic_collection,
            module_markers,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::python_ast::tests::{temp_path, write_temp_py};

    // ── prescan items ──────────────────────────────────────────────────────

    #[test]
    fn prescan_items_extracts_function_metadata() {
        let f = write_temp_py(
            "import oxitest as oxi\n\n@oxi.mark.slow\ndef test_sync(): pass\n\n@oxi.mark.xfail(reason=\"wip\")\nasync def test_async(): pass\n",
        );
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests(p) => {
                assert_eq!(p.items.len(), 2);
                // sync function
                assert_eq!(p.items[0].fn_name, "test_sync");
                assert!(!p.items[0].is_async);
                assert!(!p.items[0].is_class_method);
                assert_eq!(p.items[0].markers.len(), 1);
                assert_eq!(p.items[0].markers[0].name, "slow");
                assert!(!p.items[0].markers[0].has_dynamic_args);
                // async function
                assert_eq!(p.items[1].fn_name, "test_async");
                assert!(p.items[1].is_async);
                assert_eq!(p.items[1].markers.len(), 1);
                assert_eq!(p.items[1].markers[0].name, "xfail");
                // reason="wip" is a literal string constant
                assert!(!p.items[1].markers[0].has_dynamic_args);
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
            PrescanResult::HasTests(p) => {
                assert_eq!(p.items.len(), 2);
                assert_eq!(p.items[0].fn_name, "test_a");
                assert!(p.items[0].is_class_method);
                assert_eq!(p.items[0].class_name, Some("TestGroup".to_string()));
                assert!(!p.items[0].is_async);

                assert_eq!(p.items[1].fn_name, "test_b");
                assert!(p.items[1].is_class_method);
                assert_eq!(p.items[1].class_name, Some("TestGroup".to_string()));
                assert!(p.items[1].is_async);
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
            PrescanResult::HasTests(p) => {
                assert_eq!(p.items.len(), 1);
                assert_eq!(p.items[0].param_ids, vec!["positive", "negative"]);
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
            PrescanResult::HasTests(p) => {
                assert_eq!(p.items.len(), 1);
                assert_eq!(p.items[0].fixture_params, vec!["db"]);
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
            PrescanResult::HasTests(p) => {
                assert_eq!(p.items.len(), 1);
                assert_eq!(p.items[0].markers.len(), 1);
                assert_eq!(p.items[0].markers[0].name, "skip");
                assert!(p.items[0].markers[0].has_dynamic_args);
            }
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_detects_dynamic_collection_exec() {
        let f = write_temp_py("exec('x = 1')\ndef test_it(): pass\n");
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests(p) => assert!(p.has_dynamic_collection),
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_detects_dynamic_collection_getattr() {
        let f = write_temp_py("def __getattr__(name): ...\ndef test_it(): pass\n");
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests(p) => assert!(p.has_dynamic_collection),
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_detects_dynamic_collection_star_import() {
        let f = write_temp_py("from mylib import *\ndef test_it(): pass\n");
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests(p) => assert!(p.has_dynamic_collection),
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_no_dynamic_flag_for_clean_file() {
        let f = write_temp_py("import os\nfrom typing import *\ndef test_it(): pass\n");
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests(p) => assert!(!p.has_dynamic_collection),
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn prescan_detects_dynamic_collection_globals_assignment() {
        let f = write_temp_py("globals()['test_dynamic'] = lambda: None\ndef test_it(): pass\n");
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests(p) => assert!(p.has_dynamic_collection),
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
            PrescanResult::HasTests(p) => assert!(p.has_dynamic_collection),
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
            PrescanResult::HasTests(p) => {
                assert_eq!(p.module_markers, vec!["slow", "integration"]);
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
        let PrescanResult::HasTests(p) = result else {
            panic!("expected HasTests");
        };
        assert_eq!(p.module_markers, vec!["slow"]);
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
        let PrescanResult::HasTests(p) = result else {
            panic!("expected HasTests");
        };
        assert!(p.has_dynamic_collection);
    }

    // ── body_weight_ms ──────────────────────────────────────────────

    fn get_item_weight(content: &str) -> f64 {
        let f = write_temp_py(content);
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests(p) => {
                assert!(!p.items.is_empty(), "expected at least one test item");
                p.items[0].body_weight_ms
            }
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn body_weight_base_only() {
        // Minimal test: base = 2.0, no async, no fixtures, 1 stmt (pass), no heavy imports
        // 2.0 + 0.0 + 0.0 + 0.0 + 1/10 = 2.1
        let w = get_item_weight("def test_it():\n    pass\n");
        assert!((w - 2.1).abs() < 0.01, "expected ~2.1, got {w}");
    }

    #[test]
    fn body_weight_sleep_literal_int() {
        // time.sleep(2) -> 2000ms sleep weight; pass is 1 stmt
        // 2.0 + 2000.0 + 0.0 + 0.0 + 2/10 = 2002.2
        let w = get_item_weight("import time\ndef test_it():\n    time.sleep(2)\n    pass\n");
        assert!((w - 2002.2).abs() < 0.1, "expected ~2002.2, got {w}");
    }

    #[test]
    fn body_weight_sleep_literal_float() {
        // time.sleep(0.5) -> 500ms sleep weight; pass is 1 stmt
        // 2.0 + 500.0 + 0.0 + 0.0 + 2/10 = 502.2
        let w = get_item_weight("import time\ndef test_it():\n    time.sleep(0.5)\n    pass\n");
        assert!((w - 502.2).abs() < 0.1, "expected ~502.2, got {w}");
    }

    #[test]
    fn body_weight_sleep_dynamic_arg() {
        // time.sleep(DELAY) -> 50ms dynamic sleep weight; pass is 1 stmt
        // 2.0 + 50.0 + 0.0 + 0.0 + 2/10 = 52.2
        let w = get_item_weight("import time\ndef test_it():\n    time.sleep(DELAY)\n    pass\n");
        assert!((w - 52.2).abs() < 0.1, "expected ~52.2, got {w}");
    }

    #[test]
    fn body_weight_async_bonus() {
        // async test: base + async bonus + pass stmt
        // 2.0 + 10.0 + 0.0 + 0.0 + 1/10 = 12.1
        let w = get_item_weight("async def test_it():\n    pass\n");
        assert!((w - 12.1).abs() < 0.01, "expected ~12.1, got {w}");
    }

    #[test]
    fn body_weight_fixture_params() {
        // 2 fixtures: 2.0 + 0.0 + 0.0 + 2*3.0 + 1/10 = 8.1
        let w = get_item_weight(
            "from oxitest import Fixture\ndef test_it(a: Fixture[str], b: Fixture[int]):\n    pass\n",
        );
        assert!((w - 8.1).abs() < 0.01, "expected ~8.1, got {w}");
    }

    #[test]
    fn body_weight_many_statements() {
        // 10 statements in body: base + 10/10 = 2.0 + 1.0 = 3.0
        let content = "def test_it():\n".to_string() + &"    x = 1\n".repeat(10);
        let w = get_item_weight(&content);
        assert!((w - 3.0).abs() < 0.01, "expected ~3.0, got {w}");
    }

    // ── detect_heavy_imports ─────────────────────────────────────────

    #[test]
    fn heavy_imports_requests() {
        let f = write_temp_py("import requests\ndef test_it(): pass\n");
        let (_, stmts) = python_ast::parse_file(&temp_path(&f)).unwrap();
        assert_eq!(detect_heavy_imports(&stmts), 20.0);
    }

    #[test]
    fn heavy_imports_sqlalchemy_from_import() {
        let f = write_temp_py("from sqlalchemy import create_engine\ndef test_it(): pass\n");
        let (_, stmts) = python_ast::parse_file(&temp_path(&f)).unwrap();
        assert_eq!(detect_heavy_imports(&stmts), 20.0);
    }

    #[test]
    fn heavy_imports_no_heavy() {
        let f = write_temp_py("import os\nimport sys\ndef test_it(): pass\n");
        let (_, stmts) = python_ast::parse_file(&temp_path(&f)).unwrap();
        assert_eq!(detect_heavy_imports(&stmts), 0.0);
    }

    #[test]
    fn body_weight_heavy_import_adds_20() {
        // requests import adds 20.0 to each test's weight
        // 2.0 + 0.0 + 0.0 + 0.0 + 1/10 + 20.0 = 22.1
        let w = get_item_weight("import requests\ndef test_it():\n    pass\n");
        assert!((w - 22.1).abs() < 0.01, "expected ~22.1, got {w}");
    }

    #[test]
    fn body_weight_asyncio_sleep() {
        let f = write_temp_py(
            "import asyncio\nasync def test_async_sleep():\n    await asyncio.sleep(0.5)\n    assert True\n",
        );
        let path = temp_path(&f);
        let result = prescan_with_ast(&path, false);
        match result {
            PrescanResult::HasTests(p) => {
                // base(2) + async(10) + sleep(500) + 2 stmts / 10 = 512.2
                assert!((p.items[0].body_weight_ms - 512.2).abs() < 0.01);
            }
            _ => panic!("expected HasTests"),
        }
    }
}
