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

pub fn build_glob_set(patterns: &[String]) -> Result<GlobSet, globset::Error> {
    let mut builder = GlobSetBuilder::new();
    for pattern in patterns {
        builder.add(GlobBuilder::new(pattern).build()?);
    }
    builder.build()
}

/// Returns `(test_files, conftest_files)` sorted by path.
/// `conftest_files` are deduplicated and sorted shallow-first.
pub fn collect_files(
    config: &Config,
) -> Result<(Vec<Utf8PathBuf>, Vec<Utf8PathBuf>), globset::Error> {
    collect_files_in(&config.paths.testpaths, config)
}

/// [`collect_files`] over an explicit set of roots.
///
/// The roots are a parameter because three callers need a walk that is *not*
/// `config.paths.testpaths`: the rootdir-package derivations, which must ignore
/// how argv narrowed the run, and the doctest coverage audit, which reads the
/// declared tree. Each of them used to clone the whole `Config` and overwrite
/// one field to say so (#1798).
///
/// Everything else — `python_files`, `norecursedirs`, `use_gitignore`, the
/// rootdir `conftest.py` — still comes from `config`, so a caller can change
/// *where* the walk starts and nothing about what counts as a test file.
pub fn collect_files_in(
    roots: &[Utf8PathBuf],
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

    for testpath in roots {
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
///
/// Returns `Err` rather than an empty list: an empty `GlobSet` matches nothing,
/// so swallowing the error would silently drop the whole doctest suite under a
/// green gate.
pub fn collect_doctest_files(config: &Config) -> Result<Vec<Utf8PathBuf>, globset::Error> {
    collect_doctest_files_in(&config.paths.testpaths, config)
}

/// [`collect_doctest_files`] over an explicit set of roots. See
/// [`collect_files_in`] for why the roots are a parameter.
fn collect_doctest_files_in(
    roots: &[Utf8PathBuf],
    config: &Config,
) -> Result<Vec<Utf8PathBuf>, globset::Error> {
    let glob_set = build_glob_set(&["*.py".to_string()])?;
    let mut files = Vec::new();
    let mut dummy_conftests = HashSet::new();

    for testpath in roots {
        collect_from(
            testpath,
            config,
            &glob_set,
            &mut files,
            &mut dummy_conftests,
        );
    }

    files.sort();
    Ok(files)
}

/// The roots the doctest coverage audit walks — the project's *declared*
/// auditable surface, never the effective run set.
///
/// Falls back to `rootdir` when the project declared nothing, rather than to
/// [`crate::config::PathConfig::testpaths`]: that field is overwritten by
/// positional CLI paths (`merge_paths`), so falling back to it made
/// `oxitest tests/` audit only `tests/` in a zero-config project — the #1798
/// defect surviving in the undeclared branch, where no test reached.
///
/// The default lives here rather than in the field because
/// [`crate::config::PathConfig::declared_testpaths`] documents "empty means the
/// project declared nothing", which ADR-0009 Rule 4's rootdir package depends
/// on. Materialising it there would move that rootdir for every zero-config
/// project.
///
/// Two callers, and they must stay the same call:
/// [`collect_declared_doctest_files`] walks these roots, and
/// `StalenessInputs::is_unreachable` (`src/pipeline/collection.rs`) judges
/// scope/skip entries against them. If the two ever compute their roots
/// separately they can disagree silently, and the disagreement surfaces as a
/// correct entry reported stale — the shape that reopened #1796 three times.
pub fn coverage_roots(config: &Config) -> &[Utf8PathBuf] {
    if config.paths.declared_testpaths.is_empty() {
        std::slice::from_ref(&config.rootdir)
    } else {
        &config.paths.declared_testpaths
    }
}

/// Collect the `.py` files of the **declared** test tree, for the doctest
/// coverage audit.
///
/// [`collect_doctest_files`] walks `testpaths`, which positional CLI paths
/// overwrite. So `oxitest tests/` stopped auditing every subject outside
/// `tests/` — a green run that had audited nothing, with no diagnostic saying
/// so. Coverage asks what the *project* declares as its surface, which is
/// `declared_testpaths` (#1798).
///
/// Deliberately **unfiltered**, unlike the rootdir package's fold: a declared
/// directory holding no test files is precisely what coverage exists to audit —
/// this project declares `python/oxitest` for that reason — so the filter that
/// keeps such a directory out of the rootdir fold must not reach here.
///
/// The *item* walk stays on `testpaths`. Pointing both at the declared tree
/// would make `oxitest tests/test_one.py` execute every doctest in the project.
///
/// Roots come from [`coverage_roots`], which is also what the staleness guard
/// judges scope/skip entries against.
pub fn collect_declared_doctest_files(config: &Config) -> Result<Vec<Utf8PathBuf>, globset::Error> {
    collect_doctest_files_in(coverage_roots(config), config)
}

/// Return only conftests that are ancestors of any matched test module.
///
/// A conftest is an ancestor if its directory is a prefix of any matched
/// module's directory. Results are sorted shallow-first (by component count).
pub fn conftests_for_modules(
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

    /// The distinction from `collect_files`: a regression that reused the
    /// configured `test_*.py` globs would silently uncollect every non-test
    /// module's doctests while still returning files.
    #[test]
    fn doctest_collection_takes_every_py_file_not_just_test_files() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("test_foo.py").touch().unwrap();
        dir.child("helpers.py").touch().unwrap();
        dir.child("notes.md").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());

        let files = collect_doctest_files(&config).expect("`*.py` is a literal glob");

        let mut names: Vec<&str> = files.iter().filter_map(|f| f.file_name()).collect();
        names.sort_unstable();
        assert_eq!(
            names,
            ["helpers.py", "test_foo.py"],
            "doctest collection must reach modules `python_files` excludes, and \
             must not pick up non-Python files"
        );
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
        let path = camino::Utf8Path::new(path_str);
        // `starts_with('/')` until #1986: that spells "absolute" in POSIX
        // syntax, so on Windows it rejected a perfectly canonical
        // `\\?\C:\Users\...\test_foo.py`. `is_absolute()` asks the question the
        // test means on whichever platform it runs.
        assert!(
            path.is_absolute(),
            "collected path should be absolute, got: {path_str}"
        );
        // Component-wise rather than a `"/./"` substring search — that spelling
        // was POSIX-only too, and it could not see a trailing `/.` or any `..`
        // at all. This is the stronger assertion, not a portable rewrite of the
        // weaker one.
        assert!(
            !path.components().any(|component| matches!(
                component,
                camino::Utf8Component::CurDir | camino::Utf8Component::ParentDir
            )),
            "collected path should have no . or .. components, got: {path_str}"
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

#[cfg(test)]
mod slice1_collector_tests {
    use std::fs;
    use tempfile::TempDir;

    #[test]
    fn prescan_fixture_module_recognizes_oxi_fixture_in_same_dir() {
        // Inlined from prescan_test_module tests: verify that a __fixtures__.py
        // sibling containing @oxi.fixture returns HasFixtures when prescanned.
        let tmp = TempDir::new().unwrap();
        let pkg = tmp.path().join("slice1_pkg");
        fs::create_dir(&pkg).unwrap();
        fs::write(pkg.join("__init__.py"), "").unwrap();
        fs::write(
            pkg.join("__fixtures__.py"),
            r#"
import oxitest as oxi

@oxi.fixture(lifetime="function")
def conn():
    return object()
"#,
        )
        .unwrap();

        let fixture_path = camino::Utf8PathBuf::from_path_buf(pkg.join("__fixtures__.py")).unwrap();
        let result = crate::prescan::prescan_fixture_module(&fixture_path);
        assert!(
            matches!(result, crate::prescan::PrescanFixtureResult::HasFixtures(_)),
            "a __fixtures__.py with @oxi.fixture must prescan as HasFixtures"
        );
    }

    #[test]
    fn prescan_fixture_module_returns_none_when_no_sibling_file() {
        // Inlined from prescan_test_module tests: verify that attempting to
        // prescan a non-existent __fixtures__.py yields Unavailable (file not found).
        let tmp = TempDir::new().unwrap();
        let pkg = tmp.path().join("slice1_pkg");
        fs::create_dir(&pkg).unwrap();

        let missing_path = camino::Utf8PathBuf::from_path_buf(pkg.join("__fixtures__.py")).unwrap();
        let result = crate::prescan::prescan_fixture_module(&missing_path);
        assert!(
            matches!(result, crate::prescan::PrescanFixtureResult::Unavailable),
            "a missing __fixtures__.py must prescan as Unavailable (I/O error path)"
        );
    }
}
