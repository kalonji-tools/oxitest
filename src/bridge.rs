//! PyO3 bridge — the boundary between Rust orchestration and Python execution.
//!
//! Defines the data contracts for deserializing Python results (`TestResult`,
//! `CollectedItem`, `RawViolation`) and wraps the Python function calls
//! (`collect_module`, `run_test`, `FixtureSession` lifecycle).
//!
//! **Contract rule:** struct field names MUST stay in sync with
//! `python/oxitest/_bridge/result.py`. A mismatch silently drops data.

use std::collections::HashMap;
use std::sync::Arc;

use camino::{Utf8Path, Utf8PathBuf};
use pyo3::prelude::*;

use crate::types::{CollectError, Frame, LineNo, MarkerSet, NodeId, TestItem, TestOutcome};

fn py_collect_err(e: PyErr) -> CollectError {
    CollectError::PyError(e.to_string())
}

/// What `@oxi.arrange` resolves to: the connected components, and a map from
/// each resolved fixture name back to the spelling the user wrote.
///
/// The second half exists because a builtin is registered under its private
/// impl class name — `TempDir` resolves to `_TempDirFixture` — and a diagnostic
/// must never show a name the user cannot type (#2045).
pub type ArrangedComponents = (Vec<Vec<String>>, HashMap<String, String>);

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

impl<'a, 'py> pyo3::FromPyObject<'a, 'py> for crate::types::LocalVar {
    type Error = pyo3::PyErr;
    fn extract(ob: pyo3::Borrowed<'a, 'py, pyo3::PyAny>) -> pyo3::PyResult<Self> {
        let (name, repr): (String, String) = ob.extract()?;
        Ok(Self { name, repr })
    }
}

impl<'a, 'py> pyo3::FromPyObject<'a, 'py> for crate::types::FieldDiff {
    type Error = pyo3::PyErr;
    fn extract(ob: pyo3::Borrowed<'a, 'py, pyo3::PyAny>) -> pyo3::PyResult<Self> {
        let (field, left, right): (String, String, String) = ob.extract()?;
        Ok(Self { field, left, right })
    }
}

/// Long-lived Python fixture session held across the test loop.
pub struct FixtureSession(Py<PyAny>);

impl FixtureSession {
    /// Create an empty session. It holds builtins only until a caller registers
    /// declaration homes into it — `register_declaration_homes_for_files` for the
    /// `query` and `inspect` surfaces, or the collection loop for a run (#1722).
    ///
    /// Returns the session and a violation list that is always empty: an empty
    /// session has registered nothing, so it has nothing to report. The `Vec`
    /// stays in the signature because callers destructure it (#1720).
    ///
    /// `rootdir` is appended to `sys.path` so test modules can import sibling
    /// utility modules (#1780).
    pub fn new(py: Python<'_>, rootdir: &camino::Utf8Path) -> PyResult<(Self, Vec<RawViolation>)> {
        let factory = py.import("oxitest._bridge._session_factory")?;
        let kwargs = pyo3::types::PyDict::new(py);
        kwargs.set_item("rootdir", rootdir.as_str())?;
        let result = factory.call_method("create_session", (), Some(&kwargs))?;
        // No violations: an empty session has nothing to report. The Vec
        // stays in the signature because callers destructure it (#1720).
        Ok((Self(result.into()), Vec::new()))
    }

    /// Signal that all tests in the module have finished; runs module teardowns.
    pub fn end_module(&self, py: Python<'_>, module_path: &Utf8Path) -> PyResult<()> {
        self.0
            .bind(py)
            .call_method1("end_module", (module_path.as_str(),))?;
        Ok(())
    }

    /// Signal that every module in the package has finished; runs package teardowns.
    ///
    /// Peer to [`Self::end_module`] one tier up. Package disposal cannot ride on
    /// `end_task`: a serial run uses one session for the whole run, so the
    /// task drain fires long after the package's last test.
    pub fn end_package(&self, py: Python<'_>, package_path: &Utf8Path) -> PyResult<()> {
        self.0
            .bind(py)
            .call_method1("end_package", (package_path.as_str(),))?;
        Ok(())
    }

    /// Run the teardowns whose lifetime ends with this task group.
    ///
    /// Peer to [`Self::end_process`]. Always call this one first: a
    /// process-lifetime value may outlive a task-lifetime one, never the
    /// reverse (#1777).
    pub fn end_task(&self, py: Python<'_>) -> PyResult<()> {
        self.0.bind(py).call_method0("end_task")?;
        Ok(())
    }

    /// Run the teardowns whose lifetime ends with this *process* (#1777).
    ///
    /// Fires once per process, not once per task group — which for the
    /// coordinator means once after every execution phase has finished.
    pub fn end_process(&self, py: Python<'_>) -> PyResult<()> {
        self.0.bind(py).call_method0("end_process")?;
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
        self.0.bind(py).setattr("async_backend", backend)?;
        Ok(())
    }

    /// Returns sorted names of the fixtures declared `lifetime="module"`.
    /// Read by the wide-lifetime warning, which is about the tier's rebuild
    /// behaviour rather than about arrangement (#1848).
    /// Returns an empty Vec on any Python error.
    /// Unlike `end_module`/`end_task`, errors are absorbed here because this
    /// method is advisory-only; a failure must not abort the run.
    pub fn module_lifetime_fixture_names(&self, py: Python<'_>) -> Vec<String> {
        self.0
            .bind(py)
            .call_method0("module_lifetime_fixture_names")
            .and_then(|v| v.extract::<Vec<String>>())
            .unwrap_or_default()
    }

