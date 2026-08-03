//! Prescan engine — AST-based metadata extraction for lazy collection.
//!
//! Scans Python files without importing them, extracting per-test metadata
//! ([`PrescanItem`]) that the pipeline uses for filtering before import.
//! Heavy work lives here; leaf AST utilities live in [`crate::python_ast`].

use std::sync::OnceLock;

use camino::{Utf8Path, Utf8PathBuf};
use pyo3::types::PyAnyMethods as _;
use rustpython_parser::{Parse as _, ast};

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
    pub(crate) lineno: crate::types::LineNo,
    pub(crate) is_async: bool,
    pub(crate) markers: Vec<PrescanMarker>,
    pub(crate) param_ids: Vec<String>,
    pub(crate) fixture_params: Vec<String>,
    pub(crate) is_class_method: bool,
    pub(crate) class_name: Option<String>,
    /// Estimated execution time in milliseconds, derived from AST analysis.
    pub(crate) body_weight: crate::types::DurationMs,
}

/// Per-module prescan result used in the pipeline state.
#[derive(Debug)]
pub(crate) struct PrescanModule {
    pub(crate) path: Utf8PathBuf,
    pub(crate) items: Vec<PrescanItem>,
    pub(crate) has_dynamic_collection: bool,
    /// Prescan result for the sibling `__fixtures__.py` in the same directory,
    /// if one exists. `None` means no sibling was found. Consumed in later
    /// pipeline slices; collection uses a fresh prescan directly.
    #[allow(dead_code)] // read in later pipeline slice
    pub(crate) fixture_module: Option<PrescanFixtureResult>,
}

/// Payload extracted from a Python file that has test functions.
#[derive(Debug)]
pub(crate) struct PrescanPayload {
    pub(crate) source: String,
    pub(crate) stmts: Vec<ast::Stmt>,
    pub(crate) items: Vec<PrescanItem>,
    pub(crate) has_dynamic_collection: bool,
    pub(crate) module_markers: Vec<String>,
    /// Inline `@oxi.fixture` declarations in this test file (#1712).
    ///
    /// Capped at `module` lifetime by home *kind*, independent of the location
    /// rule that governs declaration homes (#1711): at the rootdir package that
    /// rule permits `session`, and only the home-kind cap rejects it inline.
    pub(crate) declarations: Vec<PrescanDeclaration>,
    /// Whether any top-level function carries a decorator *shaped* like a
    /// fixture declaration, under any import spelling (#1850).
    ///
    /// A strict superset of `!declarations.is_empty()`, and the two answer
    /// different questions. `declarations` is what oxitest will *act* on, so it
    /// only counts the spellings the runtime is documented to recognize. This
    /// flag decides whether the module-item cache may serve the file, where the
    /// safe direction is the other one: registration happens by marker
    /// attribute at import time, so `import oxitest as alias` declares a real
    /// fixture that `declarations` cannot see. Missing one silently
    /// reintroduces #1850 for that file; an extra one costs a cache miss.
    pub(crate) has_fixture_shaped_decorator: bool,
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

/// A fixture declaration extracted from AST (no Python import).
///
/// The payload fields are read in the pipeline's later slices (slice 5+).
/// The `HasFixtures` variant is matched in collection to gate the bridge call;
/// the inner payload data is reserved for future optimisations.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct PrescanDeclaration {
    pub(crate) fn_name: String,
    pub(crate) lineno: crate::types::LineNo,
    pub(crate) lifetime: String,
    pub(crate) is_async: bool,
}

/// The `lifetime=` value that triggers scheduler co-location (#1710).
///
/// Compared as a string because prescan reads the decorator off the AST, before
/// any Python runs — there is no `Lifetime` enum on this side of the bridge.
pub(crate) const LIFETIME_PACKAGE: &str = "package";

/// The `lifetime=` value that is legal only in a rootdir package (#1711).
///
/// Compared as a string for the same reason as [`LIFETIME_PACKAGE`]: prescan
/// reads the decorator off the AST, before any Python runs.
pub(crate) const LIFETIME_PROCESS: &str = "process";

/// Per-fixture-module payload (mirrors PrescanPayload).
///
/// `declarations` drives package co-location in `collection.rs` and is consumed
/// further by later slices (slice 5+ diagnostics, slice 9 async support).
/// `is_async` and `lineno` on each `PrescanDeclaration` remain intentional
/// scaffolding; their `#[allow(dead_code)]` markers document the deferral.
#[derive(Debug)]
pub(crate) struct PrescanFixturePayload {
    pub(crate) declarations: Vec<PrescanDeclaration>,
}

/// Payload for the `NoFixtures` variant — carries DX hints.
#[derive(Debug, Default)]
pub(crate) struct NoFixturesPayload {
    /// True when the file has @-decorated top-level functions but none matched
    /// the recognized `@oxi.fixture` / `@oxitest.fixture` / `@fixture` forms.
    /// Indicates a probable unrecognized import alias (MED-3).
    pub(crate) has_unrecognized_decorated_functions: bool,
}

