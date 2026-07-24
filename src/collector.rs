//! File discovery — walks the filesystem to find test files and conftest files.
//!
//! Uses `testpaths`, `python_files`, and `norecursedirs` from [`Config`] to
//! match files via glob patterns. Returns deduplicated, sorted lists of
//! test file paths and conftest paths.

use camino::{Utf8Path, Utf8PathBuf};
use globset::{GlobBuilder, GlobSet, GlobSetBuilder};
use ignore::WalkBuilder;
use std::collections::HashSet;

use crate::config::Config;

/// Canonicalize a path to produce a stable absolute form.
///
/// Resolves `.`, `..`, and symlinks via `std::fs::canonicalize`.
/// Falls back to the original path if canonicalization fails.
fn normalize_path(path: &Utf8Path, _canonical_rootdir: &Utf8Path) -> Utf8PathBuf {
    match std::fs::canonicalize(path.as_std_path()) {
        Ok(p) => Utf8PathBuf::from_path_buf(p).unwrap_or_else(|_| path.to_owned()),
        Err(_) => path.to_owned(),
    }
}

pub(crate) fn build_glob_set(patterns: &[String]) -> Result<GlobSet, globset::Error> {
    let mut builder = GlobSetBuilder::new();
    for pattern in patterns {
        builder.add(GlobBuilder::new(pattern).build()?);
    }
    builder.build()
}

/// Returns `(test_files, conftest_files)` sorted by path.
/// conftest_files are deduplicated and sorted shallow-first.
pub fn collect_files(
    config: &Config,
) -> Result<(Vec<Utf8PathBuf>, Vec<Utf8PathBuf>), globset::Error> {
    let glob_set = build_glob_set(&config.paths.python_files)?;
    let mut files = Vec::new();
    let mut conftest_set: HashSet<Utf8PathBuf> = HashSet::new();

    // Always check the rootdir itself for a conftest.py (covers the case where
    // testpaths point to a subdirectory and conftest.py lives at the project root).
    let rootdir_conftest = config.rootdir.join("conftest.py");
    if rootdir_conftest.exists() {
        conftest_set.insert(normalize_path(&rootdir_conftest, &config.rootdir));
    }

    for testpath in &config.paths.testpaths {
        collect_from(testpath, config, &glob_set, &mut files, &mut conftest_set);
    }

    files.sort();

    let mut conftests: Vec<Utf8PathBuf> = conftest_set.into_iter().collect();
    conftests.sort_by_key(|p| p.components().count());

    Ok((files, conftests))
}

/// Collect all `.py` files for doctest scanning.
///
/// Unlike `collect_files` which uses `python_files` glob patterns (e.g. `test_*.py`),
/// this collects every `.py` file in `testpaths` (and rootdir) since any source module
/// can contain doctests.
pub fn collect_doctest_files(config: &Config) -> Vec<Utf8PathBuf> {
    let glob_set = build_glob_set(&["*.py".to_string()]).expect("*.py is a valid glob");
    let mut files = Vec::new();
    let mut dummy_conftests = HashSet::new();

    for testpath in &config.paths.testpaths {
        collect_from(
            testpath,
            config,
            &glob_set,
            &mut files,
            &mut dummy_conftests,
        );
    }

    files.sort();
    files
}

/// Return only conftests that are ancestors of any matched test module.
///
/// A conftest is an ancestor if its directory is a prefix of any matched
/// module's directory. Results are sorted shallow-first (by component count).
pub(crate) fn conftests_for_modules(
    all_conftests: &[Utf8PathBuf],
    matched_modules: &[Utf8PathBuf],
) -> Vec<Utf8PathBuf> {
    if matched_modules.is_empty() {
        return vec![];
    }

    // Collect all ancestor directories of all matched modules.
    let mut ancestor_dirs: HashSet<Utf8PathBuf> = HashSet::new();
    for module in matched_modules {
        let mut dir = module.parent();
        while let Some(d) = dir {
            ancestor_dirs.insert(d.to_owned());
            dir = d.parent();
        }
    }

    // Filter conftests whose parent directory is in the ancestor set.
    let mut result: Vec<Utf8PathBuf> = all_conftests
        .iter()
        .filter(|c| {
            c.parent()
                .map(|d| ancestor_dirs.contains(d))
                .unwrap_or(false)
        })
        .cloned()
        .collect();

    // Sort shallow-first by component count.
    result.sort_by_key(|p| p.components().count());
    result
}

