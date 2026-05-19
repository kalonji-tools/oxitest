#![allow(clippy::useless_conversion)] // pyo3 macros generate this

use pyo3::prelude::*;

mod bridge;
mod cache;
mod collector;
mod config;
mod filter;
mod marker;
mod parallel;
mod pipeline;
mod reporter;
mod scheduler;
mod strict;
mod types;
mod worker_result;

#[pyfunction]
fn run(py: Python<'_>, args: Vec<String>) -> PyResult<i32> {
    pipeline::run(py, args)
}

#[pymodule]
fn _oxitest(m: &Bound<'_, PyModule>) -> PyResult<()> {
    let _ = tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("warn")),
        )
        .with_writer(std::io::stderr)
        .try_init();
    m.add_function(wrap_pyfunction!(run, m)?)?;
    Ok(())
}
