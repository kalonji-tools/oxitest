//! PyO3 module definition — delegates to [`pipeline::run()`] for all test execution.

#![allow(clippy::useless_conversion)] // pyo3 macros generate this

use pyo3::prelude::*;

mod affected;
mod assert_rewriter;
mod bare_asserts;
mod bridge;
mod cache;
mod collector;
mod colors;
mod config;
mod edit_distance;
mod filter;
mod import_graph;
mod parallel;
mod pipeline;
mod python_ast;
mod query;
mod reporter;
mod retry;
mod scheduler;
mod strict;
mod types;
mod worker_result;
mod worker_session;

#[cfg(test)]
mod test_doubles;

#[pyfunction]
fn run(py: Python<'_>, args: Vec<String>) -> PyResult<i32> {
    pipeline::run(py, args)
}

#[pyfunction]
fn rewrite_asserts(
    py: Python<'_>,
    source: &str,
    filename: &str,
) -> PyResult<(Py<PyAny>, Py<PyAny>)> {
    assert_rewriter::rewrite_asserts(py, source, filename)
}

#[pymodule]
fn _oxitest(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Register a stderr tracing subscriber. try_init() is a no-op if a global
    // subscriber is already set (first cdylib imported in this process wins).
    // Users control verbosity via RUST_LOG (e.g. RUST_LOG=oxitest=debug).
    let _ = tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("warn")),
        )
        .with_writer(reporter::tracing_writer::PbMakeWriter::new())
        .try_init();
    m.add_function(wrap_pyfunction!(run, m)?)?;
    m.add_function(wrap_pyfunction!(rewrite_asserts, m)?)?;
    Ok(())
}
