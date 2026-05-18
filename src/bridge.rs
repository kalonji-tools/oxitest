use std::collections::HashMap;

use camino::{Utf8Path, Utf8PathBuf};
use pyo3::prelude::*;

use crate::types::{CollectError, NodeId, RawOutcome, TestItem, TestOutcome};

/// Single traceback frame extracted from Python. Field names MUST stay in sync with
/// `python/oxitest/_bridge/result.py` `Frame`.
#[derive(FromPyObject)]
struct BridgeFrame {
    file: String,
    lineno: usize,
    name: String,
    line: String,
}

/// Bridge result extracted from Python. Field names MUST stay in sync with `python/oxitest/_bridge/result.py`.
#[derive(FromPyObject)]
struct BridgeResult {
    status: String,
    message: String,
    file: String,
    lineno: usize,
    source_line: String,
    no_message_lines: Vec<usize>,
    left: String,
    right: String,
    op: String,
    strict: bool,
    #[allow(dead_code)] // Extracted for PyO3 contract sync; used only on the Python side.
    exc_type: String,
    frames: Vec<BridgeFrame>,
}

/// Long-lived Python fixture session held across the test loop.
pub struct FixtureSession(Py<PyAny>);

impl FixtureSession {
    /// Create a session by loading fixtures from all conftest paths.
    pub fn new(py: Python<'_>, conftest_paths: &[Utf8PathBuf]) -> PyResult<Self> {
        let loader = py.import("oxitest._bridge.conftest_loader")?;
        let paths: Vec<&str> = conftest_paths.iter().map(|p| p.as_str()).collect();
        let session = loader.call_method1("create_session", (paths,))?;
        Ok(Self(session.into()))
    }

    /// Signal that all tests in the module have finished; runs module teardowns.
    pub fn end_module(&self, py: Python<'_>, module_path: &Utf8Path) -> PyResult<()> {
        self.0
            .bind(py)
            .call_method1("end_module", (module_path.as_str(),))?;
        Ok(())
    }

    /// Run session-scoped teardowns at the end of the run.
    pub fn end_session(&self, py: Python<'_>) -> PyResult<()> {
        self.0.bind(py).call_method0("end_session")?;
        Ok(())
    }

    /// Load plugins by calling into the Python plugin loader.
    pub fn load_plugins(
        &self,
        py: Python<'_>,
        plugins: &[String],
        plugin_settings: &HashMap<String, toml::Value>,
    ) -> PyResult<()> {
        let loader = py.import("oxitest._bridge.plugin_loader")?;
        let plugin_list: Vec<&str> = plugins.iter().map(|s| s.as_str()).collect();
        let settings_json = serde_json::to_string(plugin_settings).unwrap_or_default();
        loader.call_method1("init_plugins", (plugin_list, settings_json))?;
        Ok(())
    }

    /// Returns sorted names of all fixtures marked with `shared=True` in the registry.
    /// Returns an empty Vec on any Python error (treated as "no shared fixtures").
    /// Unlike `end_module`/`end_session`, errors are absorbed here because this
    /// method is advisory-only; a failure must not abort the run.
    pub fn shared_fixture_names(&self, py: Python<'_>) -> Vec<String> {
        self.0
            .bind(py)
            .call_method0("shared_fixture_names")
            .and_then(|v| v.extract::<Vec<String>>())
            .unwrap_or_default()
    }

    /// Returns this session as a bound Python object for passing to bridge calls.
    pub(crate) fn as_py_object<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        self.0.bind(py).clone()
    }
}

/// Collected test item extracted from Python. Field names MUST stay in sync with
/// `python/oxitest/_bridge/result.py` `CollectedItem`.
#[derive(FromPyObject)]
struct CollectedItem {
    fn_name: String,
    lineno: usize,
    markers: Vec<String>,
    param_id: Option<String>,
    param_values: Vec<(String, String)>,
}

/// Raw violation extracted from Python. Field names MUST stay in sync with
/// `python/oxitest/_bridge/result.py` `CollectedViolation`.
#[derive(pyo3::FromPyObject, Debug)]
pub(crate) struct RawViolation {
    pub node_id: String,
    pub kind: String, // "bare_assert" | "dict_parametrize" | "missing_mark_reason"
    pub detail: String,
}

