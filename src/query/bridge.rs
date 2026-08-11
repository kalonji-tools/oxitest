//! PyO3 bridge functions for the query engine.
//!
//! These free functions extract fixture and plugin metadata from the Python
//! [`FixtureSession`] for use by the query
//! subsystem.

use std::collections::HashMap;

use camino::Utf8PathBuf;
use pyo3::prelude::*;

use crate::bridge::FixtureSession;

/// Render fixture dependency tree as a formatted string for `--tree`.
pub fn tree_fixtures(
    session: &FixtureSession,
    py: Python<'_>,
    verbosity: i32,
    pattern: Option<&str>,
    use_color: bool,
) -> PyResult<String> {
    let obj = session.as_py_object(py);
    let lister = py.import("oxitest._bridge.fixture_lister")?;
    let registry = obj.getattr("_registry")?;
    let kwargs = pyo3::types::PyDict::new(py);
    kwargs.set_item("verbosity", verbosity)?;
    kwargs.set_item("pattern", pattern)?;
    kwargs.set_item("use_color", use_color)?;
    let result = lister.call_method("tree_fixtures_from_registry", (registry,), Some(&kwargs))?;
    result.extract::<String>()
}

/// Return fixture definitions as a list of field maps for the query engine.
pub fn fixture_entries(
    session: &FixtureSession,
    py: Python<'_>,
) -> PyResult<Vec<HashMap<String, String>>> {
    let obj = session.as_py_object(py);
    let module = py.import("oxitest._bridge.query_bridge")?;
    let registry = obj.getattr("_registry")?;
    let result = module.call_method1("fixture_entries", (registry,))?;
    result.extract()
}

/// Return the autouse fixtures that apply to each module, in firing order.
///
/// Each map has `module_path`, `fixture_names` and `lifetimes`, the last two
/// comma-separated and index-aligned. Keyed on the module because
/// `get_autouse` is: every test in one module has the same set.
pub fn autouse_entries(
    session: &FixtureSession,
    py: Python<'_>,
    test_files: &[Utf8PathBuf],
) -> PyResult<Vec<HashMap<String, String>>> {
    let obj = session.as_py_object(py);
    let module = py.import("oxitest._bridge.query_bridge")?;
    let paths: Vec<&str> = test_files.iter().map(|p| p.as_str()).collect();
    let result = module.call_method1("autouse_entries", (paths, obj))?;
    result.extract()
}

/// Return plugin entries as a list of field maps for the query engine.
pub fn plugin_entries(
    session: &FixtureSession,
    py: Python<'_>,
) -> PyResult<Vec<HashMap<String, String>>> {
    let obj = session.as_py_object(py);
    let module = py.import("oxitest._bridge.query_bridge")?;
    let registry = obj.getattr("_plugin_registry")?;
    let result = module.call_method1("plugin_entries", (registry,))?;
    result.extract()
}

/// Return test→fixture dependency associations for the inspect graph.
///
/// Calls into Python's `collect_module` per test file, extracting
/// `fixture_deps` from each collected item.  Each returned map has
/// `test_node_id` and `fixture_names` (comma-separated) keys.
pub fn test_fixture_deps(
    session: &FixtureSession,
    py: Python<'_>,
    test_files: &[Utf8PathBuf],
) -> PyResult<Vec<HashMap<String, String>>> {
    let obj = session.as_py_object(py);
    let module = py.import("oxitest._bridge.query_bridge")?;
    let paths: Vec<&str> = test_files.iter().map(|p| p.as_str()).collect();
    let result = module.call_method1("test_fixture_deps", (paths, obj))?;
    result.extract()
}
