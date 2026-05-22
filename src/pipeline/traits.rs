//! Trait seams for pipeline testability.
//!
//! Each trait abstracts one PyO3 or subprocess boundary so pipeline branching
//! logic can be unit-tested with in-memory test doubles.

use std::sync::Arc;

use camino::{Utf8Path, Utf8PathBuf};
use pyo3::prelude::*;

use crate::bridge::{self, RawViolation};
use crate::config::Config;
use crate::parallel;
use crate::reporter::Reporter;
use crate::types::{CollectError, TestItem, TestOutcome};

// ─── Trait definitions ──────────────────────────────────────────────────────

/// Abstracts `bridge::FixtureSession` lifecycle.
pub(crate) trait Session {
    fn end_module(&self, py: Python<'_>, module_path: &Utf8Path) -> PyResult<()>;
    fn end_session(&self, py: Python<'_>) -> PyResult<()>;
    fn shared_fixture_names(&self, py: Python<'_>) -> Vec<String>;
    fn as_py_object<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny>;
}

/// Abstracts `bridge::collect_module`.
pub(crate) trait ModuleCollector {
    fn collect_module(
        &self,
        py: Python<'_>,
        path: &Utf8Path,
        session: &dyn Session,
        collect_violations: bool,
    ) -> Result<(Vec<TestItem>, Vec<RawViolation>), CollectError>;
}

/// Abstracts `bridge::run_test`.
pub(crate) trait TestRunner {
    fn run_test(
        &self,
        py: Python<'_>,
        item: &TestItem,
        session: &dyn Session,
        timeout: Option<u64>,
    ) -> TestOutcome;
}

/// Abstracts `parallel::run_phase_parallel`.
pub(crate) trait ParallelRunner {
    fn run_parallel(
        &self,
        groups: Vec<(Utf8PathBuf, Vec<Arc<TestItem>>)>,
        cfg: &Config,
        workers: usize,
        conftest_files: &[Utf8PathBuf],
        rep: &mut dyn Reporter,
    ) -> parallel::PhaseResult;
}

// ─── Real implementations ───────────────────────────────────────────────────

impl Session for bridge::FixtureSession {
    fn end_module(&self, py: Python<'_>, module_path: &Utf8Path) -> PyResult<()> {
        bridge::FixtureSession::end_module(self, py, module_path)
    }

    fn end_session(&self, py: Python<'_>) -> PyResult<()> {
        bridge::FixtureSession::end_session(self, py)
    }

    fn shared_fixture_names(&self, py: Python<'_>) -> Vec<String> {
        bridge::FixtureSession::shared_fixture_names(self, py)
    }

    fn as_py_object<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        bridge::FixtureSession::as_py_object(self, py)
    }
}

/// Delegates to [`bridge::collect_module_with_session_obj`].
pub(crate) struct BridgeCollector;

impl ModuleCollector for BridgeCollector {
    fn collect_module(
        &self,
        py: Python<'_>,
        path: &Utf8Path,
        session: &dyn Session,
        collect_violations: bool,
    ) -> Result<(Vec<TestItem>, Vec<RawViolation>), CollectError> {
        bridge::collect_module_with_session_obj(
            py,
            path,
            session.as_py_object(py),
            collect_violations,
        )
    }
}

/// Delegates to [`bridge::run_test_with_session_obj`].
pub(crate) struct BridgeRunner;

impl TestRunner for BridgeRunner {
    fn run_test(
        &self,
        py: Python<'_>,
        item: &TestItem,
        session: &dyn Session,
        timeout: Option<u64>,
    ) -> TestOutcome {
        bridge::run_test_with_session_obj(py, item, session.as_py_object(py), timeout)
    }
}

/// Delegates to [`parallel::run_phase_parallel`].
pub(crate) struct DefaultParallelRunner;

impl ParallelRunner for DefaultParallelRunner {
    fn run_parallel(
        &self,
        groups: Vec<(Utf8PathBuf, Vec<Arc<TestItem>>)>,
        cfg: &Config,
        workers: usize,
        conftest_files: &[Utf8PathBuf],
        rep: &mut dyn Reporter,
    ) -> parallel::PhaseResult {
        parallel::run_phase_parallel(groups, cfg, workers, conftest_files, rep)
    }
}
