//! Rust-side assert rewriter — transforms `assert` statements into rich
//! `_OxitestAssertionError` raises so that failure messages include the
//! left/right values and comparison operator.
//!
//! This module provides:
//! - Helper functions for constructing Python AST nodes via PyO3
//! - `rewrite_asserts()` which parses source, rewrites assert nodes, and
//!   returns the modified AST tree

use std::collections::HashMap;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString};

/// Tracks function scope during AST walk for bare-assert collection.
struct BareAssertCtx {
    /// Stack of function names. Outermost is index 0.
    fn_stack: Vec<String>,
    /// Bare asserts grouped by outermost function name.
    by_fn: HashMap<String, Vec<i64>>,
}

impl BareAssertCtx {
    fn new() -> Self {
        Self {
            fn_stack: Vec::new(),
            by_fn: HashMap::new(),
        }
    }

    /// Record a bare assert at the given line, attributed to the outermost function.
    fn record(&mut self, lineno: i64) {
        if let Some(outer) = self.fn_stack.first() {
            self.by_fn.entry(outer.clone()).or_default().push(lineno);
        }
    }

    /// Convert to a Python dict[str, list[int]].
    fn into_py_dict(self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let dict = PyDict::new(py);
        for (name, mut lines) in self.by_fn {
            lines.sort_unstable();
            let py_lines = PyList::new(py, &lines)?;
            dict.set_item(name, py_lines)?;
        }
        Ok(dict.into_any().unbind())
    }
}

/// Maps Python AST compare-operator type names to their string representations.
const OP_MAP: &[(&str, &str)] = &[
    ("Eq", "=="),
    ("NotEq", "!="),
    ("Lt", "<"),
    ("LtE", "<="),
    ("Gt", ">"),
    ("GtE", ">="),
    ("Is", "is"),
    ("IsNot", "is not"),
    ("In", "in"),
    ("NotIn", "not in"),
];

/// Look up the string representation of a Python `ast` compare-operator node.
///
/// Returns `None` if the operator type name is not recognised.
fn op_str(op: &Bound<'_, PyAny>) -> PyResult<Option<&'static str>> {
    let type_obj = op.get_type();
    let name: String = type_obj.getattr("__name__")?.extract()?;
    Ok(OP_MAP.iter().find(|(k, _)| *k == name).map(|(_, v)| *v))
}

/// Create `ast.Name(id=id_, ctx=ast.Load())` with the given location.
fn make_name_load<'py>(
    py: Python<'py>,
    ast: &Bound<'py, PyModule>,
    id: &str,
    lineno: i64,
    col_offset: i64,
) -> PyResult<Bound<'py, PyAny>> {
    let load = ast.getattr("Load")?.call0()?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("id", id)?;
    kwargs.set_item("ctx", load)?;
    kwargs.set_item("lineno", lineno)?;
    kwargs.set_item("col_offset", col_offset)?;
    ast.getattr("Name")?.call((), Some(&kwargs))
}

/// Create `ast.Name(id=id_, ctx=ast.Store())` with the given location.
fn make_name_store<'py>(
    py: Python<'py>,
    ast: &Bound<'py, PyModule>,
    id: &str,
    lineno: i64,
    col_offset: i64,
) -> PyResult<Bound<'py, PyAny>> {
    let store = ast.getattr("Store")?.call0()?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("id", id)?;
    kwargs.set_item("ctx", store)?;
    kwargs.set_item("lineno", lineno)?;
    kwargs.set_item("col_offset", col_offset)?;
    ast.getattr("Name")?.call((), Some(&kwargs))
}

/// Create `ast.Constant(value=val)` with the given location.
fn make_constant<'py>(
    py: Python<'py>,
    ast: &Bound<'py, PyModule>,
    val: &Bound<'py, PyAny>,
    lineno: i64,
    col_offset: i64,
) -> PyResult<Bound<'py, PyAny>> {
    let kwargs = PyDict::new(py);
    kwargs.set_item("value", val)?;
    kwargs.set_item("lineno", lineno)?;
    kwargs.set_item("col_offset", col_offset)?;
    ast.getattr("Constant")?.call((), Some(&kwargs))
}

