//! Git-aware test selection for `--affected`.
//!
//! Runs `git diff --name-only` to discover changed files, classifies them
//! (test files, conftest, source, pyproject.toml), and filters the test file
//! list to only affected files.

use camino::{Utf8Path, Utf8PathBuf};
use pyo3::prelude::*;

use crate::bridge;

/// Error from `--affected` processing.
#[derive(thiserror::Error, Debug, Clone)]
pub enum AffectedError {
    /// Not inside a git repository.
    #[error("--affected requires a git repository")]
    NotAGitRepo,
    /// `git diff` returned a non-zero exit code.
    #[error("git diff failed: {0}")]
    GitCommandFailed(String),
}

/// Parse the raw output of `git diff --name-only` into relative path strings.
fn parse_diff_output(stdout: &str) -> Vec<String> {
    stdout
        .lines()
        .filter(|l| !l.is_empty())
        .map(String::from)
        .collect()
}

/// Run `git diff --name-only <base>` and return changed file paths (relative to rootdir).
pub(crate) fn git_changed_files(
    rootdir: &Utf8Path,
    base: &str,
) -> Result<Vec<String>, AffectedError> {
    let output = std::process::Command::new("git")
        .args(["diff", "--name-only", base])
        .current_dir(rootdir.as_std_path())
        .output()
        .map_err(|e| AffectedError::GitCommandFailed(e.to_string()))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        if stderr.to_ascii_lowercase().contains("not a git repository") {
            return Err(AffectedError::NotAGitRepo);
        }
        return Err(AffectedError::GitCommandFailed(stderr.into_owned()));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    Ok(parse_diff_output(&stdout))
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

/// Classify changed file paths into categories relevant for affected-test filtering.
pub(crate) fn classify_changed_files(changed: &[String]) -> ChangedFiles {
    let mut result = ChangedFiles {
        run_all: false,
        conftest_files: Vec::new(),
        source_files: Vec::new(),
    };

    for path in changed {
        if path == "pyproject.toml" {
            result.run_all = true;
            return result;
        }
        if !path.ends_with(".py") {
            continue;
        }
        if path.ends_with("conftest.py") {
            result.conftest_files.push(path.clone());
        } else {
            result.source_files.push(path.clone());
        }
    }

    result
}

/// Return test files that live in the directory subtree of any changed conftest.
fn conftest_affected_tests(
    test_files: &[Utf8PathBuf],
    changed_conftests: &[String],
    rootdir: &Utf8Path,
) -> Vec<Utf8PathBuf> {
    let conftest_dirs: Vec<Utf8PathBuf> = changed_conftests
        .iter()
        .filter_map(|c| {
            let abs = rootdir.join(c);
            abs.parent().map(|p| p.to_owned())
        })
        .collect();

    test_files
        .iter()
        .filter(|tf| conftest_dirs.iter().any(|dir| tf.starts_with(dir)))
        .cloned()
        .collect()
}

/// Return test files that are themselves in the changed set.
fn directly_changed_tests(
    test_files: &[Utf8PathBuf],
    changed_sources: &[String],
    rootdir: &Utf8Path,
) -> Vec<Utf8PathBuf> {
    let changed_abs: std::collections::HashSet<Utf8PathBuf> =
        changed_sources.iter().map(|s| rootdir.join(s)).collect();

    test_files
        .iter()
        .filter(|tf| changed_abs.contains(*tf))
        .cloned()
        .collect()
}

/// Filter `test_files` to only those affected by git changes.
///
/// Returns:
/// - `Ok(Some(filtered))` — filtered list of affected test files.
/// - `Ok(None)` — `pyproject.toml` changed or root conftest changed; run all.
/// - `Err(e)` — git error or import analysis error.
pub(crate) fn filter_affected_test_files(
    py: Python<'_>,
    test_files: &[Utf8PathBuf],
    rootdir: &Utf8Path,
    base_ref: &str,
) -> Result<Option<Vec<Utf8PathBuf>>, AffectedError> {
    let changed = git_changed_files(rootdir, base_ref)?;

    if changed.is_empty() {
        return Ok(Some(vec![]));
    }

    let classified = classify_changed_files(&changed);

    if classified.run_all {
        return Ok(None);
    }

    let mut affected: std::collections::HashSet<Utf8PathBuf> = std::collections::HashSet::new();

    // 1. Test files that were directly changed.
    affected.extend(directly_changed_tests(
        test_files,
        &classified.source_files,
        rootdir,
    ));

    // 2. Test files in conftest subtrees.
    affected.extend(conftest_affected_tests(
        test_files,
        &classified.conftest_files,
        rootdir,
    ));

    // 3. Test files that import changed source files (via Python AST analysis).
    //    Only analyze files not already marked affected.
    let remaining: Vec<Utf8PathBuf> = test_files
        .iter()
        .filter(|f| !affected.contains(*f))
        .cloned()
        .collect();

    if !remaining.is_empty() && !classified.source_files.is_empty() {
        let import_affected =
            bridge::resolve_affected_tests(py, &remaining, &classified.source_files, rootdir)
                .map_err(|e| {
                    AffectedError::GitCommandFailed(format!("import analysis failed: {e}"))
                })?;

        for path_str in import_affected {
            affected.insert(Utf8PathBuf::from(path_str));
        }
    }

    // Preserve original ordering from test_files.
    let result: Vec<Utf8PathBuf> = test_files
        .iter()
        .filter(|f| affected.contains(*f))
        .cloned()
        .collect();

    Ok(Some(result))
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
        let result = classify_changed_files(&changed);
        assert!(result.run_all);
    }

    #[test]
    fn classify_no_pyproject_does_not_run_all() {
        let changed = vec!["src/foo.py".to_string()];
        let result = classify_changed_files(&changed);
        assert!(!result.run_all);
    }

    #[test]
    fn classify_conftest_detected() {
        let changed = vec!["tests/conftest.py".to_string(), "src/foo.py".to_string()];
        let result = classify_changed_files(&changed);
        assert_eq!(result.conftest_files, vec!["tests/conftest.py"]);
        assert_eq!(result.source_files, vec!["src/foo.py"]);
    }

    #[test]
    fn classify_non_python_ignored() {
        let changed = vec!["README.md".to_string(), "Cargo.toml".to_string()];
        let result = classify_changed_files(&changed);
        assert!(!result.run_all);
        assert!(result.conftest_files.is_empty());
        assert!(result.source_files.is_empty());
    }

    #[test]
    fn classify_empty_input() {
        let result = classify_changed_files(&[]);
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
        let affected = conftest_affected_tests(&test_files, &changed_conftests, rootdir);
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
        let affected = conftest_affected_tests(&test_files, &changed_conftests, rootdir);
        assert_eq!(affected.len(), 2);
    }

    #[test]
    fn conftest_subtree_no_match() {
        let test_files = vec![Utf8PathBuf::from("/project/other/test_a.py")];
        let changed_conftests = vec!["tests/conftest.py".to_string()];
        let rootdir = Utf8Path::new("/project");
        let affected = conftest_affected_tests(&test_files, &changed_conftests, rootdir);
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
        let affected = directly_changed_tests(&test_files, &changed_sources, rootdir);
        assert_eq!(
            affected,
            vec![Utf8PathBuf::from("/project/tests/test_a.py")]
        );
    }

    #[test]
    fn directly_changed_no_match() {
        let test_files = vec![Utf8PathBuf::from("/project/tests/test_a.py")];
        let changed_sources = vec!["src/utils.py".to_string()];
        let rootdir = Utf8Path::new("/project");
        let affected = directly_changed_tests(&test_files, &changed_sources, rootdir);
        assert!(affected.is_empty());
    }
}