/// Result of pre-scanning a __fixtures__.py file.
///
/// `HasFixtures` is matched in `collection.rs` to gate the Python bridge call.
/// The inner payload is reserved for later-slice optimisations.
#[derive(Debug)]
pub(crate) enum PrescanFixtureResult {
    /// File contains @oxi.fixture declarations. The payload fields are reserved
    /// for later-slice optimisations; the variant itself gates the bridge call.
    #[allow(dead_code)] // inner payload read in later pipeline slice
    HasFixtures(PrescanFixturePayload),
    /// File has no recognized @oxi.fixture declarations. The payload carries
    /// DX hints (e.g. unrecognized import alias) for MED-3 diagnostics.
    NoFixtures(NoFixturesPayload),
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

/// Check whether an expression is a literal value (constant, or a collection of literals).
fn is_literal_expr(expr: &ast::Expr) -> bool {
    match expr {
        ast::Expr::Constant(_) => true,
        ast::Expr::Tuple(t) => t.elts.iter().all(is_literal_expr),
        ast::Expr::List(l) => l.elts.iter().all(is_literal_expr),
        ast::Expr::Set(s) => s.elts.iter().all(is_literal_expr),
        ast::Expr::Dict(d) => {
            d.keys
                .iter()
                .all(|k| k.as_ref().is_some_and(is_literal_expr))
                && d.values.iter().all(is_literal_expr)
        }
        _ => false,
    }
}

/// Extract keyword argument names from `@oxi.parametrize(case1=..., case2=...)`.
fn extract_parametrize_kwarg_names(decorators: &[ast::Expr]) -> Vec<String> {
    let mut ids = Vec::new();
    for dec in decorators {
        if let ast::Expr::Call(call) = dec
            && is_parametrize_call(&call.func)
        {
            for kw in &call.keywords {
                if let Some(ref arg) = kw.arg {
                    ids.push(arg.to_string());
                }
            }
        }
    }
    ids
}

/// Check if a call target is one of the recognized parametrize forms.
fn is_parametrize_call(func: &ast::Expr) -> bool {
    if let ast::Expr::Attribute(attr) = func
        && attr.attr.as_str() == "parametrize"
    {
        // oxi.parametrize or oxitest.parametrize
        if let ast::Expr::Name(n) = &*attr.value
            && python_ast::is_oxitest_namespace(n.id.as_str())
        {
            return true;
        }
        // oxi.mark.parametrize or oxitest.mark.parametrize
        if let ast::Expr::Attribute(inner) = &*attr.value
            && inner.attr.as_str() == "mark"
            && let ast::Expr::Name(n) = &*inner.value
            && python_ast::is_oxitest_namespace(n.id.as_str())
        {
            return true;
        }
        // bare mark.parametrize (from `from oxitest import mark`)
        if let ast::Expr::Name(n) = &*attr.value
            && n.id.as_str() == "mark"
        {
            return true;
        }
    }
    false
}

/// Extract parameter names that have a `Fixture[T]` annotation.
fn extract_fixture_param_names(args: &ast::Arguments) -> Vec<String> {
    let mut names = Vec::new();
    for arg_with_default in args.args.iter().chain(args.kwonlyargs.iter()) {
        if let Some(ref annotation) = arg_with_default.def.annotation
            && is_fixture_annotation(annotation)
        {
            names.push(arg_with_default.def.arg.to_string());
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
                        && matches!(&*attr.value, ast::Expr::Name(n) if python_ast::is_oxitest_namespace(n.id.as_str()))
                }
                _ => false,
            }
        }
        _ => false,
    }
}

// ── Dynamic collection detection ────────────────────────────────────────

/// Check whether a statement contains a top-level `exec()` or `eval()` call.
fn has_exec_eval_call(stmt: &ast::Stmt) -> bool {
    match stmt {
        ast::Stmt::Expr(expr) => is_dynamic_call(&expr.value),
        ast::Stmt::Assign(assign) => is_dynamic_call(&assign.value),
        _ => false,
    }
}

/// Check whether a statement injects into `globals()[]`.
fn has_globals_injection(stmt: &ast::Stmt) -> bool {
    match stmt {
        ast::Stmt::Expr(expr) => is_globals_subscript(&expr.value),
        ast::Stmt::Assign(assign) => {
            is_globals_subscript(&assign.value) || assign.targets.iter().any(is_globals_subscript)
        }
        _ => false,
    }
}

/// Check whether a statement uses `type("Name", (bases,), {...})` metaclass creation.
fn has_type_metaclass_creation(stmt: &ast::Stmt) -> bool {
    matches!(stmt, ast::Stmt::Assign(assign) if is_type_metaclass_call(&assign.value))
}

/// Check whether a statement defines a module-level `__getattr__` function.
fn has_getattr_definition(stmt: &ast::Stmt) -> bool {
    python_ast::FnDef::try_from_stmt(stmt).is_some_and(|d| d.name() == "__getattr__")
}

