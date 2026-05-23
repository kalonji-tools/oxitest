//! Watch mode — re-run affected tests on file changes.

use std::time::Duration;

use camino::{Utf8Path, Utf8PathBuf};
use crossterm::event::{self, Event, KeyCode, KeyEvent};

use crate::import_graph::ImportGraph;

/// Paths that should never trigger a re-run.
const IGNORE_PATTERNS: &[&str] = &[
    "__pycache__",
    ".pyc",
    ".oxitest_cache",
    ".git",
    ".ruff_cache",
];

/// Actions the watch loop can take.
pub(crate) enum WatchAction {
    RunAffected(Vec<Utf8PathBuf>),
    RunAll,
    RunFailed,
    Quit,
}

/// Events the watch loop can receive.
pub(crate) enum WatchEvent {
    /// File system paths changed (already converted to Utf8).
    FilesChanged(Vec<Utf8PathBuf>),
    /// User pressed a key.
    Key(WatchAction),
    /// No event within the poll window.
    #[expect(dead_code)]
    Idle,
}

/// What the watch loop should do after processing an event.
#[derive(Debug)]
pub(crate) enum LoopAction {
    /// Run tests with the given scope.
    Run(RunScope),
    /// Do nothing, continue polling.
    Continue,
    /// Exit watch mode.
    Quit,
}

/// Scope of a test run triggered by a watch event.
#[derive(Debug)]
pub(crate) enum RunScope {
    /// Run all tests.
    All,
    /// Run only the given test files.
    Affected(Vec<Utf8PathBuf>),
    /// Run only previously-failed tests.
    FailedOnly,
}

/// Pure event handler — decides what the watch loop should do next.
pub(crate) fn handle_watch_event(
    event: WatchEvent,
    graph: &ImportGraph,
    test_files: &[Utf8PathBuf],
) -> LoopAction {
    match event {
        WatchEvent::Idle => LoopAction::Continue,
        WatchEvent::Key(WatchAction::Quit) => LoopAction::Quit,
        WatchEvent::Key(WatchAction::RunAll) => LoopAction::Run(RunScope::All),
        WatchEvent::Key(WatchAction::RunFailed) => LoopAction::Run(RunScope::FailedOnly),
        WatchEvent::Key(WatchAction::RunAffected(_)) => LoopAction::Run(RunScope::All),
        WatchEvent::FilesChanged(changed) => {
            let filtered = filter_watch_paths(&changed);
            if filtered.is_empty() {
                return LoopAction::Continue;
            }
            match classify_changes(&filtered, graph, test_files) {
                WatchAction::RunAffected(files) => LoopAction::Run(RunScope::Affected(files)),
                WatchAction::RunAll | WatchAction::RunFailed => LoopAction::Run(RunScope::All),
                WatchAction::Quit => unreachable!(),
            }
        }
    }
}

/// Filter already-converted Utf8 paths for watch events.
///
/// Keeps `.py` files (excluding noise) and special config files that trigger
/// a full re-run regardless of extension (e.g. `pyproject.toml`).
fn filter_watch_paths(paths: &[Utf8PathBuf]) -> Vec<Utf8PathBuf> {
    paths
        .iter()
        .filter(|p| {
            let is_py = p.extension() == Some("py") && !is_noise(p);
            let is_special = p.file_name() == Some("pyproject.toml");
            is_py || is_special
        })
        .cloned()
        .collect()
}

fn is_noise(path: &Utf8Path) -> bool {
    let s = path.as_str();
    IGNORE_PATTERNS.iter().any(|pat| s.contains(pat))
}

/// Filter changed paths to only `.py` files, excluding noise.
pub(crate) fn filter_changed_paths(paths: Vec<std::path::PathBuf>) -> Vec<Utf8PathBuf> {
    paths
        .into_iter()
        .filter_map(|p| Utf8PathBuf::try_from(p).ok())
        .filter(|p| p.extension() == Some("py"))
        .filter(|p| !is_noise(p))
        .collect()
}

