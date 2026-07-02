//! Git-aware test selection for `--affected`.
//!
//! Runs `git diff --name-only` to discover changed files, classifies them
//! (test files, conftest, source, pyproject.toml), and filters the test file
//! list to only affected files.

use camino::{Utf8Path, Utf8PathBuf};

use crate::import_graph;

/// Error from `--affected` processing.
#[derive(thiserror::Error, Debug, Clone)]
pub enum AffectedError {
    /// Not inside a git repository.
    #[error("--affected requires a git repository")]
    NotAGitRepo,
    /// `git diff` returned a non-zero exit code.
    #[error("git command failed: {0}")]
    GitCommandFailed(String),
}

/// Check the result of a git command: map non-zero exit to an appropriate error.
fn check_git_output(output: &std::process::Output) -> Result<(), AffectedError> {
    if output.status.success() {
        return Ok(());
    }
    let stderr = String::from_utf8_lossy(&output.stderr);
    if stderr.to_ascii_lowercase().contains("not a git repository") {
        return Err(AffectedError::NotAGitRepo);
    }
    Err(AffectedError::GitCommandFailed(stderr.into_owned()))
}

/// Parse the raw output of `git diff --name-only` into relative path strings.
fn parse_diff_output(stdout: &str) -> Vec<String> {
    stdout
        .lines()
        .filter(|l| !l.is_empty())
        .map(String::from)
        .collect()
}

/// Canonicalize a path, returning the original on failure.
fn try_canonicalize(path: &Utf8Path) -> Utf8PathBuf {
    path.canonicalize_utf8().unwrap_or_else(|_| path.to_owned())
}

/// Run `git rev-parse --show-toplevel` to get the git repository root.
///
/// The returned path is canonicalized so that symlinks (common in git
/// worktrees created via `git worktree add`) are resolved and the path
/// is guaranteed to exist on the filesystem.
fn git_root(dir: &Utf8Path) -> Result<Utf8PathBuf, AffectedError> {
    let canonical_dir = try_canonicalize(dir);
    let output = std::process::Command::new("git")
        .args(["--no-optional-locks", "rev-parse", "--show-toplevel"])
        .current_dir(canonical_dir.as_std_path())
        .output()
        .map_err(|e| {
            AffectedError::GitCommandFailed(format!(
                "failed to run `git rev-parse` in {canonical_dir}: {e}"
            ))
        })?;

    check_git_output(&output)?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    let raw = Utf8PathBuf::try_from(std::path::PathBuf::from(stdout.trim()))
        .map_err(|e| AffectedError::GitCommandFailed(e.to_string()))?;
    // Canonicalize the toplevel path: in worktrees, git may return a
    // symlinked path that differs from the canonical filesystem path.
    Ok(try_canonicalize(&raw))
}

/// Run `git diff --name-only <base>` and return changed file paths (relative to git root).
pub(crate) fn git_changed_files(
    rootdir: &Utf8Path,
    base: &str,
) -> Result<(Utf8PathBuf, Vec<String>), AffectedError> {
    let repo_root = git_root(rootdir)?;

    let output = std::process::Command::new("git")
        .args(["--no-optional-locks", "diff", "--name-only", base])
        .current_dir(repo_root.as_std_path())
        .output()
        .map_err(|e| {
            AffectedError::GitCommandFailed(format!("failed to run `git diff` in {repo_root}: {e}"))
        })?;

    check_git_output(&output)?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    Ok((repo_root, parse_diff_output(&stdout)))
}

/// Result of classifying git-changed files.
#[derive(Debug)]
pub(crate) struct ChangedFiles {
    /// `pyproject.toml` was changed — must run all tests.
    pub run_all: bool,
    /// Changed `conftest.py` files (relative paths).
    pub conftest_files: Vec<String>,
    /// Changed `.py` source files that are NOT conftest (relative paths).
    pub source_files: Vec<String>,
}

