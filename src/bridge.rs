//! PyO3 bridge — the boundary between Rust orchestration and Python execution.
//!
//! Defines the data contracts for deserializing Python results ([`TestResult`],
//! [`CollectedItem`], [`RawViolation`]) and wraps the Python function calls
//! (`collect_module`, `run_test`, `FixtureSession` lifecycle).
//!
//! **Contract rule:** struct field names MUST stay in sync with
//! `python/oxitest/_bridge/result.py`. A mismatch silently drops data.

use std::collections::HashMap;
use std::sync::Arc;

use camino::{Utf8Path, Utf8PathBuf};
use pyo3::prelude::*;

use crate::types::{CollectError, Frame, LineNo, NodeId, TestItem, TestOutcome};
use crate::worker_result::WorkerOutcome;

fn py_collect_err(e: PyErr) -> CollectError {
    CollectError::PyError(e.to_string())
}

impl<'a, 'py> pyo3::FromPyObject<'a, 'py> for crate::worker_result::RawFrame {
    type Error = pyo3::PyErr;

    fn extract(ob: pyo3::Borrowed<'a, 'py, pyo3::PyAny>) -> pyo3::PyResult<Self> {
        Ok(Self {
            file: ob.getattr("file")?.extract()?,
            lineno: ob.getattr("lineno")?.extract()?,
            name: ob.getattr("name")?.extract()?,
            line: ob.getattr("line")?.extract()?,
            locals: ob.getattr("locals")?.extract()?,
        })
    }
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
    frames: Vec<crate::worker_result::RawFrame>,
    field_diffs: Vec<(String, String, String)>,
}

/// Long-lived Python fixture session held across the test loop.
pub struct FixtureSession(Py<PyAny>);

impl FixtureSession {
    /// Create a session by loading fixtures from all conftest paths.
    ///
    /// Returns the session and any strict-mode violations detected during
    /// fixture registration (e.g. missing return type annotations).
    pub fn new(
        py: Python<'_>,
        conftest_paths: &[Utf8PathBuf],
    ) -> PyResult<(Self, Vec<RawViolation>)> {
        let loader = py.import("oxitest._bridge.conftest_loader")?;
        let paths: Vec<&str> = conftest_paths.iter().map(|p| p.as_str()).collect();
        let result = loader.call_method1("create_session", (paths,))?;
        let (session, violations): (Bound<'_, PyAny>, Vec<RawViolation>) = result.extract()?;
        Ok((Self(session.into()), violations))
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

    /// Returns connected components of shared fixture dependencies.
    /// Each inner Vec is a sorted group of fixture names that must co-locate.
    /// Returns an empty Vec on any Python error (advisory-only).
    pub fn shared_fixture_groups(&self, py: Python<'_>) -> Vec<Vec<String>> {
        self.0
            .bind(py)
            .call_method0("shared_fixture_groups")
            .and_then(|v| v.extract::<Vec<Vec<String>>>())
            .unwrap_or_default()
    }

    /// Returns this session as a bound Python object for passing to bridge calls.
    pub(crate) fn as_py_object<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        self.0.bind(py).clone()
    }
}

#[cfg(test)]
impl FixtureSession {
    /// Create a dummy session wrapping `None` for unit tests that never call
    /// Python methods on the session.
    pub(crate) fn stub(py: Python<'_>) -> Self {
        Self(py.None().into())
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
    fixture_names: Vec<String>,
    fixref_names: Vec<String>,
}

/// Typed violation kind coming from Python. Variants map 1-to-1 to the
/// string values produced by `python/oxitest/_bridge/result.py`.
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum ViolationKind {
    BareAssert,
    DictParametrize,
    InvalidModuleMark,
    MissingMarkReason,
    MissingReturnAnnotation,
    SingleCaseParametrize,
    UnusedFixture,
    Unknown,
}

impl<'a, 'py> pyo3::FromPyObject<'a, 'py> for ViolationKind {
    type Error = pyo3::PyErr;