/// Create `ast.Assign(targets=[name_store], value=expr)` with the given location.
fn make_assign<'py>(
    py: Python<'py>,
    ast: &Bound<'py, PyModule>,
    target_name: &str,
    value: &Bound<'py, PyAny>,
    lineno: i64,
    col_offset: i64,
) -> PyResult<Bound<'py, PyAny>> {
    let target = make_name_store(py, ast, target_name, lineno, col_offset)?;
    let targets = PyList::new(py, &[target])?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("targets", targets)?;
    kwargs.set_item("value", value)?;
    kwargs.set_item("lineno", lineno)?;
    kwargs.set_item("col_offset", col_offset)?;
    ast.getattr("Assign")?.call((), Some(&kwargs))
}

/// Create `ast.Raise(exc=exc_node)` with the given location.
fn make_raise<'py>(
    py: Python<'py>,
    ast: &Bound<'py, PyModule>,
    exc: &Bound<'py, PyAny>,
    lineno: i64,
    col_offset: i64,
) -> PyResult<Bound<'py, PyAny>> {
    let kwargs = PyDict::new(py);
    kwargs.set_item("exc", exc)?;
    kwargs.set_item("lineno", lineno)?;
    kwargs.set_item("col_offset", col_offset)?;
    ast.getattr("Raise")?.call((), Some(&kwargs))
}

/// Create `ast.Call(func=Name(func_name), args=args, keywords=[])` with the given location.
fn make_call<'py>(
    py: Python<'py>,
    ast: &Bound<'py, PyModule>,
    func_name: &str,
    args: &Bound<'py, PyList>,
    lineno: i64,
    col_offset: i64,
) -> PyResult<Bound<'py, PyAny>> {
    let func = make_name_load(py, ast, func_name, lineno, col_offset)?;
    let keywords = PyList::empty(py);
    let kwargs = PyDict::new(py);
    kwargs.set_item("func", func)?;
    kwargs.set_item("args", args)?;
    kwargs.set_item("keywords", keywords)?;
    kwargs.set_item("lineno", lineno)?;
    kwargs.set_item("col_offset", col_offset)?;
    ast.getattr("Call")?.call((), Some(&kwargs))
}

/// Create `ast.If(test=UnaryOp(Not(), test_expr), body=[raise_stmt], orelse=[])`.
fn make_if_not_raise<'py>(
    py: Python<'py>,
    ast: &Bound<'py, PyModule>,
    test_expr: &Bound<'py, PyAny>,
    raise_stmt: &Bound<'py, PyAny>,
    lineno: i64,
    col_offset: i64,
) -> PyResult<Bound<'py, PyAny>> {
    // Build UnaryOp(op=Not(), operand=test_expr)
    let not_op = ast.getattr("Not")?.call0()?;
    let unary_kwargs = PyDict::new(py);
    unary_kwargs.set_item("op", not_op)?;
    unary_kwargs.set_item("operand", test_expr)?;
    unary_kwargs.set_item("lineno", lineno)?;
    unary_kwargs.set_item("col_offset", col_offset)?;
    let not_test = ast.getattr("UnaryOp")?.call((), Some(&unary_kwargs))?;

    let body = PyList::new(py, [raise_stmt])?;
    let orelse = PyList::empty(py);
    let kwargs = PyDict::new(py);
    kwargs.set_item("test", not_test)?;
    kwargs.set_item("body", body)?;
    kwargs.set_item("orelse", orelse)?;
    kwargs.set_item("lineno", lineno)?;
    kwargs.set_item("col_offset", col_offset)?;
    ast.getattr("If")?.call((), Some(&kwargs))
}