/// Check whether a statement is a `from non_stdlib import *`.
fn has_nonstdlib_star_import(stmt: &ast::Stmt) -> bool {
    if let ast::Stmt::ImportFrom(imp) = stmt
        && imp.names.len() == 1
        && imp.names[0].name.as_str() == "*"
    {
        let module = imp.module.as_ref().map(|m| m.as_str()).unwrap_or("");
        return !is_stdlib_module(module);
    }
    false
}

/// Detect dynamic patterns that prevent lazy collection.
///
/// Scans top-level statements for:
/// - `exec()`, `eval()`, `globals()[]` calls
/// - `__getattr__` definitions
/// - star imports from non-stdlib modules
/// - `type()` metaclass creation
fn detect_dynamic_collection(stmts: &[ast::Stmt]) -> bool {
    stmts.iter().any(|stmt| {
        has_exec_eval_call(stmt)
            || has_globals_injection(stmt)
            || has_type_metaclass_creation(stmt)
            || has_getattr_definition(stmt)
            || has_nonstdlib_star_import(stmt)
    })
}

/// Check if an expression is a call to `exec()` or `eval()`.
fn is_dynamic_call(expr: &ast::Expr) -> bool {
    if let ast::Expr::Call(call) = expr
        && let ast::Expr::Name(n) = &*call.func
    {
        let s = n.id.as_str();
        return s == "exec" || s == "eval";
    }
    false
}

/// Check if an expression is `globals()[...]`.
fn is_globals_subscript(expr: &ast::Expr) -> bool {
    if let ast::Expr::Subscript(sub) = expr
        && let ast::Expr::Call(call) = &*sub.value
        && let ast::Expr::Name(n) = &*call.func
    {
        return n.id.as_str() == "globals";
    }
    false
}

/// Check if an expression is `type("Name", (bases,), {...})` — metaclass creation.
fn is_type_metaclass_call(expr: &ast::Expr) -> bool {
    if let ast::Expr::Call(call) = expr
        && let ast::Expr::Name(n) = &*call.func
        && n.id.as_str() == "type"
        && call.args.len() >= 3
    {
        return true;
    }
    false
}

/// Cached set of Python standard library top-level module names.
///
/// Populated once from `sys.stdlib_module_names` (Python 3.10+) via
/// [`init_stdlib_names`]. When uninitialized (unit tests that skip init),
/// `is_stdlib_module` returns `false` for all modules — conservative, since
/// that triggers eager collection.
static STDLIB_NAMES: OnceLock<std::collections::HashSet<String>> = OnceLock::new();

/// Populate [`STDLIB_NAMES`] from the running Python interpreter.
///
/// Must be called once while the GIL is held, before prescan runs.
/// Safe to call multiple times — `OnceLock` ignores subsequent calls.
pub(crate) fn init_stdlib_names(py: pyo3::Python<'_>) {
    STDLIB_NAMES.get_or_init(|| {
        py.import("sys")
            .expect("sys import")
            .getattr("stdlib_module_names")
            .expect("stdlib_module_names attr")
            .extract::<std::collections::HashSet<String>>()
            .expect("extract HashSet<String>")
    });
}

/// Check whether a module name belongs to the Python standard library.
fn is_stdlib_module(module: &str) -> bool {
    let top = module.split('.').next().unwrap_or(module);
    STDLIB_NAMES.get().map(|s| s.contains(top)).unwrap_or(false)
}

// ── Module marks ────────────────────────────────────────────────────────