    /// Anchor directories of the activated plugins registered into this session.
    ///
    /// Read once at the start of collection to seed the per-directory dedupe
    /// set. Errors are absorbed into an empty Vec, matching the advisory
    /// getters around it: failing to seed costs a duplicate registration the
    /// shadow order already resolves, which is not worth aborting a run (#1717).
    pub fn plugin_anchor_dirs(&self, py: Python<'_>) -> Vec<String> {
        self.0
            .bind(py)
            .call_method0("plugin_anchor_dirs")
            .and_then(|v| v.extract::<Vec<String>>())
            .unwrap_or_default()
    }

    /// Record an activated plugin's anchor directory on the session.
    ///
    /// # Errors
    ///
    /// Returns a `CollectError::PyError` if the call raises.
    pub fn record_plugin_anchor(
        &self,
        py: Python<'_>,
        anchor_dir: &Utf8Path,
    ) -> Result<(), CollectError> {
        self.0
            .bind(py)
            .call_method1("record_plugin_anchor", (anchor_dir.as_str(),))
            .map(|_| ())
            .map_err(py_collect_err)
    }

    /// Returns sorted names of fixtures declared `lifetime="process"`.
    ///
    /// Run-constant, so the caller reads it once and reuses it — the same
    /// idiom as [`Self::module_lifetime_fixture_names`], and for the same reason:
    /// the registry is fully populated before any test runs.
    ///
    /// Errors are absorbed into an empty Vec. This only decorates a warning
    /// about a worker that was already killed; failing the run over it would
    /// be worse than saying nothing (#1777).
    pub fn process_lifetime_fixture_names(&self, py: Python<'_>) -> Vec<String> {
        self.0
            .bind(py)
            .call_method0("process_lifetime_fixture_names")
            .and_then(|v| v.extract::<Vec<String>>())
            .unwrap_or_default()
    }

    /// Returns `(name, lifetime, lineno)` for fixtures declared in *that file*.
    ///
    /// The source of truth for ADR-0009's scheduler co-location and Rule 4
    /// checks (#1859). Registration is by marker attribute, so an unrecognized
    /// import spelling registers a real fixture that prescan's AST cannot see;
    /// asking the registry makes the two agree by construction.
    ///
    /// Keyed on the declaring file rather than its anchor: a directory may hold
    /// both a `__fixtures__.py` and an `__init__.py`, which share an anchor, and
    /// an anchor-keyed query gives each of them the other's declarations.
    ///
    /// Unlike [`Self::process_lifetime_fixture_names`], errors are **not**
    /// absorbed into an empty Vec. That idiom suits a decoration on a warning;
    /// here an empty result silently disables enforcement, which is the exact
    /// failure mode this change exists to remove. The caller falls back to the
    /// prescan AST explicitly, and only when registration failed.
    pub(crate) fn module_source_declarations(
        &self,
        py: Python<'_>,
        defining_module_path: &Utf8Path,
    ) -> Result<Vec<(String, String, u32)>, CollectError> {
        self.0
            .bind(py)
            .getattr("registry")
            .and_then(|registry| {
                registry.call_method1(
                    "module_source_declarations",
                    (defining_module_path.as_str(),),
                )
            })
            .and_then(|v| v.extract::<Vec<(String, String, u32)>>())
            .map_err(py_collect_err)
    }

    /// Returns connected components of the fixtures named by `@oxi.arrange`.
    /// Each inner Vec is a sorted group of fixture names that must co-locate.
    /// `arranged` carries `(kind, value)` pairs, where `kind` is `"Type"` or
    /// `"Name"` — the two [`crate::types::ArrangedEntry`] variants. The tag has
    /// to cross: a type's `__name__` is not a registry key, and only the
    /// registry knows a public type maps to a private impl class (#2045).
    ///
    /// Returns the components, plus a map from each resolved fixture name back
    /// to the spelling the user wrote, so a diagnostic never shows a private
    /// name such as `_TempDirFixture`.
    ///
    /// A Python error is **not** swallowed here. An `@injectable` type that
    /// resolves to no fixture must refuse the run rather than silently forming
    /// no component, which is the defect #2045 removes.
    pub fn arranged_fixture_groups(
        &self,
        py: Python<'_>,
        arranged: Vec<(String, String)>,
    ) -> PyResult<ArrangedComponents> {
        self.0
            .bind(py)
            .call_method1("arranged_fixture_groups", (arranged,))
            .and_then(|v| v.extract::<ArrangedComponents>())
    }