/// Walk a list of statements, replacing `Assert` nodes with enriched raise patterns.
///
/// Uses index-based iteration because replacements change the list size.
fn rewrite_body<'py>(
    py: Python<'py>,
    ast: &Bound<'py, PyModule>,
    stmts: &Bound<'py, PyList>,
    ctx: &mut BareAssertCtx,
) -> PyResult<()> {
    let mut i = 0;
    while i < stmts.len() {
        let stmt = stmts.get_item(i)?;
        let type_name: String = stmt.get_type().getattr("__name__")?.extract()?;

        if type_name == "Assert" {
            // Record bare asserts before rewriting (msg == None means bare).
            let msg_attr = stmt.getattr("msg")?;
            if msg_attr.is_none() {
                let lineno: i64 = stmt.getattr("lineno")?.extract()?;
                ctx.record(lineno);
            }

            if let Some(replacements) = rewrite_assert(py, ast, &stmt)? {
                // Delete the original Assert node.
                stmts.call_method1("__delitem__", (i,))?;
                // Insert replacement statements at the same position.
                for (j, new_stmt) in replacements.iter().enumerate() {
                    stmts.call_method1("insert", (i + j, new_stmt))?;
                }
                i += replacements.len();
            } else {
                // Assert was not rewritten (e.g., BoolOp or chained compare).
                i += 1;
            }
        } else {
            recurse_into_children(py, ast, &stmt, &type_name, ctx)?;
            i += 1;
        }
    }
    Ok(())
}

/// Recurse into child body lists of compound statements.
fn recurse_into_children<'py>(
    py: Python<'py>,
    ast: &Bound<'py, PyModule>,
    stmt: &Bound<'py, PyAny>,
    type_name: &str,
    ctx: &mut BareAssertCtx,
) -> PyResult<()> {
    let is_fn = type_name == "FunctionDef" || type_name == "AsyncFunctionDef";
    if is_fn {
        let name: String = stmt.getattr("name")?.extract()?;
        ctx.fn_stack.push(name);
    }

    match type_name {
        "FunctionDef" | "AsyncFunctionDef" | "ClassDef" | "With" | "AsyncWith" => {
            let body: &Bound<'py, PyList> = &stmt.getattr("body")?.cast_into()?;
            rewrite_body(py, ast, body, ctx)?;
        }
        "For" | "AsyncFor" | "While" => {
            let body: &Bound<'py, PyList> = &stmt.getattr("body")?.cast_into()?;
            rewrite_body(py, ast, body, ctx)?;
            let orelse: &Bound<'py, PyList> = &stmt.getattr("orelse")?.cast_into()?;
            rewrite_body(py, ast, orelse, ctx)?;
        }
        "If" => {
            let body: &Bound<'py, PyList> = &stmt.getattr("body")?.cast_into()?;
            rewrite_body(py, ast, body, ctx)?;
            let orelse: &Bound<'py, PyList> = &stmt.getattr("orelse")?.cast_into()?;
            rewrite_body(py, ast, orelse, ctx)?;
        }
        "Match" => {
            let cases: Bound<'py, PyList> = stmt.getattr("cases")?.cast_into()?;
            for case in cases.iter() {
                let case_body: Bound<'py, PyList> = case.getattr("body")?.cast_into()?;
                rewrite_body(py, ast, &case_body, ctx)?;
            }
        }
        "Try" | "TryStar" => {
            let body: &Bound<'py, PyList> = &stmt.getattr("body")?.cast_into()?;
            rewrite_body(py, ast, body, ctx)?;
            let orelse: &Bound<'py, PyList> = &stmt.getattr("orelse")?.cast_into()?;
            rewrite_body(py, ast, orelse, ctx)?;
            let finalbody: &Bound<'py, PyList> = &stmt.getattr("finalbody")?.cast_into()?;
            rewrite_body(py, ast, finalbody, ctx)?;
            // Each ExceptHandler has its own body.
            let handlers: &Bound<'py, PyList> = &stmt.getattr("handlers")?.cast_into()?;
            for handler in handlers.iter() {
                let handler_body: &Bound<'py, PyList> = &handler.getattr("body")?.cast_into()?;
                rewrite_body(py, ast, handler_body, ctx)?;
            }
        }
        _ => {}
    }

    if is_fn {
        ctx.fn_stack.pop();
    }
    Ok(())
}