    fn extract(ob: pyo3::Borrowed<'a, 'py, pyo3::PyAny>) -> pyo3::PyResult<Self> {
        let s: String = ob.extract()?;
        Ok(match s.as_str() {
            "bare_assert" => ViolationKind::BareAssert,
            "dict_parametrize" => ViolationKind::DictParametrize,
            "invalid_module_mark" => ViolationKind::InvalidModuleMark,
            "missing_mark_reason" => ViolationKind::MissingMarkReason,
            "missing_return_annotation" => ViolationKind::MissingReturnAnnotation,
            "single_case_parametrize" => ViolationKind::SingleCaseParametrize,
            "unused_fixture" => ViolationKind::UnusedFixture,
            _ => ViolationKind::Unknown,
        })
    }
}

/// Raw violation extracted from Python. Field names MUST stay in sync with
/// `python/oxitest/_bridge/result.py` `CollectedViolation`.
#[derive(pyo3::FromPyObject, Debug, Clone)]
pub(crate) struct RawViolation {
    pub node_id: String,
    pub kind: ViolationKind,
    pub detail: String,
}

/// Fetch reporter plugin objects from the session's plugin registry via PyO3.
///
/// Calls `_plugin_registry.reporters` on the Python session object and extracts
/// the list as owned `Py<PyAny>` handles. Each object is later wrapped in a
/// [`PyPluginReporter`](crate::reporter::plugin::PyPluginReporter).
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
        .map_err(py_collect_err)?;

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

    let (raw_items, raw_violations): (Vec<CollectedItem>, Vec<RawViolation>) =
        result.extract().map_err(py_collect_err)?;

    let items_vec = raw_items
        .into_iter()
        .map(|item| TestItem {
            node_id: NodeId::new(path_str, &item.fn_name, item.param_id.as_deref()),
            module_path: path.to_owned(),
            fn_name: item.fn_name,
            lineno: LineNo::new(item.lineno),
            markers: item.markers,
            param_id: item.param_id,
            param_values: item.param_values,
            is_async: item.is_async,
            fixture_names: item.fixture_names,
            fixref_names: item.fixref_names,
        })
        .collect();

