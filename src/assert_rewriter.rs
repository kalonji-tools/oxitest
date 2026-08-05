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

    /// Convert to a Python `dict[str, list[int]]`.
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
pub fn rewrite_asserts(
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

#[cfg(test)]
mod tests {
    use super::*;

    /// Run a closure with the Python GIL acquired and the `ast` module imported.
    ///
    /// Uses `Python::attach` (pyo3 0.29 API), which initialises the interpreter
    /// automatically when running outside an embedded Python process.
    fn with_ast<F, T>(f: F) -> T
    where
        F: FnOnce(Python<'_>, &Bound<'_, PyModule>) -> T,
    {
        Python::initialize();
        Python::attach(|py| {
            let ast = py.import("ast").expect("ast module should be importable");
            f(py, &ast)
        })
    }

    // ── op_str ────────────────────────────────────────────────────────────────

    #[test]
    fn op_str_known_operators() {
        with_ast(|_py, ast| {
            let cases: &[(&str, &str)] = &[
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
            for (type_name, expected) in cases {
                let op = ast
                    .getattr(type_name)
                    .unwrap_or_else(|_| panic!("ast.{type_name} should exist"))
                    .call0()
                    .unwrap_or_else(|_| panic!("ast.{type_name}() should be callable"));
                let result =
                    op_str(&op).unwrap_or_else(|e| panic!("op_str failed for {type_name}: {e}"));
                assert!(
                    result == Some(*expected),
                    "op_str for {type_name} should return Some({expected:?}), got {result:?}"
                );
            }
        });
    }

    #[test]
    fn op_str_unknown_operator_returns_none() {
        with_ast(|_py, ast| {
            // ast.Add is a binary operator, not a compare operator — not in OP_MAP.
            let add = ast
                .getattr("Add")
                .expect("ast.Add should exist")
                .call0()
                .expect("ast.Add() should be callable");
            let result = op_str(&add).expect("op_str should not raise for unknown op");
            assert!(
                result.is_none(),
                "op_str for unknown operator should return None, got {result:?}"
            );
        });
    }

    // ── AST node constructors ─────────────────────────────────────────────────

    #[test]
    fn make_name_load_produces_correct_node() {
        with_ast(|py, ast| {
            let node =
                make_name_load(py, ast, "my_var", 3, 7).expect("make_name_load should succeed");
            let id: String = node.getattr("id").unwrap().extract().unwrap();
            let lineno: i64 = node.getattr("lineno").unwrap().extract().unwrap();
            let col: i64 = node.getattr("col_offset").unwrap().extract().unwrap();
            let ctx_type: String = node
                .getattr("ctx")
                .unwrap()
                .get_type()
                .getattr("__name__")
                .unwrap()
                .extract()
                .unwrap();
            assert!(id == "my_var", "id should be 'my_var', got {id:?}");
            assert!(lineno == 3, "lineno should be 3, got {lineno}");
            assert!(col == 7, "col_offset should be 7, got {col}");
            assert!(ctx_type == "Load", "ctx should be Load, got {ctx_type:?}");
        });
    }

    #[test]
    fn make_name_store_produces_correct_node() {
        with_ast(|py, ast| {
            let node =
                make_name_store(py, ast, "target", 1, 0).expect("make_name_store should succeed");
            let id: String = node.getattr("id").unwrap().extract().unwrap();
            let ctx_type: String = node
                .getattr("ctx")
                .unwrap()
                .get_type()
                .getattr("__name__")
                .unwrap()
                .extract()
                .unwrap();
            assert!(id == "target", "id should be 'target', got {id:?}");
            assert!(ctx_type == "Store", "ctx should be Store, got {ctx_type:?}");
        });
    }

    #[test]
    fn make_constant_produces_correct_node() {
        with_ast(|py, ast| {
            let val = pyo3::types::PyString::new(py, "hello").into_any();
            let node = make_constant(py, ast, &val, 2, 4).expect("make_constant should succeed");
            let value: String = node.getattr("value").unwrap().extract().unwrap();
            let lineno: i64 = node.getattr("lineno").unwrap().extract().unwrap();
            assert!(value == "hello", "value should be 'hello', got {value:?}");
            assert!(lineno == 2, "lineno should be 2, got {lineno}");
        });
    }

    #[test]
    fn make_assign_produces_correct_node() {
        with_ast(|py, ast| {
            let rhs = pyo3::types::PyString::new(py, "rhs_val").into_any();
            let node = make_assign(py, ast, "x", &rhs, 5, 0).expect("make_assign should succeed");
            let type_name: String = node
                .get_type()
                .getattr("__name__")
                .unwrap()
                .extract()
                .unwrap();
            assert!(
                type_name == "Assign",
                "node should be Assign, got {type_name:?}"
            );
            // targets is a list; first element should be a Name(Store).
            let targets = node.getattr("targets").unwrap();
            let first = targets.get_item(0).unwrap();
            let first_id: String = first.getattr("id").unwrap().extract().unwrap();
            assert!(
                first_id == "x",
                "target name should be 'x', got {first_id:?}"
            );
        });
    }

    #[test]
    fn make_raise_produces_correct_node() {
        with_ast(|py, ast| {
            let exc = pyo3::types::PyString::new(py, "err").into_any();
            let node = make_raise(py, ast, &exc, 9, 0).expect("make_raise should succeed");
            let type_name: String = node
                .get_type()
                .getattr("__name__")
                .unwrap()
                .extract()
                .unwrap();
            assert!(
                type_name == "Raise",
                "node should be Raise, got {type_name:?}"
            );
            let lineno: i64 = node.getattr("lineno").unwrap().extract().unwrap();
            assert!(lineno == 9, "lineno should be 9, got {lineno}");
        });
    }

    #[test]
    fn make_call_produces_correct_node() {
        with_ast(|py, ast| {
            let args = pyo3::types::PyList::empty(py);
            let node =
                make_call(py, ast, "my_func", &args, 4, 2).expect("make_call should succeed");
            let type_name: String = node
                .get_type()
                .getattr("__name__")
                .unwrap()
                .extract()
                .unwrap();
            assert!(
                type_name == "Call",
                "node should be Call, got {type_name:?}"
            );
            // func should be a Name(id="my_func", ctx=Load).
            let func = node.getattr("func").unwrap();
            let func_id: String = func.getattr("id").unwrap().extract().unwrap();
            assert!(
                func_id == "my_func",
                "func.id should be 'my_func', got {func_id:?}"
            );
        });
    }

    // ── BareAssertCtx ─────────────────────────────────────────────────────────

    #[test]
    fn bare_assert_ctx_empty_stack_records_nothing() {
        let mut ctx = BareAssertCtx::new();
        // Recording with no function on the stack is a no-op.
        ctx.record(42);
        assert!(
            ctx.by_fn.is_empty(),
            "by_fn should be empty when fn_stack is empty"
        );
    }

    #[test]
    fn bare_assert_ctx_single_function() {
        let mut ctx = BareAssertCtx::new();
        ctx.fn_stack.push("test_foo".to_string());
        ctx.record(5);
        ctx.record(10);
        assert!(
            ctx.by_fn.get("test_foo") == Some(&vec![5, 10]),
            "should record lines 5 and 10 under 'test_foo'"
        );
    }

    #[test]
    fn bare_assert_ctx_nested_functions_attributed_to_outermost() {
        let mut ctx = BareAssertCtx::new();
        ctx.fn_stack.push("outer".to_string());
        ctx.fn_stack.push("inner".to_string());
        ctx.record(7);
        // Line 7 should be attributed to "outer" (index 0), not "inner".
        assert!(
            ctx.by_fn.get("outer") == Some(&vec![7]),
            "record should attribute to outermost function"
        );
        assert!(
            !ctx.by_fn.contains_key("inner"),
            "inner function should not appear in by_fn"
        );
    }

    #[test]
    fn bare_assert_ctx_into_py_dict_sorted() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = BareAssertCtx::new();
            ctx.fn_stack.push("test_bar".to_string());
            // Insert lines out of order to verify that into_py_dict sorts them.
            ctx.record(20);
            ctx.record(5);
            ctx.record(12);

            let dict_obj = ctx.into_py_dict(py).expect("into_py_dict should succeed");
            let dict = dict_obj.bind(py);
            let lines: Bound<'_, PyAny> = dict
                .get_item("test_bar")
                .expect("test_bar key should exist in dict");
            let lines_vec: Vec<i64> = lines.extract().expect("should extract as Vec<i64>");
            assert!(
                lines_vec == vec![5, 12, 20],
                "lines should be sorted ascending, got {lines_vec:?}"
            );
        });
    }

    #[test]
    fn bare_assert_ctx_multiple_functions_after_pop() {
        let mut ctx = BareAssertCtx::new();
        ctx.fn_stack.push("test_a".to_string());
        ctx.record(1);
        ctx.fn_stack.pop();

        ctx.fn_stack.push("test_b".to_string());
        ctx.record(2);
        ctx.fn_stack.pop();

        assert!(
            ctx.by_fn.get("test_a") == Some(&vec![1]),
            "test_a should have line 1"
        );
        assert!(
            ctx.by_fn.get("test_b") == Some(&vec![2]),
            "test_b should have line 2"
        );
    }
}