/// Dispatch a single `Assert` node for rewriting.
///
/// Returns `Some(vec_of_replacement_stmts)` if rewritten, `None` if the assert
/// should be left as-is.
fn rewrite_assert<'py>(
    py: Python<'py>,
    ast: &Bound<'py, PyModule>,
    node: &Bound<'py, PyAny>,
) -> PyResult<Option<Vec<Bound<'py, PyAny>>>> {
    let test = node.getattr("test")?;
    let msg_attr = node.getattr("msg")?;
    let lineno: i64 = node.getattr("lineno")?.extract()?;
    let col_offset: i64 = node.getattr("col_offset")?.extract()?;

    let test_type: String = test.get_type().getattr("__name__")?.extract()?;

    match test_type.as_str() {
        "Compare" => rewrite_compare(py, ast, &test, &msg_attr, lineno, col_offset),
        "BoolOp" => Ok(None),
        _ => rewrite_value(py, ast, &test, &msg_attr, lineno, col_offset),
    }
}

/// Rewrite a comparison assert: `assert x == y` →
/// ```text
/// _oxitest_l = <left>
/// _oxitest_r = <comparator>
/// if not (_oxitest_l <op> _oxitest_r):
///     raise _OxitestAssertionError(_oxitest_l, _oxitest_r, "<op>", <msg>)
/// ```
fn rewrite_compare<'py>(
    py: Python<'py>,
    ast: &Bound<'py, PyModule>,
    test: &Bound<'py, PyAny>,
    msg_attr: &Bound<'py, PyAny>,
    lineno: i64,
    col_offset: i64,
) -> PyResult<Option<Vec<Bound<'py, PyAny>>>> {
    let comparators: Bound<'py, PyList> = test.getattr("comparators")?.cast_into()?;
    if comparators.len() != 1 {
        // Chained comparison (a < b < c) — leave untouched.
        return Ok(None);
    }

    let ops: Bound<'py, PyList> = test.getattr("ops")?.cast_into()?;
    let op_node = ops.get_item(0)?;
    let op_string = match op_str(&op_node)? {
        Some(s) => s,
        None => return Ok(None),
    };

    let left = test.getattr("left")?;
    let comparator = comparators.get_item(0)?;

    // _oxitest_l = <left>
    let assign_l = make_assign(py, ast, "_oxitest_l", &left, lineno, col_offset)?;
    // _oxitest_r = <comparator>
    let assign_r = make_assign(py, ast, "_oxitest_r", &comparator, lineno, col_offset)?;

    // Build the new Compare node using temp vars for the if-test.
    let new_l = make_name_load(py, ast, "_oxitest_l", lineno, col_offset)?;
    let new_r = make_name_load(py, ast, "_oxitest_r", lineno, col_offset)?;

    let new_compare_kwargs = PyDict::new(py);
    new_compare_kwargs.set_item("left", &new_l)?;
    new_compare_kwargs.set_item("ops", PyList::new(py, [&op_node])?)?;
    new_compare_kwargs.set_item("comparators", PyList::new(py, [&new_r])?)?;
    new_compare_kwargs.set_item("lineno", lineno)?;
    new_compare_kwargs.set_item("col_offset", col_offset)?;
    let new_compare = ast
        .getattr("Compare")?
        .call((), Some(&new_compare_kwargs))?;

    // Build msg expression: use the assert's msg if present, else empty string.
    let msg_expr = if msg_attr.is_none() {
        make_constant(
            py,
            ast,
            &PyString::new(py, "").into_any(),
            lineno,
            col_offset,
        )?
    } else {
        msg_attr.clone()
    };

    // Build: _OxitestAssertionError(_oxitest_l, _oxitest_r, "<op>", msg)
    let op_const = make_constant(
        py,
        ast,
        &PyString::new(py, op_string).into_any(),
        lineno,
        col_offset,
    )?;
    let call_args = PyList::new(
        py,
        [
            &make_name_load(py, ast, "_oxitest_l", lineno, col_offset)?,
            &make_name_load(py, ast, "_oxitest_r", lineno, col_offset)?,
            &op_const,
            &msg_expr,
        ],
    )?;
    let call = make_call(
        py,
        ast,
        "_OxitestAssertionError",
        &call_args,
        lineno,
        col_offset,
    )?;
    let raise = make_raise(py, ast, &call, lineno, col_offset)?;

    // Build: if not (_oxitest_l <op> _oxitest_r): raise ...
    let if_stmt = make_if_not_raise(py, ast, &new_compare, &raise, lineno, col_offset)?;

    Ok(Some(vec![assign_l, assign_r, if_stmt]))
}