    /// Returns the subset of `module_paths` that can resolve a `lifetime="module"`
    /// fixture. Each of those modules must stay inside one dispatch phase (#1750):
    /// a phase owns its own fixture session, so a split builds the fixture twice.
    ///
    /// Visibility rather than usage — `fixture_deps` carries only annotated
    /// parameters, so a usage test cannot see `fx.<ns>.<name>` access.
    ///
    /// Returns an empty Vec on any Python error. The caller then keeps today's
    /// partitioning, which is the pre-#1750 behaviour rather than a suite-wide
    /// serialisation.
    pub fn modules_with_visible_module_lifetime(
        &self,
        py: Python<'_>,
        module_paths: Vec<String>,
    ) -> Vec<String> {
        self.0
            .bind(py)
            .call_method1("modules_with_visible_module_lifetime", (module_paths,))
            .and_then(|v| v.extract::<Vec<String>>())
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

/// Drain diagnostics from the Python session and convert to `DiagnosticEntry`.
///
/// Reads `session.diagnostics`, converts each to a `DiagnosticEntry`, clears the
/// Python list, and returns the entries. Callers (pipeline code) push these into
/// `RunStats.diagnostics.entries`.
pub fn drain_session_diagnostics(
    py: Python<'_>,
    session: &FixtureSession,
) -> Vec<crate::reporter::stats::DiagnosticEntry> {
    let session_obj = session.as_py_object(py);
    drain_diagnostics_from(&session_obj).unwrap_or_default()
}

fn drain_diagnostics_from(
    session_obj: &Bound<'_, PyAny>,
) -> PyResult<Vec<crate::reporter::stats::DiagnosticEntry>> {
    use crate::reporter::stats::DiagnosticEntry;

    let diag_list = session_obj.getattr("diagnostics")?;
    let len: usize = diag_list.len()?;
    if len == 0 {
        return Ok(Vec::new());
    }

    let mut entries = Vec::with_capacity(len);
    for i in 0..len {
        let diag = diag_list.get_item(i)?;
        let severity_str: String = diag.getattr("severity")?.extract()?;
        let context: String = diag.getattr("context")?.extract()?;
        let message: String = diag.getattr("message")?.extract()?;
        let file: String = diag.getattr("file")?.extract()?;
        let lineno: u32 = diag.getattr("lineno")?.extract()?;

        entries.push(DiagnosticEntry::from_wire(
            &severity_str,
            &context,
            message,
            file,
            lineno,
        ));
    }

    // Clear the Python list
    diag_list.call_method0("clear")?;
    Ok(entries)
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
    fixture_deps: Vec<(String, String)>,
    fixref_deps: Vec<(String, String)>,
    arranged: Vec<RawArrangedEntry>,
}

/// Raw extraction type for one `@oxi.arrange(...)` entry.
///
/// Python stores `tuple[type | str, ...]`. We inspect each element: if it is a
/// Python `type` (class), we extract its `__name__`; otherwise we extract the
/// string directly. The result is stored as [`crate::types::ArrangedEntry`].
struct RawArrangedEntry(crate::types::ArrangedEntry);

impl<'a, 'py> pyo3::FromPyObject<'a, 'py> for RawArrangedEntry {
    type Error = pyo3::PyErr;

    fn extract(ob: pyo3::Borrowed<'a, 'py, pyo3::PyAny>) -> pyo3::PyResult<Self> {
        // Check if the object is a Python type (class) by calling builtins.isinstance(ob, type).
        // PyAny::is_instance_of::<pyo3::types::PyType> checks if *ob itself* is a type.
        use crate::types::ArrangedEntry;
        if ob.is_instance_of::<pyo3::types::PyType>() {
            let name: String = ob.getattr("__name__")?.extract()?;
            Ok(Self(ArrangedEntry::Type(name)))
        } else {
            let name: String = ob.extract()?;
            Ok(Self(ArrangedEntry::Name(name)))
        }
    }
}

/// Typed violation kind coming from Python. Variants map 1-to-1 to the
/// string values produced by `python/oxitest/_bridge/result.py`.
#[derive(Debug, Clone, PartialEq)]
pub enum ViolationKind {
    BareAssert,
    BroadFixtureType,
    DictParametrize,
    InvalidModuleMark,
    MissingMarkReason,
    MissingReturnAnnotation,
    ModuleLevelDef,
    SingleCaseParametrize,
    TestReturnsValue,
    UnusedFixture,
    Unknown,
}

impl<'a, 'py> pyo3::FromPyObject<'a, 'py> for ViolationKind {
    type Error = pyo3::PyErr;

    /// # Machine-parsed — do not rewrite `ViolationKind::` to `Self::`
    ///
    /// `python/tests/test_bridge_contract.py` scrapes these arms with the regex
    /// `"(\w+)"\s*=>\s*ViolationKind::` to check that every Python `ViolationKind`
    /// value has a Rust arm here. Spelling them `Self::` makes that regex match
    /// nothing, and the contract test fails with an empty Rust-side set.
    ///
    /// The `_ => Unknown` arm has no string literal and is not scraped; it keeps the
    /// long spelling so the block does not read half-one-way, half-the-other.
    #[expect(
        clippy::use_self,
        reason = "test_bridge_contract.py regex-scrapes these arms for `ViolationKind::` to keep bridge.rs and result.py in sync"
    )]
    fn extract(ob: pyo3::Borrowed<'a, 'py, pyo3::PyAny>) -> pyo3::PyResult<Self> {
        let s: String = ob.extract()?;
        Ok(match s.as_str() {
            "bare_assert" => ViolationKind::BareAssert,
            "broad_fixture_type" => ViolationKind::BroadFixtureType,
            "dict_parametrize" => ViolationKind::DictParametrize,
            "invalid_module_mark" => ViolationKind::InvalidModuleMark,
            "missing_mark_reason" => ViolationKind::MissingMarkReason,
            "missing_return_annotation" => ViolationKind::MissingReturnAnnotation,
            "module_level_def" => ViolationKind::ModuleLevelDef,
            "single_case_parametrize" => ViolationKind::SingleCaseParametrize,
            "test_returns_value" => ViolationKind::TestReturnsValue,
            "unused_fixture" => ViolationKind::UnusedFixture,
            _ => ViolationKind::Unknown,
        })
    }
}