    Ok((items_vec, raw_violations))
}

/// Validate that all collected fixture names can resolve in the registry.
///
/// Returns `(node_id, fixture_name)` pairs for names that cannot resolve.
/// FixtureRef-resolved parameters (from `@parametrize`) are excluded.
pub(crate) fn validate_fixture_names(
    py: Python<'_>,
    session: &FixtureSession,
    items: &[Arc<TestItem>],
) -> Result<Vec<(NodeId, String)>, CollectError> {
    let session_obj = session.as_py_object(py);

    let dicts: Vec<Bound<'_, pyo3::types::PyDict>> = items
        .iter()
        .map(|item| {
            let dict = pyo3::types::PyDict::new(py);
            let nid: &str = &item.node_id;
            dict.set_item("node_id", nid).unwrap();
            dict.set_item("fixture_names", &item.fixture_names).unwrap();
            dict.set_item("fixref_names", &item.fixref_names).unwrap();
            dict
        })
        .collect();

    let items_list = pyo3::types::PyList::new(py, &dicts).map_err(py_collect_err)?;

    let result = session_obj
        .call_method1("validate_fixture_names", (items_list,))
        .map_err(py_collect_err)?;

    let pairs: Vec<(String, String)> = result.extract().map_err(py_collect_err)?;

    Ok(pairs
        .into_iter()
        .map(|(nid, name)| (NodeId::from_raw(&nid), name))
        .collect())
}

/// Return all fixture names known to the registry.
pub(crate) fn registered_fixture_names(
    py: Python<'_>,
    session: &FixtureSession,
) -> Result<Vec<String>, CollectError> {
    let session_obj = session.as_py_object(py);
    let result = session_obj
        .call_method0("registered_fixture_names")
        .map_err(py_collect_err)?;
    result.extract().map_err(py_collect_err)
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
    opts: crate::pipeline::execution::DebugOptions<'_>,
) -> TestOutcome {
    try_run_test_with_session_obj(py, item, session_obj, default_timeout, opts).unwrap_or_else(
        |e| TestOutcome::Error {
            message: format!("{} — {}", item.node_id, e),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            frames: vec![],
        },
    )
}

/// Call `FixtureSession.find_unused_fixtures()` to detect fixtures defined
/// in conftest but never referenced by any collected test.
pub(crate) fn find_unused_fixtures(
    py: Python<'_>,
    session: &FixtureSession,
    items: &[std::sync::Arc<TestItem>],
) -> PyResult<Vec<RawViolation>> {
    let items_list = pyo3::types::PyList::empty(py);
    for item in items {
        let dict = pyo3::types::PyDict::new(py);
        let fixture_names =
            pyo3::types::PyList::new(py, item.fixture_names.iter().map(String::as_str))?;
        dict.set_item("fixture_names", fixture_names)?;
        items_list.append(dict)?;
    }
    let result: Vec<(String, String)> = session
        .0
        .bind(py)
        .call_method1("find_unused_fixtures", (items_list,))?
        .extract()?;
    Ok(result
        .into_iter()
        .map(|(conftest_path, fixture_name)| RawViolation {
            node_id: conftest_path,
            kind: ViolationKind::UnusedFixture,
            detail: fixture_name,
        })
        .collect())
}

/// Start coverage collection via the Python bridge.
///
/// Calls `resolve_provider_standalone()` to get the default provider,
/// then calls `provider.start({})`.
/// Returns the Python provider object for later stop/report calls.
pub fn start_coverage(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let module = py.import("oxitest._bridge._coverage")?;
    let provider = module.call_method0("resolve_provider_standalone")?;
    provider.call_method1("start", (py.None(),))?;
    Ok(provider.into())
}

/// Stop coverage and generate report.
///
/// Calls `provider.stop()` then `provider.report(format)`.
/// Errors are printed as warnings, never fatal.
pub fn stop_and_report_coverage(
    py: Python<'_>,
    provider: &Py<PyAny>,
    format: &str,
) -> PyResult<()> {
    let provider = provider.bind(py);
    provider.call_method0("stop")?;

    let cov_module = py.import("oxitest._bridge._coverage")?;
    let fmt_cls = cov_module.getattr("CovReportFormat")?;
    let fmt = fmt_cls.call1((format,))?;
    provider.call_method1("report", (&fmt,))?;
    Ok(())
}

fn try_run_test_with_session_obj(
    py: Python<'_>,
    item: &TestItem,
    session_obj: Bound<'_, PyAny>,
    default_timeout: Option<u64>,
    opts: crate::pipeline::execution::DebugOptions<'_>,
) -> PyResult<TestOutcome> {
    let executor = py.import("oxitest._bridge.executor")?;

    // Construct a Python TestMeta object with test identity fields.
    let test_meta_mod = py.import("oxitest._bridge._test_meta")?;
    let test_meta_cls = test_meta_mod.getattr("TestMeta")?;

    let param_id_obj: Bound<'_, PyAny> = match &item.param_id {
        Some(pid) => pid.as_str().into_pyobject(py)?.into_any(),
        None => py.None().into_bound(py),
    };

    let markers_list = pyo3::types::PyList::new(py, &item.markers)?;
    let markers_frozen = pyo3::types::PyFrozenSet::new(py, markers_list)?;

    let node_id_str: &str = &item.node_id;
    let meta_obj = test_meta_cls.call1((
        item.module_path.as_str(),
        item.fn_name.as_str(),
        node_id_str,
        &param_id_obj,
        markers_frozen,
    ))?;

    let timeout_obj: Bound<'_, PyAny> = match default_timeout {
        Some(t) => (t as i64).into_pyobject(py)?.into_any(),
        None => py.None().into_bound(py),
    };

    let debug_obj: Bound<'_, PyAny> = match opts.debug_mode {
        Some(mode) => mode.into_pyobject(py)?.into_any(),
        None => py.None().into_bound(py),
    };

    let keep_tmp_obj: Bound<'_, PyAny> = match opts.keep_tmp {
        Some(mode) => mode.into_pyobject(py)?.into_any(),
        None => py.None().into_bound(py),
    };

    let show_locals_obj: Bound<'_, PyAny> = pyo3::types::PyBool::new(py, opts.show_locals)
        .to_owned()
        .into_any();
    let show_internals_obj: Bound<'_, PyAny> = pyo3::types::PyBool::new(py, opts.show_internals)
        .to_owned()
        .into_any();

    let r: TestResult = executor
        .call_method1(
            "run_test",
            (
                meta_obj,
                session_obj,
                &timeout_obj,
                debug_obj,
                keep_tmp_obj,
                show_locals_obj,
                show_internals_obj,
            ),
        )?
        .extract()?;

    let r_outcome = convert_test_result(r);
    Ok(TestOutcome::from(r_outcome))
}

