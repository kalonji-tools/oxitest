//! PyO3 bridge — the boundary between Rust orchestration and Python execution.
//!
//! Defines the data contracts for deserializing Python results ([`TestResult`],
//! [`CollectedItem`], [`RawViolation`]) and wraps the Python function calls
//! (`collect_module`, `run_test`, `FixtureSession` lifecycle).
//!
//! **Contract rule:** struct field names MUST stay in sync with
//! `python/oxitest/_bridge/result.py`. A mismatch silently drops data.

use std::collections::HashMap;

use camino::{Utf8Path, Utf8PathBuf};
use pyo3::prelude::*;

use crate::types::{CollectError, Frame, NodeId, RawOutcome, TestItem, TestOutcome};

/// Single traceback frame extracted from Python. Field names MUST stay in sync with
/// `python/oxitest/_bridge/result.py` `Frame`.
#[derive(FromPyObject)]
struct BridgeFrame {
    file: String,
    lineno: usize,
    name: String,
    line: String,
}

/// Test result extracted from Python. Field names MUST stay in sync with `python/oxitest/_bridge/result.py`.
#[derive(FromPyObject)]
struct TestResult {
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

    /// Load plugins by calling into the Python plugin loader and store the
    /// registry on the session object for dependency injection.
    pub fn load_plugins(
        &self,
        py: Python<'_>,
        plugins: &[String],
        plugin_settings: &HashMap<String, toml::Value>,
    ) -> PyResult<()> {
        let loader = py.import("oxitest._bridge.plugin_loader")?;
        let plugin_list: Vec<&str> = plugins.iter().map(|s| s.as_str()).collect();
        let settings_json = match serde_json::to_string(plugin_settings) {
            Ok(json) => json,
            Err(e) => {
                tracing::warn!(error = %e, "failed to serialize plugin settings; plugins will receive empty config");
                "{}".to_owned()
            }
        };
        let json_mod = py.import("json")?;
        let plugin_configs = json_mod.call_method1("loads", (&settings_json,))?;
        let registry = loader.call_method1("load_plugins", (plugin_list, plugin_configs))?;
        self.0.bind(py).setattr("_plugin_registry", registry)?;
        Ok(())
    }

    /// Initialize the async backend by resolving the config name against plugins.
    pub fn init_async_backend(&self, py: Python<'_>, backend_name: &str) -> PyResult<()> {
        let backend_mod = py.import("oxitest._bridge._async_backend")?;
        let registry = self.0.bind(py).getattr("_plugin_registry")?;
        let backend = backend_mod.call_method1("resolve_backend", (backend_name, registry))?;
        self.0.bind(py).setattr("_async_backend", backend)?;
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
    is_async: bool,
}

/// Typed violation kind coming from Python. Variants map 1-to-1 to the
/// string values produced by `python/oxitest/_bridge/result.py`.
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum ViolationKind {
    BareAssert,
    DictParametrize,
    MissingMarkReason,
    SingleCaseParametrize,
    Unknown,
}

impl<'a, 'py> pyo3::FromPyObject<'a, 'py> for ViolationKind {
    type Error = pyo3::PyErr;

    fn extract(ob: pyo3::Borrowed<'a, 'py, pyo3::PyAny>) -> pyo3::PyResult<Self> {
        let s: String = ob.extract()?;
        Ok(match s.as_str() {
            "bare_assert" => ViolationKind::BareAssert,
            "dict_parametrize" => ViolationKind::DictParametrize,
            "missing_mark_reason" => ViolationKind::MissingMarkReason,
            "single_case_parametrize" => ViolationKind::SingleCaseParametrize,
            _ => ViolationKind::Unknown,
        })
    }
}

/// Raw violation extracted from Python. Field names MUST stay in sync with
/// `python/oxitest/_bridge/result.py` `CollectedViolation`.
#[derive(pyo3::FromPyObject, Debug)]
pub(crate) struct RawViolation {
    pub node_id: String,
    pub kind: ViolationKind,
    pub detail: String,
}

/// Fetch plugin reporter objects from the session's plugin registry.
pub fn get_plugin_reporters(py: Python<'_>, session: &FixtureSession) -> PyResult<Vec<Py<PyAny>>> {
    let registry = session.0.bind(py).getattr("_plugin_registry")?;
    let reporters: Vec<Py<PyAny>> = registry.getattr("reporters")?.extract::<Vec<Py<PyAny>>>()?;
    Ok(reporters)
}

/// Variant of `collect_module` that accepts a raw Python session object.
///
/// The trait-based `ModuleCollector` implementation calls this directly,
/// bypassing the `Option<&FixtureSession>` indirection.
pub(crate) fn collect_module_with_session_obj(
    py: Python<'_>,
    path: &Utf8Path,
    session_obj: Bound<'_, PyAny>,
    collect_violations: bool,
) -> Result<(Vec<TestItem>, Vec<RawViolation>), CollectError> {
    let importer = py
        .import("oxitest._bridge.importer")
        .map_err(|e: PyErr| CollectError::PyError(e.to_string()))?;

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
            is_async: item.is_async,
        })
        .collect();

    Ok((items_vec, raw_violations))
}

/// Variant of `run_test` that accepts a raw Python session object.
///
/// The trait-based `TestRunner` implementation calls this directly,
/// bypassing the `Option<&FixtureSession>` indirection.
pub(crate) fn run_test_with_session_obj(
    py: Python<'_>,
    item: &TestItem,
    session_obj: Bound<'_, PyAny>,
    default_timeout: Option<u64>,
) -> TestOutcome {
    try_run_test_with_session_obj(py, item, session_obj, default_timeout).unwrap_or_else(|e| {
        TestOutcome::Error {
            message: format!("{} — {}", item.node_id, e),
            file: String::new(),
            lineno: 0,
            source_line: String::new(),
            frames: vec![],
        }
    })
}

fn try_run_test_with_session_obj(
    py: Python<'_>,
    item: &TestItem,
    session_obj: Bound<'_, PyAny>,
    default_timeout: Option<u64>,
) -> PyResult<TestOutcome> {
    let executor = py.import("oxitest._bridge.executor")?;
    let path_str = item.module_path.as_str();

    let param_id_obj: Bound<'_, PyAny> = match &item.param_id {
        Some(pid) => pid.as_str().into_pyobject(py)?.into_any(),
        None => py.None().into_bound(py),
    };

    let timeout_obj: Bound<'_, PyAny> = match default_timeout {
        Some(t) => (t as i64).into_pyobject(py)?.into_any(),
        None => py.None().into_bound(py),
    };

    let r: TestResult = executor
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

    let frames: Vec<Frame> = r
        .frames
        .into_iter()
        .map(|f| Frame {
            file: f.file,
            lineno: f.lineno,
            name: f.name,
            line: f.line,
        })
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