fn collect_from(
    path: &Utf8Path,
    config: &Config,
    glob_set: &GlobSet,
    out: &mut Vec<Utf8PathBuf>,
    conftests: &mut HashSet<Utf8PathBuf>,
) {
    if path.is_file() {
        if let Some(filename) = path.file_name()
            && glob_set.is_match(filename)
        {
            out.push(normalize_path(path, &config.rootdir));
        }
        // Walk from the file's directory up to rootdir, collecting conftest.py at each level.
        // This ensures intermediate conftests (e.g. tests/conftest.py between rootdir and
        // tests/integration/test_foo.py) are discovered when targeting a single file.
        let mut dir = path.parent();
        while let Some(d) = dir {
            let conftest = d.join("conftest.py");
            if conftest.exists() {
                conftests.insert(normalize_path(&conftest, &config.rootdir));
            }
            if d == config.rootdir {
                break;
            }
            dir = d.parent();
        }
        return;
    }

    let norecursedirs = config.paths.norecursedirs.clone();
    let walker = WalkBuilder::new(path)
        .follow_links(false)
        .hidden(false)
        .git_ignore(config.paths.use_gitignore)
        .git_global(false)
        .git_exclude(false)
        .filter_entry(move |e| {
            if e.file_type().is_some_and(|ft| ft.is_dir()) {
                let name = e.file_name().to_string_lossy();
                return !norecursedirs.iter().any(|d| d == name.as_ref());
            }
            true
        })
        .build();

    for entry in walker.filter_map(|e| e.ok()) {
        let Some(ft) = entry.file_type() else {
            continue;
        };
        if ft.is_dir() {
            let conftest_std = entry.path().join("conftest.py");
            if conftest_std.exists() {
                match Utf8PathBuf::from_path_buf(conftest_std) {
                    Ok(utf8) => {
                        conftests.insert(normalize_path(&utf8, &config.rootdir));
                    }
                    Err(p) => tracing::warn!(path = ?p, "skipping non-UTF-8 conftest path"),
                }
            }
        } else if ft.is_file() {
            let filename = entry.file_name();
            if glob_set.is_match(filename) {
                match Utf8PathBuf::from_path_buf(entry.into_path()) {
                    Ok(utf8) => out.push(normalize_path(&utf8, &config.rootdir)),
                    Err(p) => tracing::warn!(path = ?p, "skipping non-UTF-8 test file path"),
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use assert_fs::prelude::*;

    fn make_config(dir: &camino::Utf8Path) -> Config {
        let canonical_root = match std::fs::canonicalize(dir.as_std_path()) {
            Ok(p) => Utf8PathBuf::from_path_buf(p).unwrap_or_else(|_| dir.to_owned()),
            Err(_) => dir.to_owned(),
        };
        Config {
            rootdir: canonical_root.clone(),
            paths: crate::config::PathConfig {
                testpaths: vec![canonical_root],
                python_files: vec!["test_*.py".to_string(), "*_test.py".to_string()],
                norecursedirs: vec![".git".to_string(), "__pycache__".to_string()],
                ..Default::default()
            },
            ..Config::default()
        }
    }

    #[test]
    fn test_collect_empty_dir() {
        let dir = assert_fs::TempDir::new().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (files, conftests) = collect_files(&config).unwrap();
        assert!(files.is_empty());
        assert!(conftests.is_empty());
    }

    #[test]
    fn test_collect_finds_test_file() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("test_foo.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (files, _) = collect_files(&config).unwrap();
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].file_name().unwrap(), "test_foo.py");
    }

    #[test]
    fn test_collect_ignores_non_test_files() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("helper.py").touch().unwrap();
        dir.child("test_real.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (files, _) = collect_files(&config).unwrap();
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].file_name().unwrap(), "test_real.py");
    }

    #[test]
    fn test_collect_respects_norecursedirs() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("__pycache__/test_hidden.py").touch().unwrap();
        dir.child("test_visible.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (files, _) = collect_files(&config).unwrap();
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].file_name().unwrap(), "test_visible.py");
    }

    #[test]
    fn test_collect_specific_file() {
        let dir = assert_fs::TempDir::new().unwrap();
        let f = dir.child("test_specific.py");
        f.touch().unwrap();
        let utf8_dir = camino::Utf8Path::from_path(dir.path()).unwrap();
        let config = Config {
            paths: crate::config::PathConfig {
                testpaths: vec![camino::Utf8Path::from_path(f.path()).unwrap().to_owned()],
                python_files: vec!["test_*.py".to_string()],
                norecursedirs: vec![],
                ..Default::default()
            },
            ..make_config(utf8_dir)
        };
        let (files, _) = collect_files(&config).unwrap();
        assert_eq!(files.len(), 1);
        assert!(
            files[0].as_str().ends_with("test_specific.py"),
            "collected path should end with test_specific.py, got: {}",
            files[0]
        );
    }

    #[test]
    fn test_collect_multi_wildcard_pattern() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("test_foo_integration.py").touch().unwrap();
        dir.child("test_foo.py").touch().unwrap();
        dir.child("helper.py").touch().unwrap();
        let utf8_dir = camino::Utf8Path::from_path(dir.path()).unwrap();
        let config = Config {
            paths: crate::config::PathConfig {
                python_files: vec!["test_*_integration.py".to_string()],
                norecursedirs: vec![],
                ..make_config(utf8_dir).paths
            },
            ..make_config(utf8_dir)
        };
        let (files, _) = collect_files(&config).unwrap();
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].file_name().unwrap(), "test_foo_integration.py");
    }

    #[test]
    fn test_collect_finds_conftest_in_directory() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("conftest.py").touch().unwrap();
        dir.child("test_foo.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (_, conftests) = collect_files(&config).unwrap();
        assert_eq!(conftests.len(), 1);
        assert_eq!(conftests[0].file_name().unwrap(), "conftest.py");
    }

    #[test]
    fn test_collect_conftest_in_subdirectory() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("tests/conftest.py").touch().unwrap();
        dir.child("tests/test_foo.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (_, conftests) = collect_files(&config).unwrap();
        assert_eq!(conftests.len(), 1);
    }

    #[test]
    fn test_collect_no_conftest_returns_empty_vec() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("test_foo.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (_, conftests) = collect_files(&config).unwrap();
        assert!(conftests.is_empty());
    }

    #[test]
    fn test_collect_deduplicates_conftest_paths() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("conftest.py").touch().unwrap();
        dir.child("test_a.py").touch().unwrap();
        dir.child("test_b.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (_, conftests) = collect_files(&config).unwrap();
        assert_eq!(conftests.len(), 1);
    }

    #[test]
    fn test_collect_respects_gitignore() {
        let dir = assert_fs::TempDir::new().unwrap();
        std::process::Command::new("git")
            .args(["init"])
            .current_dir(dir.path())
            .output()
            .unwrap();
        dir.child(".gitignore").write_str("ignored_dir/\n").unwrap();
        dir.child("ignored_dir/test_hidden.py").touch().unwrap();
        dir.child("test_visible.py").touch().unwrap();

        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (files, _) = collect_files(&config).unwrap();
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].file_name().unwrap(), "test_visible.py");
    }

    #[test]
    fn test_collect_gitignore_disabled() {
        let dir = assert_fs::TempDir::new().unwrap();
        std::process::Command::new("git")
            .args(["init"])
            .current_dir(dir.path())
            .output()
            .unwrap();
        dir.child(".gitignore").write_str("ignored_dir/\n").unwrap();
        dir.child("ignored_dir/test_hidden.py").touch().unwrap();
        dir.child("test_visible.py").touch().unwrap();

        let utf8_dir = camino::Utf8Path::from_path(dir.path()).unwrap();
        let mut config = make_config(utf8_dir);
        config.paths.use_gitignore = false;
        let config = config;
        let (files, _) = collect_files(&config).unwrap();
        assert_eq!(files.len(), 2);
    }

    #[test]
    fn test_collect_no_git_repo_still_works() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("test_foo.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (files, _) = collect_files(&config).unwrap();
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_collect_gitignore_and_norecursedirs_additive() {
        let dir = assert_fs::TempDir::new().unwrap();
        std::process::Command::new("git")
            .args(["init"])
            .current_dir(dir.path())
            .output()
            .unwrap();
        dir.child(".gitignore").write_str("git_ignored/\n").unwrap();
        dir.child("git_ignored/test_a.py").touch().unwrap();
        dir.child("custom_ignored/test_b.py").touch().unwrap();
        dir.child("test_visible.py").touch().unwrap();

        let utf8_dir = camino::Utf8Path::from_path(dir.path()).unwrap();
        let mut config = make_config(utf8_dir);
        config.paths.norecursedirs = vec![
            ".git".to_string(),
            "__pycache__".to_string(),
            "custom_ignored".to_string(),
        ];
        let config = config;
        let (files, _) = collect_files(&config).unwrap();
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].file_name().unwrap(), "test_visible.py");
    }

    #[test]
    fn test_collect_gitignore_filters_conftest_too() {
        let dir = assert_fs::TempDir::new().unwrap();
        std::process::Command::new("git")
            .args(["init"])
            .current_dir(dir.path())
            .output()
            .unwrap();
        dir.child(".gitignore").write_str("ignored_dir/\n").unwrap();
        dir.child("ignored_dir/conftest.py").touch().unwrap();
        dir.child("ignored_dir/test_hidden.py").touch().unwrap();
        dir.child("test_visible.py").touch().unwrap();

        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (files, conftests) = collect_files(&config).unwrap();
        assert_eq!(files.len(), 1);
        assert!(conftests.is_empty());
    }

    #[test]
    fn test_collect_paths_are_canonical_absolute() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("test_foo.py").touch().unwrap();
        let utf8_dir = camino::Utf8Path::from_path(dir.path()).unwrap();
        let config = make_config(utf8_dir);
        let (files, _) = collect_files(&config).unwrap();
        assert_eq!(files.len(), 1);
        let path_str = files[0].as_str();
        assert!(
            path_str.starts_with('/'),
            "collected path should be absolute, got: {path_str}"
        );
        assert!(
            !path_str.contains("/./"),
            "collected path should have no ./ components, got: {path_str}"
        );
    }

    #[test]
    fn test_collect_normalizes_dotslash_paths() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("sub/test_foo.py").touch().unwrap();
        let utf8_dir = camino::Utf8Path::from_path(dir.path()).unwrap();
        let canonical_root = match std::fs::canonicalize(utf8_dir.as_std_path()) {
            Ok(p) => Utf8PathBuf::from_path_buf(p).unwrap(),
            Err(_) => utf8_dir.to_owned(),
        };

        let mut config_dot = make_config(utf8_dir);
        config_dot.rootdir = canonical_root.clone();
        config_dot.paths.testpaths = vec![canonical_root.join("./sub/test_foo.py")];
        let (files_dot, _) = collect_files(&config_dot).unwrap();

        let mut config_plain = make_config(utf8_dir);
        config_plain.rootdir = canonical_root.clone();
        config_plain.paths.testpaths = vec![canonical_root.join("sub/test_foo.py")];
        let (files_plain, _) = collect_files(&config_plain).unwrap();

        assert_eq!(files_dot.len(), 1);
        assert_eq!(files_plain.len(), 1);
        assert_eq!(
            files_dot[0].as_str(),
            files_plain[0].as_str(),
            "different input forms must produce identical collected paths"
        );
    }

    #[test]
    fn conftests_for_modules_returns_ancestor_chain_only() {
        let all_conftests = vec![
            Utf8PathBuf::from("/project/conftest.py"),
            Utf8PathBuf::from("/project/tests/conftest.py"),
            Utf8PathBuf::from("/project/tests/unit/conftest.py"),
            Utf8PathBuf::from("/project/tests/integration/conftest.py"),
            Utf8PathBuf::from("/project/tests/e2e/conftest.py"),
        ];
        let matched_modules = vec![Utf8PathBuf::from("/project/tests/unit/test_auth.py")];
        let result = conftests_for_modules(&all_conftests, &matched_modules);
        assert!(result.contains(&Utf8PathBuf::from("/project/conftest.py")));
        assert!(result.contains(&Utf8PathBuf::from("/project/tests/conftest.py")));
        assert!(result.contains(&Utf8PathBuf::from("/project/tests/unit/conftest.py")));
        assert!(!result.contains(&Utf8PathBuf::from("/project/tests/integration/conftest.py")));
        assert!(!result.contains(&Utf8PathBuf::from("/project/tests/e2e/conftest.py")));
        assert_eq!(result.len(), 3);
    }

    #[test]
    fn conftests_for_modules_multiple_matched_dirs() {
        let all_conftests = vec![
            Utf8PathBuf::from("/project/conftest.py"),
            Utf8PathBuf::from("/project/tests/unit/conftest.py"),
            Utf8PathBuf::from("/project/tests/integration/conftest.py"),
        ];
        let matched_modules = vec![
            Utf8PathBuf::from("/project/tests/unit/test_a.py"),
            Utf8PathBuf::from("/project/tests/integration/test_b.py"),
        ];
        let result = conftests_for_modules(&all_conftests, &matched_modules);
        assert_eq!(result.len(), 3);
    }

    #[test]
    fn conftests_for_modules_empty_matched_returns_empty() {
        let all_conftests = vec![Utf8PathBuf::from("/project/conftest.py")];
        let matched_modules: Vec<Utf8PathBuf> = vec![];
        let result = conftests_for_modules(&all_conftests, &matched_modules);
        assert!(result.is_empty());
    }

    #[test]
    fn test_collect_with_invalid_glob_pattern_returns_error() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("test_foo.py").touch().unwrap();
        let utf8_dir = camino::Utf8Path::from_path(dir.path()).unwrap();
        let mut config = make_config(utf8_dir);
        config.paths.python_files = vec!["test_*.py".to_string(), "[".to_string()];
        config.paths.norecursedirs = vec![];
        let config = config;
        let err = collect_files(&config).unwrap_err();
        assert!(
            err.to_string().contains('['),
            "error should mention the invalid pattern"
        );
    }
}
