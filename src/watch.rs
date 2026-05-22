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
}
