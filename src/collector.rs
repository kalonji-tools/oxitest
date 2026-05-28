//! File discovery — walks the filesystem to find test files and conftest files.
//!
//! Uses `testpaths`, `python_files`, and `norecursedirs` from [`Config`] to
//! match files via glob patterns. Returns deduplicated, sorted lists of
//! test file paths and conftest paths.

use camino::{Utf8Path, Utf8PathBuf};
use globset::{GlobBuilder, GlobSet, GlobSetBuilder};
use std::collections::HashSet;
use walkdir::WalkDir;

use crate::config::Config;

fn build_glob_set(patterns: &[String]) -> GlobSet {
    let mut builder = GlobSetBuilder::new();
    for pattern in patterns {
        match GlobBuilder::new(pattern).build() {
            Ok(glob) => {
                builder.add(glob);
            }
            Err(e) => {
                tracing::warn!(
                    pattern = %pattern,
                    error = %e,
                    "invalid glob pattern, skipping"
                );
            }
        }
    }
    builder.build().unwrap_or_else(|_| GlobSet::empty())
}

/// Returns `(test_files, conftest_files)` sorted by path.
/// conftest_files are deduplicated and sorted shallow-first.
pub fn collect_files(config: &Config) -> (Vec<Utf8PathBuf>, Vec<Utf8PathBuf>) {
    let glob_set = build_glob_set(&config.python_files);
    let mut files = Vec::new();
    let mut conftest_set: HashSet<Utf8PathBuf> = HashSet::new();

    // Always check the rootdir itself for a conftest.py (covers the case where
    // testpaths point to a subdirectory and conftest.py lives at the project root).
    let rootdir_conftest = config.rootdir.join("conftest.py");
    if rootdir_conftest.exists() {
        conftest_set.insert(rootdir_conftest);
    }

    for testpath in &config.testpaths {
        collect_from(testpath, config, &glob_set, &mut files, &mut conftest_set);
    }

    files.sort();

    let mut conftests: Vec<Utf8PathBuf> = conftest_set.into_iter().collect();
    conftests.sort_by_key(|p| p.components().count());

    (files, conftests)
}