/// Fetch plugin reporter objects from the Python plugin registry.
pub fn get_plugin_reporters(py: Python<'_>) -> PyResult<Vec<Py<PyAny>>> {
    let loader = py.import("oxitest._bridge.plugin_loader")?;
    let registry = loader.call_method0("get_registry")?;
    let reporters: Vec<Py<PyAny>> = registry
        .getattr("reporters")?
        .extract::<Vec<Py<PyAny>>>()?;
    Ok(reporters)
}

pub fn collect_module(
    py: Python<'_>,
    path: &Utf8Path,
    session: Option<&FixtureSession>,
    collect_violations: bool,
) -> Result<(Vec<TestItem>, Vec<RawViolation>), CollectError> {
    let importer = py
        .import("oxitest._bridge.importer")
        .map_err(|e: PyErr| CollectError::PyError(e.to_string()))?;

    let session_obj = session
        .map(|s| s.as_py_object(py))
        .unwrap_or_else(|| py.None().into_bound(py));

    let path_str = path.as_str();
    let result = importer
        .call_method1(
            "collect_module",
            (path_str, session_obj, collect_violations),
        )
        .map_err(|e: PyErr| {
            // e.to_string() formats as "ImportError: <message>"; strip the redundant
            // type prefix because CollectError::ImportError already labels the context.
            let full = e.to_string();
            let message = full
                .strip_prefix("ImportError: ")
                .unwrap_or(&full)
                .to_string();
            CollectError::ImportError {
                path: path.to_owned(),
                message,
            }
        })?;

    let (raw_items, raw_violations): (Vec<CollectedItem>, Vec<RawViolation>) = result
        .extract()
        .map_err(|e: PyErr| CollectError::PyError(e.to_string()))?;

    let items_vec = raw_items
        .into_iter()
        .map(|item| TestItem {
            node_id: NodeId::new(path_str, &item.fn_name, item.param_id.as_deref()),
            module_path: path.to_owned(),
            fn_name: item.fn_name,
            lineno: item.lineno,
            markers: item.markers,
            param_id: item.param_id,
            param_values: item.param_values,
        })
        .collect();

    Ok((items_vec, raw_violations))
}

pub fn run_test(
    py: Python<'_>,
    item: &TestItem,
    session: Option<&FixtureSession>,
    default_timeout: Option<u64>,
) -> TestOutcome {
    try_run_test(py, item, session, default_timeout).unwrap_or_else(|e| TestOutcome::Error {
        message: format!("{} — {}", item.node_id, e),
        file: String::new(),
        lineno: 0,
        source_line: String::new(),
        frames: vec![],
    })
}

fn try_run_test(
    py: Python<'_>,
    item: &TestItem,
    session: Option<&FixtureSession>,
    default_timeout: Option<u64>,
) -> PyResult<TestOutcome> {
    let executor = py.import("oxitest._bridge.executor")?;
    let path_str = item.module_path.as_str();

    let session_obj = session
        .map(|s| s.as_py_object(py))
        .unwrap_or_else(|| py.None().into_bound(py));

    let param_id_obj: Bound<'_, PyAny> = match &item.param_id {
        Some(pid) => pid.as_str().into_pyobject(py)?.into_any(),
        None => py.None().into_bound(py),
    };

    let timeout_obj: Bound<'_, PyAny> = match default_timeout {
        Some(t) => (t as i64).into_pyobject(py)?.into_any(),
        None => py.None().into_bound(py),
    };

    let r: BridgeResult = executor
        .call_method1(
            "run_test",
            (
                path_str,
                item.fn_name.as_str(),
                session_obj,
                &param_id_obj,
                &timeout_obj,
            ),
        )?
        .extract()?;

    let frames: Vec<(String, usize, String, String)> = r
        .frames
        .iter()
        .map(|f| (f.file.clone(), f.lineno, f.name.clone(), f.line.clone()))
        .collect();

    Ok(TestOutcome::from_raw(RawOutcome {
        status: r.status.as_str(),
        message: &r.message,
        file: &r.file,
        lineno: r.lineno,
        source_line: &r.source_line,
        no_message_lines: &r.no_message_lines,
        left: &r.left,
        right: &r.right,
        op: &r.op,
        strict: r.strict,
        frames: &frames,
    }))
}