/// Raw violation extracted from Python. Field names MUST stay in sync with
/// `python/oxitest/_bridge/result.py` `CollectedViolation`.
#[derive(pyo3::FromPyObject, Debug, Clone)]
pub struct RawViolation {
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
pub fn collect_module_with_session_obj(
    py: Python<'_>,
    path: &Utf8Path,
    session_obj: Bound<'_, PyAny>,
    collect_violations: bool,
) -> Result<(Vec<TestItem>, Vec<RawViolation>), CollectError> {
    let importer = py
        .import("oxitest._bridge.importer")
        .map_err(py_collect_err)?;

    let path_str = path.as_str();
    let kwargs = pyo3::types::PyDict::new(py);
    kwargs
        .set_item("collect_violations", collect_violations)
        .map_err(py_collect_err)?;
    let result = importer
        .call_method("collect_module", (path_str, session_obj), Some(&kwargs))
        .map_err(|e: PyErr| {
            let full = e.to_string();
            // Extract the Python exception class name so we can route
            // CollectionError (arrange-collision) separately from ImportError.
            // e.to_string() formats as "<ExcType>: <message>".
            let exc_class = e
                .get_type(py)
                .name()
                .map(|n| n.to_string())
                .unwrap_or_default();
            if exc_class == "CollectionError" {
                // Arrange-collision or similar collection-time misconfiguration —
                // strip the "CollectionError: " prefix (already in exc_class label)
                // and surface with a clear classification.
                let message = full
                    .strip_prefix("CollectionError: ")
                    .unwrap_or(&full)
                    .to_string();
                CollectError::PyError(format!("collection error: {message}"))
            } else {
                // ImportError (syntax error, missing module, etc.) — strip the
                // redundant type prefix because CollectError::ImportError already
                // labels the context.
                let message = full
                    .strip_prefix("ImportError: ")
                    .unwrap_or(&full)
                    .to_string();
                CollectError::ImportError {
                    path: path.to_owned(),
                    message,
                }
            }
        })?;

    let (raw_items, raw_violations): (Vec<CollectedItem>, Vec<RawViolation>) =
        result.extract().map_err(py_collect_err)?;

    let items_vec = raw_items
        .into_iter()
        .map(|item| TestItem {
            node_id: NodeId::new(path_str, &item.fn_name, item.param_id.as_deref()),
            fn_name: Arc::from(item.fn_name.as_str()),
            lineno: LineNo::new(item.lineno),
            markers: MarkerSet::from(item.markers),
            param_id: item.param_id,
            param_values: item.param_values.into_iter().map(Into::into).collect(),
            is_async: item.is_async,
            fixture_deps: item.fixture_deps,
            fixref_deps: item.fixref_deps,
            arranged: item.arranged.into_iter().map(|r| r.0).collect(),
        })
        .collect();

    Ok((items_vec, raw_violations))
}

/// Register fixtures from a `__fixtures__.py` sibling into the session registry.
///
/// Called during collection when a test module's directory contains a
/// `__fixtures__.py` whose prescan revealed at least one `@oxi.fixture`
/// decorator. Extracts the `FixtureRegistry` from the Python session object
/// and calls `importer.register_module_source_fixtures_for_module` to import
/// the fixture module and register its fixtures.
///
/// # Errors
///
/// Returns a `CollectError::PyError` if the Python import or registration
/// raises (e.g. syntax error in `__fixtures__.py`, or fixture name collision).
pub fn register_fixture_module_for_path(
    py: Python<'_>,
    session_obj: Bound<'_, PyAny>,
    fixture_module_path: &Utf8Path,
    anchor_package_path: &Utf8Path,
) -> Result<(), CollectError> {
    let registry = session_obj.getattr("registry").map_err(py_collect_err)?;

    let importer = py
        .import("oxitest._bridge.importer")
        .map_err(py_collect_err)?;

    let kwargs = pyo3::types::PyDict::new(py);
    kwargs
        .set_item("registry", registry)
        .map_err(py_collect_err)?;
    kwargs
        .set_item("fixture_module_path", fixture_module_path.as_str())
        .map_err(py_collect_err)?;
    kwargs
        .set_item("anchor_package_path", anchor_package_path.as_str())
        .map_err(py_collect_err)?;

    importer
        .call_method(
            "register_module_source_fixtures_for_module",
            (),
            Some(&kwargs),
        )
        .map_err(|e| CollectError::PyError(e.to_string()))?;

    Ok(())
}

/// One activated plugin package that declares fixtures.
///
/// Mirrors `plugin_loader.PluginFixtureHome`. Resolved once after plugin
/// activation, when every plugin module is imported (#1717).
pub struct PluginFixtureHome {
    pub plugin_module: String,
    pub anchor_dir: Utf8PathBuf,
    pub namespace: String,
    pub autouse: Vec<String>,
}

/// Ask the plugin loader which activated plugins declare fixtures.
///
/// # Errors
///
/// Returns a `CollectError::PyError` if a namespace is reserved, invalid, or
/// claimed by two plugins — all three are refusals, not warnings.
pub fn plugin_fixture_homes(
    py: Python<'_>,
    plugins: &[String],
    plugin_settings: &HashMap<String, toml::Value>,
) -> Result<Vec<PluginFixtureHome>, CollectError> {
    let loader = py
        .import("oxitest._bridge.plugin_loader")
        .map_err(py_collect_err)?;

    // Warn rather than fail, matching `load_plugins`: an unserialisable
    // settings table costs the plugin its config, and saying so beats a
    // namespace silently reverting to the module name.
    let settings_json = match serde_json::to_string(plugin_settings) {
        Ok(json) => json,
        Err(e) => {
            tracing::warn!(
                error = %e,
                "failed to serialize plugin settings; plugin fixture homes will \
                 resolve with default namespace and no autouse"
            );
            "{}".to_owned()
        }
    };
    let json_mod = py.import("json").map_err(py_collect_err)?;
    let settings = json_mod
        .call_method1("loads", (&settings_json,))
        .map_err(py_collect_err)?;

    let kwargs = pyo3::types::PyDict::new(py);
    kwargs
        .set_item("activated_modules", plugins)
        .map_err(py_collect_err)?;
    kwargs
        .set_item("plugin_settings", settings)
        .map_err(py_collect_err)?;

    let homes = loader
        .call_method("plugin_fixture_homes", (), Some(&kwargs))
        .map_err(|e| CollectError::PyError(e.to_string()))?;

    let mut out = Vec::new();
    for home in homes.try_iter().map_err(py_collect_err)? {
        let home = home.map_err(py_collect_err)?;
        out.push(PluginFixtureHome {
            plugin_module: home
                .getattr("plugin_module")
                .and_then(|v| v.extract())
                .map_err(py_collect_err)?,
            // Python spells this anchor; Rust compares it against directories
            // from `std::fs::canonicalize`. Normalised here, where the struct
            // is built, rather than at either consumer: `anchor_dir` reaches
            // both `register_plugin_home` and `record_plugin_anchor`, and the
            // worker `(module, anchor)` pairs derive from this field, so
            // normalising at one consumer leaves the others spelling it the
            // Python way and the dedup seed misses (#1767, #1717).
            // `test_plugin_anchor_seed_matches_when_imported_off_sys_path`
            // carries the mechanism and the platform it bites on.
            anchor_dir: home
                .getattr("anchor_dir")
                .and_then(|v| v.extract::<String>())
                .map(|s| crate::collector::normalize_path(Utf8Path::new(&s)))
                .map_err(py_collect_err)?,
            namespace: home
                .getattr("namespace")
                .and_then(|v| v.extract())
                .map_err(py_collect_err)?,
            autouse: home
                .getattr("autouse")
                .and_then(|v| v.extract())
                .map_err(py_collect_err)?,
        });
    }
    Ok(out)
}

/// Register an activated plugin's `__fixtures__.py` into the session registry.
///
/// The plugin counterpart of [`register_fixture_module_for_path`]. Differs only
/// in which Python registrar receives the module: plugin declarations become
/// ambient `PluginModuleSource` defs rather than anchored `ModuleSource` ones,
/// and carry the namespace and the user's autouse opt-in list (#1717).
///
/// # Errors
///
/// Returns a `CollectError::PyError` if the import or registration raises —
/// a syntax error in the plugin's `__fixtures__.py`, a `package` lifetime
/// declaration, or a reserved namespace.
pub fn register_plugin_fixture_module(
    py: Python<'_>,
    session_obj: Bound<'_, PyAny>,
    fixture_module_path: &Utf8Path,
    plugin_module: &str,
    namespace: &str,
    autouse_names: &[String],
) -> Result<(), CollectError> {
    let registry = session_obj.getattr("registry").map_err(py_collect_err)?;

    let importer = py
        .import("oxitest._bridge.importer")
        .map_err(py_collect_err)?;

    let kwargs = pyo3::types::PyDict::new(py);
    kwargs
        .set_item("registry", registry)
        .map_err(py_collect_err)?;
    kwargs
        .set_item("fixture_module_path", fixture_module_path.as_str())
        .map_err(py_collect_err)?;
    kwargs
        .set_item("plugin_module", plugin_module)
        .map_err(py_collect_err)?;
    kwargs
        .set_item("namespace", namespace)
        .map_err(py_collect_err)?;
    kwargs
        .set_item("autouse_names", autouse_names)
        .map_err(py_collect_err)?;

    importer
        .call_method(
            "register_plugin_source_fixtures_for_module",
            (),
            Some(&kwargs),
        )
        .map_err(|e| CollectError::PyError(e.to_string()))?;

    Ok(())
}

/// Validate that all collected fixture names can resolve in the registry.
///
/// Returns `(node_id, fixture_name)` pairs for names that cannot resolve.
/// FixtureRef-resolved parameters (from `@parametrize`) are excluded.
/// Resolve every static `fx.` access prescan found, before any test runs (#1758).
///
/// Returns `(module_path, lineno, message)` per access that cannot resolve.
///
/// The verdict is computed in Python against the live registry rather than in
/// Rust against a second model of it. B1 exemptions live on `FixtureDef.anchor`
/// — conftest, plugin and builtin declarations are ambient — and a Rust
/// reimplementation would have to re-derive them, which is exactly where the
/// validated prototype for this issue was thin.
/// `collected` scopes the check to the modules that were actually imported.
/// Prescan fills `fx_usages` before the import set is narrowed, and the
/// registry is filled from what survives that narrowing — so the two must be
/// intersected or a deselected module's access is judged against a catalog that
/// never loaded its declaring package.
pub fn validate_fx_boundaries(
    py: Python<'_>,
    session: &FixtureSession,
    fx_usages: &std::collections::HashMap<Utf8PathBuf, Vec<crate::prescan::FxUsage>>,
    collected: &std::collections::HashSet<&str>,
) -> Result<Vec<(String, usize, String)>, CollectError> {
    if fx_usages.is_empty() {
        return Ok(vec![]);
    }
    let session_obj = session.as_py_object(py);

    // Sorted so a multi-module refusal reports in a stable order; a HashMap
    // walk would reorder the diagnostic between runs of the same tree.
    let mut paths: Vec<&Utf8PathBuf> = fx_usages
        .keys()
        .filter(|path| collected.contains(path.as_str()))
        .collect();
    paths.sort();
    if paths.is_empty() {
        return Ok(vec![]);
    }

    let modules = pyo3::types::PyList::empty(py);
    for path in paths {
        let dict = pyo3::types::PyDict::new(py);
        dict.set_item("module_path", path.as_str())
            .map_err(py_collect_err)?;
        let usages = pyo3::types::PyList::empty(py);
        for usage in &fx_usages[path] {
            let entry = pyo3::types::PyDict::new(py);
            entry
                .set_item("namespace", usage.namespace.as_deref())
                .map_err(py_collect_err)?;
            entry
                .set_item("name", &usage.name)
                .map_err(py_collect_err)?;
            entry
                .set_item("lineno", *usage.lineno)
                .map_err(py_collect_err)?;
            usages.append(entry).map_err(py_collect_err)?;
        }
        dict.set_item("usages", usages).map_err(py_collect_err)?;
        modules.append(dict).map_err(py_collect_err)?;
    }

    let result = session_obj
        .call_method1("validate_fx_boundaries", (modules,))
        .map_err(py_collect_err)?;
    result.extract().map_err(py_collect_err)
}

pub fn validate_fixture_names(
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
            dict.set_item("node_id", nid).map_err(py_collect_err)?;
            let fixture_names: Vec<&str> =
                item.fixture_deps.iter().map(|(q, _)| q.as_str()).collect();
            dict.set_item("fixture_names", fixture_names)
                .map_err(py_collect_err)?;
            let fixref_names: Vec<&str> =
                item.fixref_deps.iter().map(|(q, _)| q.as_str()).collect();
            dict.set_item("fixref_names", fixref_names)
                .map_err(py_collect_err)?;
            // Pass full (qualifier, type_name) pairs so the validator can filter builtins
            dict.set_item("fixture_deps", &item.fixture_deps)
                .map_err(py_collect_err)?;
            Ok(dict)
        })
        .collect::<Result<Vec<_>, CollectError>>()?;

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
pub fn registered_fixture_names(
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
pub fn run_test_with_session_obj(
    py: Python<'_>,
    item: &TestItem,
    session_obj: Bound<'_, PyAny>,
    default_timeout: Option<u64>,
    opts: crate::pipeline::execution::DebugOptions<'_>,
) -> TestOutcome {
    try_run_test_with_session_obj(py, item, session_obj, default_timeout, opts).unwrap_or_else(
        |e| {
            TestOutcome::Error(Box::new(crate::types::FailureDiagnostic::sentinel(
                format!("{} — {}", item.node_id, e),
            )))
        },
    )
}

/// Call `FixtureSession.find_unused_fixtures()` to detect fixtures defined
/// in a declaration file but never referenced by any collected test.
pub fn find_unused_fixtures(
    py: Python<'_>,
    session: &FixtureSession,
    items: &[std::sync::Arc<TestItem>],
) -> PyResult<Vec<RawViolation>> {
    let items_list = pyo3::types::PyList::empty(py);
    for item in items {
        let dict = pyo3::types::PyDict::new(py);
        let fixture_names: Vec<&str> = item.fixture_deps.iter().map(|(q, _)| q.as_str()).collect();
        let fixture_names_py = pyo3::types::PyList::new(py, fixture_names)?;
        dict.set_item("fixture_names", fixture_names_py)?;
        items_list.append(dict)?;
    }
    let result: Vec<(String, String)> = session
        .0
        .bind(py)
        .call_method1("find_unused_fixtures", (items_list,))?
        .extract()?;
    Ok(result
        .into_iter()
        .map(|(declaration_path, fixture_name)| RawViolation {
            node_id: declaration_path,
            kind: ViolationKind::UnusedFixture,
            detail: fixture_name,
        })
        .collect())
}

/// Start coverage collection via the Python bridge.
///
/// Calls `resolve_provider_standalone()` to get the default provider,
/// then calls `provider.start()`.
/// Returns the Python provider object for later stop/report calls.
pub fn start_coverage(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let module = py.import("oxitest._bridge._coverage")?;
    let provider = module.call_method0("resolve_provider_standalone")?;
    provider.call_method0("start")?;
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
    let report_kwargs = pyo3::types::PyDict::new(py);
    report_kwargs.set_item("fmt", &fmt)?;
    provider.call_method("report", (), Some(&report_kwargs))?;
    Ok(())
}

/// Activate deferred plugins and replace the registry with the updated version.
///
/// After `load_plugins()` defers plugins with `oxitest_cli_extension`, this
/// function calls the module-level `activate_deferred_plugins()` to construct
/// typed configs from merged pyproject + CLI values, activate all remaining
/// deferred plugins, and return a new frozen registry.
pub fn activate_deferred_plugins(
    py: Python<'_>,
    session: &FixtureSession,
    plugin_settings: &std::collections::HashMap<String, toml::Value>,
    plugin_cli_values: &std::collections::HashMap<
        String,
        std::collections::HashMap<String, toml::Value>,
    >,
) -> PyResult<()> {
    let loader = py.import("oxitest._bridge.plugin_loader")?;
    let registry = session.0.bind(py).getattr("_plugin_registry")?;

    let settings_json = serde_json::to_string(plugin_settings).unwrap_or_else(|_| "{}".to_owned());
    let cli_json = serde_json::to_string(plugin_cli_values).unwrap_or_else(|_| "{}".to_owned());

    let new_registry = loader.call_method1(
        "activate_deferred_plugins",
        (registry, settings_json, cli_json),
    )?;
    session
        .0
        .bind(py)
        .setattr("_plugin_registry", new_registry)?;
    Ok(())
}

/// Phase 1: Import plugin modules and read `oxitest_cli_extension` attributes.
/// Returns CLI option descriptors for dynamic clap building.
pub fn discover_plugin_cli(
    py: Python<'_>,
    plugins: &[String],
    plugin_settings: &std::collections::HashMap<String, toml::Value>,
) -> PyResult<crate::config::PluginCliExtensions> {
    let mut extensions = crate::config::PluginCliExtensions::default();
    let introspect_mod = py.import("oxitest._bridge._plugin_config")?;
    let introspect_fn = introspect_mod.getattr("introspect_config")?;
    let dataclasses = py.import("dataclasses")?;
    let missing = dataclasses.getattr("MISSING")?;

    for module_name in plugins {
        let module = match py.import(module_name.as_str()) {
            Ok(m) => m,
            Err(_) => continue, // Skip non-importable plugins (they'll fail later in load_plugins)
        };

        let ext = match module.getattr("oxitest_cli_extension") {
            Ok(attr) if !attr.is_none() => attr,
            _ => continue,
        };

        let prefix: String = ext.getattr("prefix")?.extract()?;
        let config_type = ext.getattr("config_type")?;

        // Check for user prefix override
        let effective_prefix = plugin_settings
            .get(module_name)
            .and_then(|v| v.get("cli_prefix"))
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or(prefix);

        // Introspect the config dataclass
        let descriptors = introspect_fn.call1((config_type,))?;

        let mut options = Vec::new();
        for desc in descriptors.try_iter()? {
            let desc: Bound<'_, PyAny> = desc?;
            let name: String = desc.getattr("name")?.extract()?;
            let source = desc.getattr("source")?;
            let source_type = source.get_type().name()?.to_string();

            // Skip Conf-only fields (no CLI flag)
            if source_type == "Conf" {
                continue;
            }

            let help: String = source.getattr("help")?.extract()?;
            let short: Option<String> = source.getattr("short")?.extract()?;
            let env: Option<String> = if source_type == "Both" {
                source.getattr("env")?.extract()?
            } else {
                None
            };

            let py_type: String = desc
                .getattr("python_type")?
                .getattr("__name__")?
                .extract()?;
            let value_type = match py_type.as_str() {
                "bool" => crate::config::PluginValueType::Bool,
                "int" => crate::config::PluginValueType::Int,
                "float" => crate::config::PluginValueType::Float,
                _ => crate::config::PluginValueType::String,
            };

            let default = desc.getattr("default")?;
            let has_default = !default.is(&missing);

            options.push(crate::config::PluginCliOption {
                field_name: name.clone(),
                long_flag: format!("--{}-{}", effective_prefix, name.replace('_', "-")),
                short_flag: short.and_then(|s: String| s.chars().next()),
                help,
                value_type,
                env,
                has_default,
            });
        }

        if !options.is_empty() {
            extensions
                .plugins
                .insert(module_name.clone(), (effective_prefix, options));
        }
    }

    Ok(extensions)
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

    // Construct TestKind via _test_kind.from_wire — sum type is the source
    // of truth on TestMeta; the wire boundary here mirrors the worker path.
    let test_kind_mod = py.import("oxitest._bridge._test_kind")?;
    let from_wire_fn = test_kind_mod.getattr("from_wire")?;
    let param_id_obj: Bound<'_, PyAny> = match &item.param_id {
        Some(pid) => pid.as_str().into_pyobject(py)?.into_any(),
        None => py.None().into_bound(py),
    };
    let kind_obj = from_wire_fn.call1((param_id_obj,))?;

    let marker_strs: Vec<String> = item.markers.iter().map(|s| s.to_string()).collect();
    let markers_frozen = pyo3::types::PyFrozenSet::new(py, &marker_strs)?;

    let node_id_str: &str = &item.node_id;
    let meta_obj = test_meta_cls.call1((
        item.module_path(),
        &*item.fn_name,
        node_id_str,
        kind_obj,
        markers_frozen,
    ))?;

    let timeout_obj: Bound<'_, PyAny> = match default_timeout {
        Some(t) => (t as i64).into_pyobject(py)?.into_any(),
        None => py.None().into_bound(py),
    };

    let keep_tmp_obj: Bound<'_, PyAny> = opts.keep_tmp.into_pyobject(py)?.into_any();

    // Construct DebugContext Python object
    let runners_mod = py.import("oxitest._bridge._runners")?;
    let debug_ctx_cls = runners_mod.getattr("DebugContext")?;
    let debug_kwargs = pyo3::types::PyDict::new(py);
    if let Some(mode) = opts.debug_mode {
        debug_kwargs.set_item("mode", mode)?;
    }
    debug_kwargs.set_item("show_locals", opts.show_locals)?;
    debug_kwargs.set_item("show_internals", opts.show_internals)?;
    let debug_obj = debug_ctx_cls.call((), Some(&debug_kwargs))?;

    let run_kwargs = pyo3::types::PyDict::new(py);
    run_kwargs.set_item("debug", &debug_obj)?;

    let py_result = executor.call_method(
        "run_test",
        (meta_obj, session_obj, &timeout_obj, keep_tmp_obj),
        Some(&run_kwargs),
    )?;

    extract_outcome(&py_result)
}