/// Extract module-level marks from `oxi_mark = mark.NAME` or `oxi_mark = [mark.NAME, ...]`
/// assignments.
fn extract_module_marks(stmts: &[ast::Stmt]) -> Vec<String> {
    let mut marks = Vec::new();
    for stmt in stmts {
        if let ast::Stmt::Assign(assign) = stmt {
            // Check target is `oxi_mark`
            if assign.targets.len() == 1
                && let ast::Expr::Name(n) = &assign.targets[0]
                && n.id.as_str() == "oxi_mark"
            {
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
                if let ast::Expr::Name(n) = &*inner.value
                    && python_ast::is_oxitest_namespace(n.id.as_str())
                {
                    return Some(mark_name.to_string());
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
fn sleep_call_weight(expr: &ast::Expr) -> f64 {
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
            sleep_weight += sleep_call_weight(call_expr);
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
pub(crate) fn heavy_import_weight(stmts: &[ast::Stmt]) -> f64 {
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

/// Build a [`PrescanItem`] from a [`python_ast::FnDef`] adapter.
///
/// The adapter unifies sync/async function defs so callers don't need
/// separate match arms.
macro_rules! build_prescan_item {
    ($def:expr, $is_class_method:expr, $class_name:expr, $line_index:expr, $heavy_import_weight:expr) => {{
        let markers: Vec<PrescanMarker> = $def
            .decorator_list()
            .iter()
            .filter_map(extract_prescan_marker)
            .collect();
        let param_ids = extract_parametrize_kwarg_names($def.decorator_list());
        let fixture_params = extract_fixture_param_names($def.args());
        let lineno = crate::types::LineNo::from_u32(python_ast::offset_to_line(
            $line_index,
            $def.range().start().to_u32(),
        ));
        let body_weight = crate::types::DurationMs::new(compute_body_weight(
            $def.body(),
            $def.is_async(),
            fixture_params.len(),
            $heavy_import_weight,
        ));
        PrescanItem {
            fn_name: $def.name().to_string(),
            lineno,
            is_async: $def.is_async(),
            markers,
            param_ids,
            fixture_params,
            is_class_method: $is_class_method,
            class_name: $class_name,
            body_weight,
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
    let mut items = Vec::new();

    let heavy_import_wt = heavy_import_weight(&parsed.1);

    python_ast::walk_test_defs(&parsed.1, |def, class_opt| {
        items.push(build_prescan_item!(
            def,
            class_opt.is_some(),
            class_opt.map(|cls| cls.name.to_string()),
            &line_index,
            heavy_import_wt
        ));
    });

    if items.is_empty() {
        return PrescanResult::NoTests;
    }

    let has_dynamic_collection = detect_dynamic_collection(&parsed.1);
    let module_markers = extract_module_marks(&parsed.1);
    // Separate walk from `walk_test_defs` above, which visits only `test_*`
    // functions and so cannot see a fixture-decorated helper. A function that is
    // both stays in both lists: the two walks answer independent questions, and
    // collapsing them here would hide a user error a later slice should report.
    let declarations = collect_declarations(&parsed.1, &line_index);
    let fixture_shaped = has_fixture_shaped_decorator(&parsed.1);

    if keep_ast {
        PrescanResult::HasTests(PrescanPayload {
            source: parsed.0,
            stmts: parsed.1,
            items,
            has_dynamic_collection,
            module_markers,
            declarations,
            has_fixture_shaped_decorator: fixture_shaped,
        })
    } else {
        PrescanResult::HasTests(PrescanPayload {
            source: String::new(),
            stmts: Vec::new(),
            items,
            has_dynamic_collection,
            // Carried in both arms: the inline cap check does not depend on
            // `keep_ast`, which is driven by strict-mode violation collection.
            module_markers,
            declarations,
            has_fixture_shaped_decorator: fixture_shaped,
        })
    }
}

// ── Fixture module prescan ──────────────────────────────────────────────

/// Check if a call target is one of the recognized fixture decorator forms:
/// `oxi.fixture(...)`, `oxitest.fixture(...)`, or bare `fixture(...)`.
fn is_fixture_call(func: &ast::Expr) -> bool {
    match func {
        ast::Expr::Attribute(a) => {
            if a.attr.as_str() != "fixture" {
                return false;
            }
            match a.value.as_ref() {
                ast::Expr::Name(n) => python_ast::is_oxitest_namespace(n.id.as_str()),
                _ => false,
            }
        }
        ast::Expr::Name(n) => n.id.as_str() == "fixture",
        _ => false,
    }
}

/// Collect every `@oxi.fixture` declaration among a module's top-level functions.
///
/// Shared by the fixture-module path and the test-file path (#1712): both ask the
/// same question of the same AST shape, and a copy would let the two drift on
/// what counts as a declaration.
///
/// Only the first recognized `@oxi.fixture` decorator on a function counts — a
/// function cannot hold two lifetimes, and stacking them is a user error for a
/// later slice to diagnose rather than something to record twice here.
fn collect_declarations(stmts: &[ast::Stmt], line_index: &[u32]) -> Vec<PrescanDeclaration> {
    let mut declarations: Vec<PrescanDeclaration> = Vec::new();
    for stmt in stmts {
        let (fn_name, decorators, range, is_async) = match stmt {
            ast::Stmt::FunctionDef(f) => (f.name.to_string(), &f.decorator_list, f.range, false),
            ast::Stmt::AsyncFunctionDef(f) => {
                (f.name.to_string(), &f.decorator_list, f.range, true)
            }
            _ => continue,
        };
        for dec in decorators {
            if let Some(lifetime) = extract_fixture_decorator_lifetime(dec) {
                let lineno = crate::types::LineNo::from_u32(python_ast::offset_to_line(
                    line_index,
                    range.start().to_u32(),
                ));
                declarations.push(PrescanDeclaration {
                    fn_name: fn_name.clone(),
                    lineno,
                    lifetime,
                    is_async,
                });
                break;
            }
        }
    }
    declarations
}

/// Recognize `@oxi.fixture(...)` / `@oxitest.fixture(...)` / `@fixture(...)`.
///
/// Returns `Some(lifetime_string)` if the decorator is a static call with a
/// single `lifetime="..."` kwarg and no positional args. Returns `None`
/// otherwise (unrecognized shape → skipped, does NOT set has_dynamic flag).
fn extract_fixture_decorator_lifetime(dec: &ast::Expr) -> Option<String> {
    let call = match dec {
        ast::Expr::Call(c) => c,
        _ => return None,
    };
    if !is_fixture_call(&call.func) {
        return None;
    }
    if !call.args.is_empty() {
        return None; // slice 1 forbids positional args
    }
    if call.keywords.len() != 1 {
        return None;
    }
    let kw = &call.keywords[0];
    let key = kw.arg.as_ref()?.as_str();
    if key != "lifetime" {
        return None;
    }
    match &kw.value {
        ast::Expr::Constant(c) => match &c.value {
            ast::Constant::Str(s) => Some(s.clone()),
            _ => None,
        },
        _ => None,
    }
}

/// Whether any top-level function is decorated by a call to something named
/// `fixture` — `oxi.fixture(...)`, `pkg.fixture(...)`, or bare `fixture(...)`.
///
/// Deliberately looser than [`is_fixture_call`]: the namespace is not checked,
/// because the point is to catch the spellings that function makes invisible.
/// Used **only** for module-item cache eligibility (#1850), never to accept a
/// declaration — a false positive costs one cache miss, while a false negative
/// silently drops the fixture on every warm run.
///
/// Still requires a *call*: `@oxi.fixture` without arguments cannot be a
/// fixture, since `fixture(*, lifetime)` takes a required keyword-only
/// argument and would raise at import.
fn has_fixture_shaped_decorator(stmts: &[ast::Stmt]) -> bool {
    stmts.iter().any(|stmt| {
        let decorators = match stmt {
            ast::Stmt::FunctionDef(f) => &f.decorator_list,
            ast::Stmt::AsyncFunctionDef(f) => &f.decorator_list,
            _ => return false,
        };
        decorators.iter().any(|dec| match dec {
            ast::Expr::Call(call) => match call.func.as_ref() {
                ast::Expr::Attribute(attr) => attr.attr.as_str() == "fixture",
                ast::Expr::Name(name) => name.id.as_str() == "fixture",
                _ => false,
            },
            _ => false,
        })
    })
}

/// Read `path` from disk and prescan it as a fixture module.
///
/// Returns `Unavailable` if the file can't be read or parsed.
pub(crate) fn prescan_fixture_module(path: &Utf8Path) -> PrescanFixtureResult {
    let source = match std::fs::read_to_string(path.as_std_path()) {
        Ok(s) => s,
        Err(_) => return PrescanFixtureResult::Unavailable,
    };
    prescan_fixture_module_from_source(path, &source)
}

/// Test-friendly variant that takes source directly (skips fs read).
pub(crate) fn prescan_fixture_module_from_source(
    path: &Utf8Path,
    source: &str,
) -> PrescanFixtureResult {
    let stmts = match ast::Suite::parse(source, path.as_str()) {
        Ok(s) => s,
        Err(_) => return PrescanFixtureResult::Unavailable,
    };

    let line_index = python_ast::build_line_index(source);
    let declarations = collect_declarations(&stmts, &line_index);

    if declarations.is_empty() {
        // MED-3: detect decorated top-level functions whose decorator was not
        // recognized as an @oxi.fixture form. This hints at a probable
        // unrecognized import alias (e.g. `import oxitest as ox`).
        let has_unrecognized_decorated_functions = stmts.iter().any(|stmt| {
            let decorators: &[ast::Expr] = match stmt {
                ast::Stmt::FunctionDef(f) => &f.decorator_list,
                ast::Stmt::AsyncFunctionDef(f) => &f.decorator_list,
                _ => return false,
            };
            !decorators.is_empty()
        });
        return PrescanFixtureResult::NoFixtures(NoFixturesPayload {
            has_unrecognized_decorated_functions,
        });
    }

    PrescanFixtureResult::HasFixtures(PrescanFixturePayload { declarations })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::python_ast::tests::{temp_path, write_temp_py};

    // ── inline fixture declarations in test files (#1712) ──────────────────

    fn inline_declarations(src: &str) -> Vec<PrescanDeclaration> {
        let file = write_temp_py(src);
        match prescan_with_ast(&temp_path(&file), false) {
            PrescanResult::HasTests(payload) => payload.declarations,
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn inline_fixture_beside_a_test_is_collected() {
        let declarations = inline_declarations(
            "import oxitest as oxi\n\n@oxi.fixture(lifetime=\"module\")\ndef conn(): return 1\n\ndef test_uses_it(): pass\n",
        );

        assert_eq!(
            declarations.len(),
            1,
            "an @oxi.fixture in a test file must be collected; walk_test_defs \
             only visits test_* functions, so a separate walk is what finds it — \
             got {declarations:?}"
        );
        assert_eq!(
            declarations[0].fn_name, "conn",
            "the declaration must name the fixture so the cap diagnostic can too"
        );
        assert_eq!(
            declarations[0].lifetime, "module",
            "the lifetime kwarg is captured verbatim; the cap check compares strings"
        );
    }

    #[test]
    fn a_test_file_without_fixtures_collects_no_declarations() {
        let declarations = inline_declarations("def test_alone(): pass\n");

        assert!(
            declarations.is_empty(),
            "an ordinary test file declares nothing; a false positive here would \
             make every existing suite pay the cap check — got {declarations:?}"
        );
    }

    // ── cache-eligibility signal (#1850) ──────────────────────────────────

    fn fixture_shaped(src: &str) -> bool {
        let file = write_temp_py(src);
        match prescan_with_ast(&temp_path(&file), false) {
            PrescanResult::HasTests(payload) => payload.has_fixture_shaped_decorator,
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

    #[test]
    fn an_unrecognized_alias_still_counts_as_a_possible_declaration() {
        // `ox` is not in the recognized namespace set, so `declarations` is
        // empty here — but the decorator still attaches the fixture marker at
        // import, so the module-item cache must not serve this file.
        let src = "import oxitest as ox\n\n@ox.fixture(lifetime=\"function\")\ndef client(): return 1\n\ndef test_uses_it(): pass\n";

        assert!(
            inline_declarations(src).is_empty(),
            "guard for this test's premise: if the declaration scan ever learns \
             this spelling, the cache signal is no longer the only thing \
             standing between an aliased import and #1850"
        );
        assert!(
            fixture_shaped(src),
            "an aliased fixture decorator must make the file cache-ineligible; \
             missing it drops the fixture on every warm run, which is the exact \
             defect #1850 fixed for the documented spelling"
        );
    }

    #[test]
    fn a_bare_fixture_decorator_call_counts() {
        assert!(
            fixture_shaped(
                "from oxitest import fixture\n\n@fixture(lifetime=\"module\")\ndef conn(): return 1\n\ndef test_uses_it(): pass\n"
            ),
            "the `from oxitest import fixture` spelling is documented and must \
             be cache-ineligible like the dotted ones"
        );
    }

    #[test]
    fn an_ordinary_test_file_stays_cache_eligible() {
        assert!(
            !fixture_shaped(
                "import oxitest as oxi\n\n@oxi.mark.skip(reason=\"x\")\ndef test_marked(): pass\n"
            ),
            "marks are not fixtures; treating any decorator as a possible \
             declaration would cost every marked suite its item cache"
        );
    }

    #[test]
    fn two_inline_fixtures_keep_their_separate_lifetimes() {
        let declarations = inline_declarations(
            "import oxitest as oxi\n\n@oxi.fixture(lifetime=\"function\")\ndef per_test(): return 1\n\n@oxi.fixture(lifetime=\"module\")\ndef per_module(): return 2\n\ndef test_both(): pass\n",
        );

        let pairs: Vec<(String, String)> = declarations
            .iter()
            .map(|decl| (decl.fn_name.clone(), decl.lifetime.clone()))
            .collect();
        assert_eq!(
            pairs,
            vec![
                ("per_test".to_owned(), "function".to_owned()),
                ("per_module".to_owned(), "module".to_owned()),
            ],
            "each declaration carries its own lifetime — collapsing them would \
             make one of the two tiers silently wrong"
        );
    }

    #[test]
    fn a_function_that_is_both_test_and_fixture_appears_in_both_lists() {
        let file = write_temp_py(
            "import oxitest as oxi\n\n@oxi.fixture(lifetime=\"function\")\ndef test_both(): return 1\n",
        );

        match prescan_with_ast(&temp_path(&file), false) {
            PrescanResult::HasTests(payload) => {
                assert_eq!(
                    payload.items.len(),
                    1,
                    "the test_ name still makes it a test item"
                );
                assert_eq!(
                    payload.declarations.len(),
                    1,
                    "and the decorator still makes it a declaration. The two walks \
                     answer independent questions; resolving the conflict here \
                     would hide a user error that belongs in a diagnostic (#1713 \
                     or #1716), not in silent precedence"
                );
            }
            other => panic!("expected HasTests, got {other:?}"),
        }
    }

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
    fn prescan_items_extracts_bare_mark_parametrize() {
        let f = write_temp_py(
            "from oxitest import mark\n@mark.parametrize(case1=1, case2=2)\ndef test_it(case1, case2):\n    pass\n",
        );
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests(p) => {
                assert_eq!(p.items.len(), 1);
                assert_eq!(p.items[0].param_ids, vec!["case1", "case2"]);
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
    fn prescan_items_extracts_oxi_fixture_annotation() {
        let f = write_temp_py(
            "import oxitest as oxi\nfrom pathlib import Path\ndef test_it(tmp: oxi.Fixture[Path]):\n    pass\n",
        );
        let result = prescan_with_ast(&temp_path(&f), false);
        match result {
            PrescanResult::HasTests(p) => {
                assert_eq!(p.items.len(), 1);
                assert_eq!(p.items[0].fixture_params, vec!["tmp"]);
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
        pyo3::Python::initialize();
        pyo3::Python::attach(init_stdlib_names);
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
                p.items[0].body_weight.as_f64()
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

    // ── heavy_import_weight ──────────────────────────────────────────

    #[test]
    fn heavy_imports_requests() {
        let f = write_temp_py("import requests\ndef test_it(): pass\n");
        let (_, stmts) = python_ast::parse_file(&temp_path(&f)).unwrap();
        assert_eq!(heavy_import_weight(&stmts), 20.0);
    }

    #[test]
    fn heavy_imports_sqlalchemy_from_import() {
        let f = write_temp_py("from sqlalchemy import create_engine\ndef test_it(): pass\n");
        let (_, stmts) = python_ast::parse_file(&temp_path(&f)).unwrap();
        assert_eq!(heavy_import_weight(&stmts), 20.0);
    }

    #[test]
    fn heavy_imports_no_heavy() {
        let f = write_temp_py("import os\nimport sys\ndef test_it(): pass\n");
        let (_, stmts) = python_ast::parse_file(&temp_path(&f)).unwrap();
        assert_eq!(heavy_import_weight(&stmts), 0.0);
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
                assert!((p.items[0].body_weight.as_f64() - 512.2).abs() < 0.01);
            }
            _ => panic!("expected HasTests"),
        }
    }

    // ── is_literal_expr ──────────────────────────────────────────────────

    #[test]
    fn is_literal_expr_constant() {
        let expr = ast::Expr::Constant(ast::ExprConstant {
            value: ast::Constant::Str("hello".to_string()),
            kind: None,
            range: Default::default(),
        });
        assert!(is_literal_expr(&expr));
    }

    #[test]
    fn is_literal_expr_tuple_of_constants() {
        let expr = ast::Expr::Tuple(ast::ExprTuple {
            elts: vec![
                ast::Expr::Constant(ast::ExprConstant {
                    value: ast::Constant::Str("a".to_string()),
                    kind: None,
                    range: Default::default(),
                }),
                ast::Expr::Constant(ast::ExprConstant {
                    value: ast::Constant::Str("b".to_string()),
                    kind: None,
                    range: Default::default(),
                }),
            ],
            ctx: ast::ExprContext::Load,
            range: Default::default(),
        });
        assert!(is_literal_expr(&expr));
    }

    #[test]
    fn is_literal_expr_list_with_name_is_false() {
        let expr = ast::Expr::List(ast::ExprList {
            elts: vec![ast::Expr::Name(ast::ExprName {
                id: ast::Identifier::new("x"),
                ctx: ast::ExprContext::Load,
                range: Default::default(),
            })],
            ctx: ast::ExprContext::Load,
            range: Default::default(),
        });
        assert!(!is_literal_expr(&expr));
    }

    #[test]
    fn is_literal_expr_empty_tuple() {
        let expr = ast::Expr::Tuple(ast::ExprTuple {
            elts: vec![],
            ctx: ast::ExprContext::Load,
            range: Default::default(),
        });
        assert!(is_literal_expr(&expr));
    }

    #[test]
    fn is_stdlib_module_recognizes_os() {
        pyo3::Python::initialize();
        pyo3::Python::attach(|py| {
            init_stdlib_names(py);
        });
        assert!(is_stdlib_module("os"));
        assert!(is_stdlib_module("os.path"));
        assert!(is_stdlib_module("importlib"));
        assert!(is_stdlib_module("importlib.metadata"));
    }

    #[test]
    fn is_stdlib_module_rejects_third_party() {
        // OnceLock already initialized by the sibling test (process-global).
        // If this runs first, the uninit fallback returns false — also correct.
        assert!(!is_stdlib_module("requests"));
        assert!(!is_stdlib_module("numpy"));
        assert!(!is_stdlib_module("django.db"));
    }

    // ── slice1_recognizer_tests ─────────────────────────────────────────────

    #[cfg(test)]
    mod slice1_recognizer_tests {
        use super::*;

        fn prescan(src: &str) -> PrescanFixtureResult {
            let path = camino::Utf8PathBuf::from("/virtual/__fixtures__.py");
            prescan_fixture_module_from_source(&path, src)
        }

        #[test]
        fn recognizes_oxi_fixture_call() {
            let src = r#"
import oxitest as oxi

@oxi.fixture(lifetime="function")
def conn():
    return object()
"#;
            match prescan(src) {
                PrescanFixtureResult::HasFixtures(payload) => {
                    assert_eq!(
                        payload.declarations.len(),
                        1,
                        "expected exactly 1 declaration, got {:?}",
                        payload.declarations
                    );
                    assert_eq!(
                        payload.declarations[0].fn_name, "conn",
                        "fixture function name must be 'conn'"
                    );
                    assert_eq!(
                        payload.declarations[0].lifetime, "function",
                        "lifetime kwarg must be captured verbatim"
                    );
                    assert!(
                        !payload.declarations[0].is_async,
                        "sync def must not be flagged is_async"
                    );
                }
                other => panic!("expected HasFixtures, got {other:?}"),
            }
        }

        #[test]
        fn recognizes_direct_fixture_import() {
            let src = r#"
from oxitest import fixture

@fixture(lifetime="function")
def conn():
    return object()
"#;
            match prescan(src) {
                PrescanFixtureResult::HasFixtures(payload) => {
                    assert_eq!(
                        payload.declarations.len(),
                        1,
                        "bare @fixture should be recognized when imported from oxitest"
                    );
                    assert_eq!(
                        payload.declarations[0].fn_name, "conn",
                        "fixture function name must be 'conn'"
                    );
                }
                other => panic!("expected HasFixtures, got {other:?}"),
            }
        }

        #[test]
        fn falls_through_on_dynamic_decoration() {
            // Dynamic decoration (`dec = oxi.fixture(...); @dec def conn():`)
            // is not statically recognized. The prescan returns NoFixtures
            // (declarations.is_empty() == true). With_unrecognized_decorated_
            // functions may be set since `conn` has a decorator `@dec`.
            let src = r#"
import oxitest as oxi

dec = oxi.fixture(lifetime="function")
@dec
def conn():
    return object()
"#;
            let result = prescan(src);
            assert!(
                !matches!(result, PrescanFixtureResult::Unavailable),
                "dynamic-decoration is not a parse error, must not return Unavailable"
            );
            // Both HasFixtures(empty) and NoFixtures are acceptable outcomes;
            // either way the bridge call is gated on HasFixtures so the
            // unrecognized dynamic pattern falls through to Python import.
        }

        #[test]
        fn no_fixtures_file_returns_nofixtures() {
            let src = r#"
def not_a_fixture():
    return 1
"#;
            assert!(
                matches!(prescan(src), PrescanFixtureResult::NoFixtures(_)),
                "plain functions without @fixture decorator must yield NoFixtures"
            );
        }

        #[test]
        fn parse_error_returns_unavailable() {
            let src = "def broken(\n"; // syntactically invalid
            assert!(
                matches!(prescan(src), PrescanFixtureResult::Unavailable),
                "a syntax error must return Unavailable so the caller falls back to Python"
            );
        }

        /// Every decorator shape the slice-1 recognizer must reject.
        ///
        /// Each case is a decorator the user might plausibly write that
        /// slice 1 does not accept. All must fall through to NoFixtures with
        /// `has_unrecognized_decorated_functions` set, which is what drives
        /// the MED-3 "check your import alias" diagnostic — a silent
        /// acceptance here would register a fixture the runtime cannot honour.
        #[test]
        fn rejects_unsupported_decorator_shapes() {
            let cases: &[(&str, &str)] = &[
                (
                    "@oxi.other(lifetime=\"function\")",
                    "attribute is not `fixture`",
                ),
                (
                    "@a.b.fixture(lifetime=\"function\")",
                    "namespace is not a bare name",
                ),
                (
                    "@decs[\"f\"](lifetime=\"function\")",
                    "callee is neither Name nor Attribute",
                ),
                ("@oxi.fixture", "bare decorator, not a call"),
                (
                    "@oxi.fixture(\"function\")",
                    "slice 1 forbids positional args",
                ),
                ("@oxi.fixture()", "no keyword arguments"),
                (
                    "@oxi.fixture(lifetime=\"function\", autouse=True)",
                    "more than one keyword",
                ),
                (
                    "@oxi.fixture(scope=\"function\")",
                    "keyword is not `lifetime`",
                ),
                (
                    "@oxi.fixture(lifetime=1)",
                    "lifetime is not a string constant",
                ),
                (
                    "@oxi.fixture(lifetime=DEFAULT)",
                    "lifetime is not a constant at all",
                ),
                ("@oxi.fixture(**opts)", "double-star keyword has no name"),
            ];

            for (decorator, why) in cases {
                let src = format!(
                    "import oxitest as oxi\n\n{decorator}\ndef conn():\n    return object()\n"
                );
                match prescan(&src) {
                    PrescanFixtureResult::NoFixtures(payload) => {
                        assert!(
                            payload.has_unrecognized_decorated_functions,
                            "`{decorator}` ({why}) is rejected but the function is still \
                             decorated — has_unrecognized_decorated_functions must be true \
                             so the MED-3 diagnostic fires"
                        );
                    }
                    other => panic!("`{decorator}` must be rejected ({why}), got {other:?}"),
                }
            }
        }

        #[test]
        fn recognizes_async_fixture_declaration() {
            let src = r#"
import oxitest as oxi

@oxi.fixture(lifetime="function")
async def conn():
    return object()
"#;
            match prescan(src) {
                PrescanFixtureResult::HasFixtures(payload) => {
                    assert_eq!(
                        payload.declarations.len(),
                        1,
                        "an async fixture is a declaration like any other; \
                         slice-9 needs is_async recorded at prescan time"
                    );
                    assert!(
                        payload.declarations[0].is_async,
                        "is_async must be true for `async def` so slice-9 async \
                         support can dispatch without re-parsing"
                    );
                }
                other => panic!("expected HasFixtures for an async fixture, got {other:?}"),
            }
        }

        #[test]
        fn async_function_counts_as_unrecognized_decoration() {
            let src = r#"
import oxitest as testing

@testing.fixture(lifetime="function")
async def conn():
    return object()
"#;
            match prescan(src) {
                PrescanFixtureResult::NoFixtures(payload) => {
                    assert!(
                        payload.has_unrecognized_decorated_functions,
                        "a decorated `async def` must count toward the MED-3 hint \
                         exactly as a decorated `def` does — otherwise an unknown \
                         alias on an async fixture fails silently"
                    );
                }
                other => panic!("expected NoFixtures for an unrecognized alias, got {other:?}"),
            }
        }
    }
}
