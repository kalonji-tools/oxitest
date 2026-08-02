//! PyO3 module definition — delegates to `pipeline::run()` for all test execution.

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
mod doctest;
mod edit_distance;
mod filter;
mod import_graph;
mod inspect;
mod parallel;
mod parallel_context;
mod pipeline;
mod prescan;
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
fn builtin_markers() -> Vec<&'static str> {
    crate::filter::BUILTIN_MARKERS.to_vec()
}

#[pyfunction]
fn rewrite_asserts(
    py: Python<'_>,
    source: &str,
    filename: &str,
) -> PyResult<(Py<PyAny>, Py<PyAny>)> {
    assert_rewriter::rewrite_asserts(py, source, filename)
}

/// Emit a tracing event from Python bridge code.
///
/// Called directly by Python for developer-level traces on the serial path.
/// Worker subprocesses use LDJSON `{"type": "trace"}` instead.
#[pyfunction]
fn trace(level: &str, module: &str, message: &str) {
    match level {
        "debug" => tracing::debug!(python_module = module, "{}", message),
        "info" => tracing::info!(python_module = module, "{}", message),
        "warn" => tracing::warn!(python_module = module, "{}", message),
        "error" => tracing::error!(python_module = module, "{}", message),
        _ => tracing::trace!(python_module = module, "{}", message),
    }
}

#[pymodule]
fn _oxitest(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Register a stderr tracing subscriber. try_init() is a no-op if a global
    // subscriber is already set (first cdylib imported in this process wins).
    // Users control verbosity via RUST_LOG (e.g. RUST_LOG=_oxitest=debug).
    // The target is the crate name (this fn), not the `oxitest` package name.
    // A target that matches nothing still parses, so the "warn" fallback below
    // never applies and nothing is logged at all.
    let _ = tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("warn")),
        )
        .with_writer(reporter::tracing_writer::PbMakeWriter::new())
        .try_init();
    m.add_function(wrap_pyfunction!(run, m)?)?;
    m.add_function(wrap_pyfunction!(rewrite_asserts, m)?)?;
    m.add_function(wrap_pyfunction!(builtin_markers, m)?)?;
    m.add_function(wrap_pyfunction!(trace, m)?)?;
    Ok(())
}