/// Diagnostic data collected during `--affected` filtering.
///
/// Each field captures per-stage information that the pipeline
/// transition renders to stderr at the appropriate verbosity level.
#[derive(Debug, Default)]
pub(crate) struct AffectedDiagnostics {
    /// Base ref used for `git diff`.
    pub base_ref: String,
    /// Total files changed in git diff.
    pub total_changed: usize,
    /// Number of changed files that are not `.py` (ignored by affected).
    pub non_python_count: usize,
    /// Changed conftest.py files.
    pub conftest_files: Vec<String>,
    /// Changed Python source files (non-conftest).
    pub source_files: Vec<String>,
    /// Test files selected because they themselves changed.
    pub direct_matches: Vec<String>,
    /// Test files selected because a conftest in their subtree changed.
    pub conftest_matches: Vec<String>,
    /// Per-test-file import analysis results (for -vv).
    pub import_analysis: Vec<ImportAnalysis>,
    /// Total test files in the project.
    pub total_tests: usize,
    /// Total test files selected as affected.
    pub affected_count: usize,
}

/// Per-file result of import graph analysis.
#[derive(Debug)]
pub(crate) struct ImportAnalysis {
    /// Test file path (relative to project root for display).
    pub test_file: String,
    /// Whether this test was selected as affected.
    pub affected: bool,
    /// The imports from this file that matched changed sources.
    pub matched_imports: Vec<String>,
}

/// Classify changed files and return the non-Python count for diagnostics.
pub(crate) fn classify_changed_files_with_diagnostics(
    changed: Vec<String>,
) -> (ChangedFiles, usize) {
    let mut result = ChangedFiles {
        run_all: false,
        conftest_files: Vec::new(),
        source_files: Vec::new(),
    };
    let mut non_python = 0usize;

    for path in changed {
        if path == "pyproject.toml" {
            result.run_all = true;
            return (result, non_python);
        }
        if !path.ends_with(".py") {
            non_python += 1;
            continue;
        }
        if path.ends_with("conftest.py") {
            result.conftest_files.push(path);
        } else {
            result.source_files.push(path);
        }
    }

    (result, non_python)
}

/// Insert into `affected` test files that live in the directory subtree of any changed conftest.
fn conftest_affected_tests(
    test_files: &[Utf8PathBuf],
    changed_conftests: &[String],
    rootdir: &Utf8Path,
    affected: &mut std::collections::HashSet<Utf8PathBuf>,
) {
    let mut conftest_dirs: Vec<Utf8PathBuf> = changed_conftests
        .iter()
        .filter_map(|c| {
            let abs = rootdir.join(c);
            abs.parent().map(|p| p.to_owned())
        })
        .collect();
    conftest_dirs.sort_unstable();
    conftest_dirs.dedup();

    for tf in test_files {
        if conftest_dirs.iter().any(|dir| tf.starts_with(dir)) {
            affected.insert(tf.clone());
        }
    }
}

/// Insert into `affected` test files that are themselves in the changed set.
fn directly_changed_tests(
    test_files: &[Utf8PathBuf],
    changed_sources: &[String],
    rootdir: &Utf8Path,
    affected: &mut std::collections::HashSet<Utf8PathBuf>,
) {
    let changed_abs: std::collections::HashSet<Utf8PathBuf> =
        changed_sources.iter().map(|s| rootdir.join(s)).collect();

    for tf in test_files {
        if changed_abs.contains(tf) {
            affected.insert(tf.clone());
        }
    }
}

/// Filter `test_files` to only those affected by git changes.
///
/// Returns:
/// - `Ok(Some(filtered))` — filtered list of affected test files.
/// - `Ok(None)` — `pyproject.toml` changed or root conftest changed; run all.
/// - `Err(e)` — git error or import analysis error.
pub(crate) fn filter_affected_test_files(
    test_files: &[Utf8PathBuf],
    rootdir: &Utf8Path,
    base_ref: &str,
) -> Result<Option<Vec<Utf8PathBuf>>, AffectedError> {
    filter_affected_with_diagnostics(test_files, rootdir, base_ref).map(|(result, _diag)| result)
}