/// Determine what action to take based on changed files.
pub(crate) fn classify_changes(
    changed: &[Utf8PathBuf],
    graph: &ImportGraph,
    test_files: &[Utf8PathBuf],
) -> WatchAction {
    if changed.is_empty() {
        return WatchAction::RunAll;
    }

    // pyproject.toml change → re-run everything
    if changed
        .iter()
        .any(|p| p.file_name() == Some("pyproject.toml"))
    {
        return WatchAction::RunAll;
    }

    // conftest.py change → re-run all tests
    if changed.iter().any(|p| ImportGraph::is_conftest(p)) {
        return WatchAction::RunAll;
    }

    let result = graph.affected_test_files(changed, test_files);

    // Unknown files (not in graph) → conservative: run all
    if !result.unknown_files.is_empty() {
        return WatchAction::RunAll;
    }

    if result.test_files.is_empty() {
        return WatchAction::RunAll;
    }

    WatchAction::RunAffected(result.test_files)
}

/// Print the watch mode status line.
pub(crate) fn print_status_line() {
    eprintln!();
    eprintln!("  Watching for changes... (press q to quit, a to run all, f to run failed, Enter to re-run)");
}

/// Poll for a keyboard event with timeout.
/// Returns `Some(action)` if the user pressed a recognized key.
pub(crate) fn poll_keyboard(timeout: Duration) -> Option<WatchAction> {
    if !event::poll(timeout).unwrap_or(false) {
        return None;
    }
    match event::read() {
        Ok(Event::Key(KeyEvent { code, .. })) => match code {
            KeyCode::Char('q') => Some(WatchAction::Quit),
            KeyCode::Char('a') => Some(WatchAction::RunAll),
            KeyCode::Char('f') => Some(WatchAction::RunFailed),
            KeyCode::Enter => Some(WatchAction::RunAll),
            _ => None,
        },
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn filter_ignores_pycache() {
        let paths = vec![
            std::path::PathBuf::from("tests/__pycache__/test_foo.cpython-312.pyc"),
            std::path::PathBuf::from("tests/test_foo.py"),
        ];
        let result = filter_changed_paths(paths);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0], Utf8PathBuf::from("tests/test_foo.py"));
    }

    #[test]
    fn filter_ignores_non_python() {
        let paths = vec![
            std::path::PathBuf::from("README.md"),
            std::path::PathBuf::from("src/main.rs"),
        ];
        let result = filter_changed_paths(paths);
        assert!(result.is_empty());
    }

    #[test]
    fn classify_pyproject_change_runs_all() {
        let graph = ImportGraph::new();
        let changed = vec![Utf8PathBuf::from("pyproject.toml")];
        assert!(matches!(
            classify_changes(&changed, &graph, &[]),
            WatchAction::RunAll
        ));
    }

    #[test]
    fn classify_conftest_change_runs_all() {
        let graph = ImportGraph::new();
        let changed = vec![Utf8PathBuf::from("tests/conftest.py")];
        assert!(matches!(
            classify_changes(&changed, &graph, &[]),
            WatchAction::RunAll
        ));
    }

    #[test]
    fn classify_known_source_returns_affected() {
        let mut graph = ImportGraph::new();
        let source = Utf8PathBuf::from("src/auth.py");
        let test = Utf8PathBuf::from("tests/test_auth.py");
        graph.add_edge(source.clone(), test.clone());

        match classify_changes(&[source], &graph, &[test.clone()]) {
            WatchAction::RunAffected(files) => {
                assert_eq!(files, vec![test]);
            }
            _ => panic!("expected RunAffected"),
        }
    }

    #[test]
    fn classify_unknown_source_runs_all() {
        let graph = ImportGraph::new();
        let changed = vec![Utf8PathBuf::from("src/unknown.py")];
        assert!(matches!(
            classify_changes(&changed, &graph, &[Utf8PathBuf::from("tests/test_foo.py")]),
            WatchAction::RunAll
        ));
    }

    #[test]
    fn is_noise_detects_pycache() {
        assert!(is_noise(Utf8Path::new("tests/__pycache__/foo.pyc")));
    }

    #[test]
    fn is_noise_allows_normal_files() {
        assert!(!is_noise(Utf8Path::new("tests/test_foo.py")));
    }

    #[test]
    fn filter_ignores_oxitest_cache() {
        let paths = vec![std::path::PathBuf::from(".oxitest_cache/timings.json")];
        let result = filter_changed_paths(paths);
        assert!(result.is_empty());
    }

    // ── handle_watch_event ──────────────────────────────────────────────

    #[test]
    fn event_idle_returns_continue() {
        let graph = ImportGraph::new();
        assert!(matches!(
            handle_watch_event(WatchEvent::Idle, &graph, &[]),
            LoopAction::Continue
        ));
    }

    #[test]
    fn event_key_quit_returns_quit() {
        let graph = ImportGraph::new();
        assert!(matches!(
            handle_watch_event(WatchEvent::Key(WatchAction::Quit), &graph, &[]),
            LoopAction::Quit
        ));
    }

    #[test]
    fn event_key_run_all_returns_run_all() {
        let graph = ImportGraph::new();
        assert!(matches!(
            handle_watch_event(WatchEvent::Key(WatchAction::RunAll), &graph, &[]),
            LoopAction::Run(RunScope::All)
        ));
    }

    #[test]
    fn event_key_run_failed_returns_run_failed_only() {
        let graph = ImportGraph::new();
        assert!(matches!(
            handle_watch_event(WatchEvent::Key(WatchAction::RunFailed), &graph, &[]),
            LoopAction::Run(RunScope::FailedOnly)
        ));
    }

    #[test]
    fn event_empty_files_returns_continue() {
        let graph = ImportGraph::new();
        assert!(matches!(
            handle_watch_event(WatchEvent::FilesChanged(vec![]), &graph, &[]),
            LoopAction::Continue
        ));
    }

    #[test]
    fn event_non_py_files_returns_continue() {
        let graph = ImportGraph::new();
        let changed = vec![Utf8PathBuf::from("README.md")];
        assert!(matches!(
            handle_watch_event(WatchEvent::FilesChanged(changed), &graph, &[]),
            LoopAction::Continue
        ));
    }

    #[test]
    fn event_noise_files_returns_continue() {
        let graph = ImportGraph::new();
        let changed = vec![Utf8PathBuf::from(
            "tests/__pycache__/test_foo.cpython-312.pyc",
        )];
        assert!(matches!(
            handle_watch_event(WatchEvent::FilesChanged(changed), &graph, &[]),
            LoopAction::Continue
        ));
    }

    #[test]
    fn event_known_test_file_returns_run_affected() {
        let graph = ImportGraph::new();
        let test = Utf8PathBuf::from("tests/test_foo.py");
        match handle_watch_event(
            WatchEvent::FilesChanged(vec![test.clone()]),
            &graph,
            &[test.clone()],
        ) {
            LoopAction::Run(RunScope::Affected(files)) => {
                assert_eq!(files, vec![test]);
            }
            other => panic!("expected Run(Affected), got {other:?}"),
        }
    }

    #[test]
    fn event_unknown_source_returns_run_all() {
        let graph = ImportGraph::new();
        let changed = vec![Utf8PathBuf::from("src/unknown.py")];
        let test_files = vec![Utf8PathBuf::from("tests/test_foo.py")];
        assert!(matches!(
            handle_watch_event(WatchEvent::FilesChanged(changed), &graph, &test_files),
            LoopAction::Run(RunScope::All)
        ));
    }

    #[test]
    fn event_conftest_returns_run_all() {
        let graph = ImportGraph::new();
        let changed = vec![Utf8PathBuf::from("tests/conftest.py")];
        assert!(matches!(
            handle_watch_event(WatchEvent::FilesChanged(changed), &graph, &[]),
            LoopAction::Run(RunScope::All)
        ));
    }

    #[test]
    fn event_pyproject_returns_run_all() {
        let graph = ImportGraph::new();
        let changed = vec![Utf8PathBuf::from("pyproject.toml")];
        assert!(matches!(
            handle_watch_event(WatchEvent::FilesChanged(changed), &graph, &[]),
            LoopAction::Run(RunScope::All)
        ));
    }
}