/// Two-phase extraction: extract status first, then only the fields needed
/// for that outcome kind. Passed tests (the majority) go from 13 PyO3
/// `getattr()` calls to 2.
///
/// Each status arm extracts PyO3 fields, builds a
/// [`RawOutcome`](crate::worker_result::RawOutcome), and calls
/// [`RawOutcome::into_test_outcome()`](crate::worker_result::RawOutcome::into_test_outcome)
/// — the single conversion path shared with the JSON worker.
fn extract_outcome(py_result: &Bound<'_, PyAny>) -> PyResult<TestOutcome> {
    use crate::worker_result::RawOutcome;

    let status: String = py_result.getattr("status")?.extract()?;

    match status.as_str() {
        "passed" => {
            let no_message_lines: Vec<usize> = py_result.getattr("no_message_lines")?.extract()?;
            Ok(RawOutcome::Passed { no_message_lines }.into_test_outcome())
        }
        "skipped" => {
            let message: String = py_result.getattr("message")?.extract()?;
            Ok(RawOutcome::Skipped { reason: message }.into_test_outcome())
        }
        "xfailed" => {
            let message: String = py_result.getattr("message")?.extract()?;
            Ok(RawOutcome::XFailed { reason: message }.into_test_outcome())
        }
        "xpassed" => {
            let strict: bool = py_result.getattr("strict")?.extract()?;
            Ok(RawOutcome::XPassed { strict }.into_test_outcome())
        }
        "timeout" => {
            let message: String = py_result.getattr("message")?.extract()?;
            Ok(RawOutcome::Timeout { message }.into_test_outcome())
        }
        "warned" => {
            let message: String = py_result.getattr("message")?.extract()?;
            let no_message_lines: Vec<usize> = py_result.getattr("no_message_lines")?.extract()?;
            Ok(RawOutcome::Warned {
                reason: message,
                no_message_lines,
            }
            .into_test_outcome())
        }
        "failed" => {
            let message: String = py_result.getattr("message")?.extract()?;
            let file: String = py_result.getattr("file")?.extract()?;
            let lineno: usize = py_result.getattr("lineno")?.extract()?;
            let source_line: String = py_result.getattr("source_line")?.extract()?;
            let left: String = py_result.getattr("left")?.extract()?;
            let right: String = py_result.getattr("right")?.extract()?;
            let op: String = py_result.getattr("op")?.extract()?;
            let raw_frames: Vec<crate::worker_result::RawFrame> =
                py_result.getattr("frames")?.extract()?;
            let field_diffs: Vec<crate::types::FieldDiff> =
                py_result.getattr("field_diffs")?.extract()?;
            let frames: Vec<Frame> = raw_frames.into_iter().map(Into::into).collect();
            Ok(RawOutcome::Failed {
                message,
                file: Utf8PathBuf::from(file),
                lineno: LineNo::new(lineno),
                source_line,
                frames,
                comparison: crate::types::ComparisonDetail {
                    left,
                    right,
                    op,
                    field_diffs,
                },
            }
            .into_test_outcome())
        }
        _ => {
            let message: String = py_result.getattr("message")?.extract()?;
            let file: String = py_result.getattr("file")?.extract()?;
            let lineno: usize = py_result.getattr("lineno")?.extract()?;
            let source_line: String = py_result.getattr("source_line")?.extract()?;
            let raw_frames: Vec<crate::worker_result::RawFrame> =
                py_result.getattr("frames")?.extract()?;
            let frames: Vec<Frame> = raw_frames.into_iter().map(Into::into).collect();
            // This arm also serves result types that carry no usage flag, so a
            // missing attribute reads as false rather than aborting the run.
            let usage_error: bool = py_result
                .getattr("usage_error")
                .and_then(|value| value.extract())
                .unwrap_or(false);
            Ok(RawOutcome::Error {
                message,
                file: Utf8PathBuf::from(file),
                lineno: LineNo::new(lineno),
                source_line,
                frames,
                usage_error,
            }
            .into_test_outcome())
        }
    }
}