/// Convert a Python [`TestResult`] into a [`WorkerOutcome`].
///
/// Pure function — testable without a Python runtime.
fn convert_test_result(r: TestResult) -> WorkerOutcome {
    let frames: Vec<Frame> = r.frames.into_iter().map(Into::into).collect();
    let lineno = LineNo::new(r.lineno);
    let file = Utf8PathBuf::from(r.file);

    match r.status.as_str() {
        "passed" => WorkerOutcome::Passed {
            no_message_lines: r.no_message_lines,
        },
        "warned" => WorkerOutcome::Warned {
            reason: r.message,
            no_message_lines: r.no_message_lines,
        },
        "failed" => WorkerOutcome::Failed {
            message: r.message,
            file,
            lineno,
            source_line: r.source_line,
            left: r.left,
            right: r.right,
            op: r.op,
            frames,
            field_diffs: r.field_diffs,
        },
        "skipped" => WorkerOutcome::Skipped { reason: r.message },
        "xfailed" => WorkerOutcome::XFailed { reason: r.message },
        "xpassed" => WorkerOutcome::XPassed { strict: r.strict },
        "timeout" => WorkerOutcome::Timeout { reason: r.message },
        _ => WorkerOutcome::Error {
            message: r.message,
            file,
            lineno,
            source_line: r.source_line,
            frames,
        },
    }
}

#[cfg(test)]
mod convert_tests {
    use super::*;
    use crate::worker_result::WorkerOutcome;

    fn make_test_result(status: &str) -> TestResult {
        TestResult {
            status: status.to_string(),
            message: "test msg".to_string(),
            file: "test.py".to_string(),
            lineno: 42,
            source_line: "assert True".to_string(),
            no_message_lines: vec![],
            left: String::new(),
            right: String::new(),
            op: String::new(),
            strict: false,
            exc_type: String::new(),
            frames: vec![],
            field_diffs: vec![],
        }
    }

    #[test]
    fn convert_passed() {
        let outcome = convert_test_result(make_test_result("passed"));
        assert!(matches!(outcome, WorkerOutcome::Passed { .. }));
    }

    #[test]
    fn convert_failed() {
        let outcome = convert_test_result(make_test_result("failed"));
        assert!(matches!(outcome, WorkerOutcome::Failed { .. }));
    }

    #[test]
    fn convert_skipped() {
        let outcome = convert_test_result(make_test_result("skipped"));
        assert!(matches!(outcome, WorkerOutcome::Skipped { .. }));
    }

    #[test]
    fn convert_xfailed() {
        let outcome = convert_test_result(make_test_result("xfailed"));
        assert!(matches!(outcome, WorkerOutcome::XFailed { .. }));
    }

    #[test]
    fn convert_xpassed() {
        let outcome = convert_test_result(make_test_result("xpassed"));
        assert!(matches!(outcome, WorkerOutcome::XPassed { .. }));
    }

    #[test]
    fn convert_timeout() {
        let outcome = convert_test_result(make_test_result("timeout"));
        assert!(matches!(outcome, WorkerOutcome::Timeout { .. }));
    }

    #[test]
    fn convert_warned() {
        let outcome = convert_test_result(make_test_result("warned"));
        assert!(matches!(outcome, WorkerOutcome::Warned { .. }));
    }

    #[test]
    fn convert_unknown_status_becomes_error() {
        let outcome = convert_test_result(make_test_result("banana"));
        assert!(matches!(outcome, WorkerOutcome::Error { .. }));
    }
}
