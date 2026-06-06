//! PyO3 bridge functions for the query engine.
//!
//! These free functions extract fixture and plugin metadata from the Python
//! [`FixtureSession`](crate::bridge::FixtureSession) for use by the query
//! subsystem.

use std::collections::HashMap;

use pyo3::prelude::*;

use crate::bridge::FixtureSession;

/// Render fixture dependency tree as a formatted string for `--tree`.
pub(crate) fn tree_fixtures(
    session: &FixtureSession,
    py: Python<'_>,
    verbosity: i32,
    pattern: Option<&str>,
    use_color: bool,
) -> PyResult<String> {
    let obj = session.as_py_object(py);
    let lister = py.import("oxitest._bridge.fixture_lister")?;
    let registry = obj.getattr("_registry")?;
    let result = lister.call_method1(
        "tree_fixtures_from_registry",
        (registry, verbosity, pattern, use_color),
    )?;
    result.extract::<String>()
}

/// Return fixture definitions as a list of field maps for the query engine.
pub(crate) fn fixture_entries(
    session: &FixtureSession,
    py: Python<'_>,
) -> PyResult<Vec<HashMap<String, String>>> {
    let obj = session.as_py_object(py);
    let module = py.import("oxitest._bridge.query_bridge")?;
    let registry = obj.getattr("_registry")?;
    let result = module.call_method1("fixture_entries", (registry,))?;
    result.extract()
}

/// Return plugin entries as a list of field maps for the query engine.
pub(crate) fn plugin_entries(
    session: &FixtureSession,
    py: Python<'_>,
) -> PyResult<Vec<HashMap<String, String>>> {
    let obj = session.as_py_object(py);
    let module = py.import("oxitest._bridge.query_bridge")?;
    let registry = obj.getattr("_plugin_registry")?;
    let result = module.call_method1("plugin_entries", (registry,))?;
    result.extract()
}