/// Rewrite a simple value assert: `assert flag` →
/// ```text
/// _oxitest_v = <test>
/// if not _oxitest_v:
///     raise _OxitestAssertionError(_oxitest_v, _oxitest_no_rhs, "", <msg>)
/// ```
fn rewrite_value<'py>(
    py: Python<'py>,
    ast: &Bound<'py, PyModule>,
    test: &Bound<'py, PyAny>,
    msg_attr: &Bound<'py, PyAny>,
    lineno: i64,
    col_offset: i64,
) -> PyResult<Option<Vec<Bound<'py, PyAny>>>> {
    // _oxitest_v = <test>
    let assign_v = make_assign(py, ast, "_oxitest_v", test, lineno, col_offset)?;

    // Build msg expression.
    let msg_expr = if msg_attr.is_none() {
        make_constant(
            py,
            ast,
            &PyString::new(py, "").into_any(),
            lineno,
            col_offset,
        )?
    } else {
        msg_attr.clone()
    };

    // Build: _OxitestAssertionError(_oxitest_v, _oxitest_no_rhs, "", msg)
    let empty_op = make_constant(
        py,
        ast,
        &PyString::new(py, "").into_any(),
        lineno,
        col_offset,
    )?;
    let call_args = PyList::new(
        py,
        [
            &make_name_load(py, ast, "_oxitest_v", lineno, col_offset)?,
            &make_name_load(py, ast, "_oxitest_no_rhs", lineno, col_offset)?,
            &empty_op,
            &msg_expr,
        ],
    )?;
    let call = make_call(
        py,
        ast,
        "_OxitestAssertionError",
        &call_args,
        lineno,
        col_offset,
    )?;
    let raise = make_raise(py, ast, &call, lineno, col_offset)?;

    // Build: if not _oxitest_v: raise ...
    let test_name = make_name_load(py, ast, "_oxitest_v", lineno, col_offset)?;
    let if_stmt = make_if_not_raise(py, ast, &test_name, &raise, lineno, col_offset)?;

    Ok(Some(vec![assign_v, if_stmt]))
}

/// Parse `source` into a Python AST, rewrite assert nodes, and return
/// `(tree, bare_asserts)` where `bare_asserts` is a `dict[str, list[int]]`
/// mapping function names to sorted bare-assert line numbers.
pub(crate) fn rewrite_asserts(
    py: Python<'_>,
    source: &str,
    filename: &str,
) -> PyResult<(Py<PyAny>, Py<PyAny>)> {
    let ast = py.import("ast")?;

    // Parse the source into an AST tree.
    let source_py = PyString::new(py, source);
    let filename_py = PyString::new(py, filename);
    let parse_kwargs = PyDict::new(py);
    parse_kwargs.set_item("source", source_py)?;
    parse_kwargs.set_item("filename", filename_py)?;
    let tree = ast.getattr("parse")?.call((), Some(&parse_kwargs))?;

    // Walk the AST and rewrite assert statements.
    let mut ctx = BareAssertCtx::new();
    let body: Bound<'_, PyList> = tree.getattr("body")?.cast_into()?;
    rewrite_body(py, &ast, &body, &mut ctx)?;

    // Fix missing locations AFTER rewriting so generated nodes get filled in.
    ast.call_method1("fix_missing_locations", (&tree,))?;

    let bare_map = ctx.into_py_dict(py)?;
    Ok((tree.into_any().unbind(), bare_map))
}