fn collect_from(
    path: &Utf8Path,
    config: &Config,
    glob_set: &GlobSet,
    out: &mut Vec<Utf8PathBuf>,
    conftests: &mut HashSet<Utf8PathBuf>,
) {
    if path.is_file() {
        if let Some(filename) = path.file_name() {
            if glob_set.is_match(filename) {
                out.push(path.to_owned());
            }
        }
        // Check for conftest.py in the same directory as a directly specified file
        if let Some(parent) = path.parent() {
            let conftest = parent.join("conftest.py");
            if conftest.exists() {
                conftests.insert(conftest);
            }
        }
        return;
    }

    for entry in WalkDir::new(path)
        .follow_links(false)
        .into_iter()
        .filter_entry(|e| {
            let name = e.file_name().to_string_lossy(); // OsStr from walkdir
            if e.file_type().is_dir() {
                return !config.norecursedirs.iter().any(|d| d == name.as_ref());
            }
            true
        })
        .filter_map(|e| e.ok())
    {
        if entry.file_type().is_dir() {
            let conftest_std = entry.path().join("conftest.py"); // &Path from walkdir
            if conftest_std.exists() {
                match Utf8PathBuf::from_path_buf(conftest_std) {
                    Ok(utf8) => {
                        conftests.insert(utf8);
                    }
                    Err(p) => tracing::warn!(path = ?p, "skipping non-UTF-8 conftest path"),
                }
            }
        } else if entry.file_type().is_file() {
            let filename = entry.file_name(); // OsStr — glob accepts AsRef<Path>
            if glob_set.is_match(filename) {
                match Utf8PathBuf::from_path_buf(entry.into_path()) {
                    Ok(utf8) => out.push(utf8),
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
        Config {
            rootdir: dir.to_owned(),
            testpaths: vec![dir.to_owned()],
            python_files: vec!["test_*.py".to_string(), "*_test.py".to_string()],
            norecursedirs: vec![".git".to_string(), "__pycache__".to_string()],
            maxfail: 0,
            registered_markers: vec![],
            timeout_secs: None,
            serial: false,
            debug: None,
            workers: None,
            cache_max_age: 50,
            min_parallel_tests: 100,
            timeout_multiplier: None,
            spawn_overhead_ms: 250.0,
            strict: None,
            markers_without_description: vec![],
            schedule: crate::config::ScheduleStrategy::LongestFirst,
            failed: None,
            tb: crate::config::TbStyle::Short,
            verbose: false,
            durations: None,
            color: crate::config::ColorMode::Auto,
            plugins: vec![],
            plugin_settings: std::collections::HashMap::new(),
            async_backend: "asyncio".to_string(),
            affected: None,
            affected_base: "HEAD".to_string(),
            retries: 0,
            retries_delay_secs: 0,
        }
    }

    #[test]
    fn test_collect_empty_dir() {
        let dir = assert_fs::TempDir::new().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (files, conftests) = collect_files(&config);
        assert!(files.is_empty());
        assert!(conftests.is_empty());
    }

    #[test]
    fn test_collect_finds_test_file() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("test_foo.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (files, _) = collect_files(&config);
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].file_name().unwrap(), "test_foo.py");
    }

    #[test]
    fn test_collect_ignores_non_test_files() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("helper.py").touch().unwrap();
        dir.child("test_real.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (files, _) = collect_files(&config);
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].file_name().unwrap(), "test_real.py");
    }

    #[test]
    fn test_collect_respects_norecursedirs() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("__pycache__/test_hidden.py").touch().unwrap();
        dir.child("test_visible.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (files, _) = collect_files(&config);
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
            testpaths: vec![camino::Utf8Path::from_path(f.path()).unwrap().to_owned()],
            python_files: vec!["test_*.py".to_string()],
            norecursedirs: vec![],
            ..make_config(utf8_dir)
        };
        let (files, _) = collect_files(&config);
        assert_eq!(
            files,
            vec![camino::Utf8Path::from_path(f.path()).unwrap().to_owned()]
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
            python_files: vec!["test_*_integration.py".to_string()],
            norecursedirs: vec![],
            ..make_config(utf8_dir)
        };
        let (files, _) = collect_files(&config);
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].file_name().unwrap(), "test_foo_integration.py");
    }

    #[test]
    fn test_collect_finds_conftest_in_directory() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("conftest.py").touch().unwrap();
        dir.child("test_foo.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (_, conftests) = collect_files(&config);
        assert_eq!(conftests.len(), 1);
        assert_eq!(conftests[0].file_name().unwrap(), "conftest.py");
    }

    #[test]
    fn test_collect_conftest_in_subdirectory() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("tests/conftest.py").touch().unwrap();
        dir.child("tests/test_foo.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (_, conftests) = collect_files(&config);
        assert_eq!(conftests.len(), 1);
    }

    #[test]
    fn test_collect_no_conftest_returns_empty_vec() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("test_foo.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (_, conftests) = collect_files(&config);
        assert!(conftests.is_empty());
    }

    #[test]
    fn test_collect_deduplicates_conftest_paths() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("conftest.py").touch().unwrap();
        dir.child("test_a.py").touch().unwrap();
        dir.child("test_b.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let (_, conftests) = collect_files(&config);
        assert_eq!(conftests.len(), 1);
    }

    #[test]
    fn test_collect_with_invalid_glob_pattern_does_not_crash() {
        // "[" is an unclosed bracket — invalid in globset syntax.
        // The invalid pattern should be skipped; the valid pattern must still work.
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("test_foo.py").touch().unwrap();
        let utf8_dir = camino::Utf8Path::from_path(dir.path()).unwrap();
        let config = Config {
            python_files: vec!["test_*.py".to_string(), "[".to_string()],
            norecursedirs: vec![],
            ..make_config(utf8_dir)
        };
        let (files, _) = collect_files(&config);
        assert_eq!(
            files.len(),
            1,
            "valid pattern must still match despite bad sibling"
        );
        assert_eq!(files[0].file_name().unwrap(), "test_foo.py");
    }
}