/// Filter test files with full diagnostic data collection.
///
/// Returns the same result as [`filter_affected_test_files`] plus an
/// [`AffectedDiagnostics`] struct populated at each stage.
pub(crate) fn filter_affected_with_diagnostics(
    test_files: &[Utf8PathBuf],
    rootdir: &Utf8Path,
    base_ref: &str,
) -> Result<(Option<Vec<Utf8PathBuf>>, AffectedDiagnostics), AffectedError> {
    let mut diag = AffectedDiagnostics {
        base_ref: base_ref.to_string(),
        total_tests: test_files.len(),
        ..Default::default()
    };

    let (repo_root, changed) = git_changed_files(rootdir, base_ref)?;
    diag.total_changed = changed.len();

    if changed.is_empty() {
        return Ok((Some(vec![]), diag));
    }

    let (classified, non_python) = classify_changed_files_with_diagnostics(changed);
    diag.non_python_count = non_python;
    diag.conftest_files = classified.conftest_files.clone();
    diag.source_files = classified.source_files.clone();

    if classified.run_all {
        diag.affected_count = test_files.len();
        return Ok((None, diag));
    }

    let mut affected: std::collections::HashSet<Utf8PathBuf> = std::collections::HashSet::new();

    // Stage 3: Direct matches
    directly_changed_tests(
        test_files,
        &classified.source_files,
        &repo_root,
        &mut affected,
    );
    diag.direct_matches = affected.iter().map(|p| p.to_string()).collect();
    diag.direct_matches.sort();

    // Stage 4: Conftest matches
    conftest_affected_tests(
        test_files,
        &classified.conftest_files,
        &repo_root,
        &mut affected,
    );
    diag.conftest_matches = affected
        .iter()
        .filter(|p| !diag.direct_matches.contains(&p.to_string()))
        .map(|p| p.to_string())
        .collect();
    diag.conftest_matches.sort();

    // Stage 5: Import graph
    let remaining: Vec<Utf8PathBuf> = test_files
        .iter()
        .filter(|f| !affected.contains(*f))
        .cloned()
        .collect();

    if !remaining.is_empty() && !classified.source_files.is_empty() {
        let analysis =
            import_graph::resolve_affected_with_diagnostics(&remaining, &classified.source_files);
        for entry in &analysis {
            if entry.affected {
                affected.insert(Utf8PathBuf::from(&entry.test_file));
            }
        }
        diag.import_analysis = analysis;
    }

    // Preserve original ordering
    let result: Vec<Utf8PathBuf> = test_files
        .iter()
        .filter(|f| affected.contains(*f))
        .cloned()
        .collect();
    diag.affected_count = result.len();

    Ok((Some(result), diag))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_diff_empty_output() {
        assert!(parse_diff_output("").is_empty());
    }

    #[test]
    fn parse_diff_single_file() {
        let out = "src/utils.py\n";
        let files = parse_diff_output(out);
        assert_eq!(files, vec!["src/utils.py"]);
    }

    #[test]
    fn parse_diff_multiple_files() {
        let out = "src/utils.py\ntests/test_foo.py\nconftest.py\n";
        let files = parse_diff_output(out);
        assert_eq!(files.len(), 3);
    }

    #[test]
    fn parse_diff_trailing_newlines_ignored() {
        let out = "a.py\n\n\n";
        let files = parse_diff_output(out);
        assert_eq!(files, vec!["a.py"]);
    }

    // ── classify_changed_files ───────────────────────────────────────

    #[test]
    fn classify_pyproject_triggers_run_all() {
        let changed = vec!["pyproject.toml".to_string(), "src/foo.py".to_string()];
        let result = classify_changed_files_with_diagnostics(changed).0;
        assert!(result.run_all);
    }

    #[test]
    fn classify_no_pyproject_does_not_run_all() {
        let changed = vec!["src/foo.py".to_string()];
        let result = classify_changed_files_with_diagnostics(changed).0;
        assert!(!result.run_all);
    }

    #[test]
    fn classify_conftest_detected() {
        let changed = vec!["tests/conftest.py".to_string(), "src/foo.py".to_string()];
        let result = classify_changed_files_with_diagnostics(changed).0;
        assert_eq!(result.conftest_files, vec!["tests/conftest.py"]);
        assert_eq!(result.source_files, vec!["src/foo.py"]);
    }

    #[test]
    fn classify_non_python_ignored() {
        let changed = vec!["README.md".to_string(), "Cargo.toml".to_string()];
        let result = classify_changed_files_with_diagnostics(changed).0;
        assert!(!result.run_all);
        assert!(result.conftest_files.is_empty());
        assert!(result.source_files.is_empty());
    }

    #[test]
    fn classify_empty_input() {
        let result = classify_changed_files_with_diagnostics(vec![]).0;
        assert!(!result.run_all);
        assert!(result.conftest_files.is_empty());
        assert!(result.source_files.is_empty());
    }

    // ── conftest_affected_tests ──────────────────────────────────────

    #[test]
    fn conftest_subtree_includes_tests_below() {
        let test_files = vec![
            Utf8PathBuf::from("/project/tests/test_a.py"),
            Utf8PathBuf::from("/project/tests/sub/test_b.py"),
            Utf8PathBuf::from("/project/other/test_c.py"),
        ];
        let changed_conftests = vec!["tests/conftest.py".to_string()];
        let rootdir = Utf8Path::new("/project");
        let mut affected = std::collections::HashSet::new();
        conftest_affected_tests(&test_files, &changed_conftests, rootdir, &mut affected);
        assert_eq!(affected.len(), 2);
        assert!(affected.contains(&Utf8PathBuf::from("/project/tests/test_a.py")));
        assert!(affected.contains(&Utf8PathBuf::from("/project/tests/sub/test_b.py")));
    }

    #[test]
    fn conftest_subtree_root_conftest_affects_all() {
        let test_files = vec![
            Utf8PathBuf::from("/project/tests/test_a.py"),
            Utf8PathBuf::from("/project/other/test_b.py"),
        ];
        let changed_conftests = vec!["conftest.py".to_string()];
        let rootdir = Utf8Path::new("/project");
        let mut affected = std::collections::HashSet::new();
        conftest_affected_tests(&test_files, &changed_conftests, rootdir, &mut affected);
        assert_eq!(affected.len(), 2);
    }

    #[test]
    fn conftest_subtree_no_match() {
        let test_files = vec![Utf8PathBuf::from("/project/other/test_a.py")];
        let changed_conftests = vec!["tests/conftest.py".to_string()];
        let rootdir = Utf8Path::new("/project");
        let mut affected = std::collections::HashSet::new();
        conftest_affected_tests(&test_files, &changed_conftests, rootdir, &mut affected);
        assert!(affected.is_empty());
    }

    // ── directly_changed_tests ───────────────────────────────────────

    #[test]
    fn directly_changed_test_files_included() {
        let test_files = vec![
            Utf8PathBuf::from("/project/tests/test_a.py"),
            Utf8PathBuf::from("/project/tests/test_b.py"),
        ];
        let changed_sources = vec!["tests/test_a.py".to_string()];
        let rootdir = Utf8Path::new("/project");
        let mut affected = std::collections::HashSet::new();
        directly_changed_tests(&test_files, &changed_sources, rootdir, &mut affected);
        assert_eq!(affected.len(), 1);
        assert!(affected.contains(&Utf8PathBuf::from("/project/tests/test_a.py")));
    }

    #[test]
    fn directly_changed_no_match() {
        let test_files = vec![Utf8PathBuf::from("/project/tests/test_a.py")];
        let changed_sources = vec!["src/utils.py".to_string()];
        let rootdir = Utf8Path::new("/project");
        let mut affected = std::collections::HashSet::new();
        directly_changed_tests(&test_files, &changed_sources, rootdir, &mut affected);
        assert!(affected.is_empty());
    }

    // ── diagnostics ────────────────────────────────────────────────

    #[test]
    fn diagnostics_populated_from_classify() {
        let changed = vec!["src/foo.py".to_string(), "README.md".to_string()];
        let (classified, non_python) = classify_changed_files_with_diagnostics(changed);
        let diag = AffectedDiagnostics {
            base_ref: "HEAD".to_string(),
            total_changed: 2,
            non_python_count: non_python,
            conftest_files: classified.conftest_files.clone(),
            source_files: classified.source_files.clone(),
            ..Default::default()
        };
        assert_eq!(diag.non_python_count, 1, "README.md is non-Python");
        assert_eq!(diag.source_files.len(), 1, "src/foo.py is source");
        assert_eq!(diag.total_changed, 2, "total changed files from git diff");
    }

    #[test]
    fn classify_diagnostics_counts_non_python() {
        let changed = vec![
            "src/foo.py".to_string(),
            "README.md".to_string(),
            "Cargo.toml".to_string(),
            "tests/conftest.py".to_string(),
        ];
        let (classified, non_python) = classify_changed_files_with_diagnostics(changed);
        assert_eq!(
            non_python, 2,
            "README.md and Cargo.toml are non-Python files"
        );
        assert_eq!(
            classified.source_files.len(),
            1,
            "only src/foo.py is a Python source file"
        );
        assert_eq!(
            classified.conftest_files.len(),
            1,
            "tests/conftest.py is a conftest"
        );
    }
}
