//! File discovery — walks the filesystem to find test files.
//!
//! Uses `testpaths`, `python_files`, and `norecursedirs` from [`Config`] to
//! match files via glob patterns. Returns a deduplicated, sorted list of
//! test file paths.

use camino::{Utf8Path, Utf8PathBuf};
use globset::{GlobBuilder, GlobSet, GlobSetBuilder};
use ignore::WalkBuilder;

use crate::config::Config;

/// Canonicalize a path to produce a stable absolute form.
///
/// Resolves `.`, `..`, and symlinks via `std::fs::canonicalize`.
/// Falls back to the original path if canonicalization fails.
///
/// Also used by the plugin-anchor boundary in `bridge.rs`, so that an anchor
/// is spelled the way the collector spells what it is compared against. A
/// second idiom there would reintroduce the divergence this removes (#1767).
///
/// `pub` rather than `pub(crate)`: `collector` is a private module
/// (`lib.rs`), so the crate boundary is already the limit and
/// `clippy::redundant_pub_crate` refuses the narrower spelling.
pub fn normalize_path(path: &Utf8Path) -> Utf8PathBuf {
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

/// Returns the test files, sorted by path.
pub fn collect_files(config: &Config) -> Result<Vec<Utf8PathBuf>, globset::Error> {
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
/// Everything else — `python_files`, `norecursedirs`, `use_gitignore` — still
/// comes from `config`, so a caller can change *where* the walk starts and
/// nothing about what counts as a test file.
pub fn collect_files_in(
    roots: &[Utf8PathBuf],
    config: &Config,
) -> Result<Vec<Utf8PathBuf>, globset::Error> {
    walk_matching(roots, config, &config.paths.python_files)
}

/// Walk `roots` and return every file matching `patterns`, sorted.
///
/// The two public walks differ only in where their patterns come from —
/// `python_files` for test collection, `*.py` for doctests. They were distinct
/// bodies while each also built a `conftest.py` set; retiring that walk (#2168)
/// left them identical, so the shared half lives here.
fn walk_matching(
    roots: &[Utf8PathBuf],
    config: &Config,
    patterns: &[String],
) -> Result<Vec<Utf8PathBuf>, globset::Error> {
    let glob_set = build_glob_set(patterns)?;
    let mut files = Vec::new();

    for root in roots {
        collect_from(root, config, &glob_set, &mut files);
    }

    files.sort();

    Ok(files)
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
    walk_matching(roots, config, &["*.py".to_string()])
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
/// An explicit `[tool.oxitest.doctest] roots` wins over both branches below.
/// It is resolved here rather than at either caller so the walk and the
/// staleness guard cannot disagree — see the note above.
pub fn coverage_roots(config: &Config) -> &[Utf8PathBuf] {
    if let Some(dt) = config.doctest.as_ref()
        && !dt.roots.is_empty()
    {
        return &dt.roots;
    }
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
fn collect_from(path: &Utf8Path, config: &Config, glob_set: &GlobSet, out: &mut Vec<Utf8PathBuf>) {
    if path.is_file() {
        if let Some(filename) = path.file_name()
            && glob_set.is_match(filename)
        {
            out.push(normalize_path(path));
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
        if ft.is_file() {
            let filename = entry.file_name();
            if glob_set.is_match(filename) {
                match Utf8PathBuf::from_path_buf(entry.into_path()) {
                    Ok(utf8) => out.push(normalize_path(&utf8)),
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
        let files = collect_files(&config).unwrap();
        assert!(files.is_empty());
    }

    #[test]
    fn test_collect_finds_test_file() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("test_foo.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let files = collect_files(&config).unwrap();
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].file_name().unwrap(), "test_foo.py");
    }

    #[test]
    fn test_collect_ignores_non_test_files() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("helper.py").touch().unwrap();
        dir.child("test_real.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let files = collect_files(&config).unwrap();
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].file_name().unwrap(), "test_real.py");
    }

    #[test]
    fn test_collect_respects_norecursedirs() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("__pycache__/test_hidden.py").touch().unwrap();
        dir.child("test_visible.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let files = collect_files(&config).unwrap();
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
        let files = collect_files(&config).unwrap();
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
        let files = collect_files(&config).unwrap();
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].file_name().unwrap(), "test_foo_integration.py");
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
        let files = collect_files(&config).unwrap();
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
        let files = collect_files(&config).unwrap();
        assert_eq!(files.len(), 2);
    }

    #[test]
    fn test_collect_no_git_repo_still_works() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("test_foo.py").touch().unwrap();
        let config = make_config(camino::Utf8Path::from_path(dir.path()).unwrap());
        let files = collect_files(&config).unwrap();
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
        let files = collect_files(&config).unwrap();
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].file_name().unwrap(), "test_visible.py");
    }

    #[test]
    fn test_collect_paths_are_canonical_absolute() {
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("test_foo.py").touch().unwrap();
        let utf8_dir = camino::Utf8Path::from_path(dir.path()).unwrap();
        let config = make_config(utf8_dir);
        let files = collect_files(&config).unwrap();
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
        let files_dot = collect_files(&config_dot).unwrap();

        let mut config_plain = make_config(utf8_dir);
        config_plain.rootdir = canonical_root.clone();
        config_plain.paths.testpaths = vec![canonical_root.join("sub/test_foo.py")];
        let files_plain = collect_files(&config_plain).unwrap();

        assert_eq!(files_dot.len(), 1);
        assert_eq!(files_plain.len(), 1);
        assert_eq!(
            files_dot[0].as_str(),
            files_plain[0].as_str(),
            "different input forms must produce identical collected paths"
        );
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
            matches!(
                result,
                crate::prescan::PrescanFixtureResult::Unavailable(
                    crate::python_ast::ParseFailure::Io { .. }
                )
            ),
            "a missing __fixtures__.py must prescan as Unavailable via the Io arm — routing it \
             through Parse would report a read failure as a syntax error the user cannot find"
        );
    }

    #[test]
    fn prescan_fixture_module_rejects_invalid_utf8() {
        // A file that is not valid UTF-8 never reaches the parser:
        // `read_to_string` refuses it, so it arrives as `Io` and is reported
        // in `std::io::Error`'s own words. Pinned so the routing stays a
        // decision — the two arms are named for parse and read, and this is a
        // third cause that has to land in one of them (#1727).
        let tmp = TempDir::new().unwrap();
        let pkg = tmp.path().join("utf8_pkg");
        fs::create_dir(&pkg).unwrap();

        let path = camino::Utf8PathBuf::from_path_buf(pkg.join("__fixtures__.py")).unwrap();
        fs::write(&path, b"import oxitest\n# \xff\xfe not utf-8\n").unwrap();

        let result = crate::prescan::prescan_fixture_module(&path);
        let crate::prescan::PrescanFixtureResult::Unavailable(
            crate::python_ast::ParseFailure::Io { cause },
        ) = result
        else {
            panic!(
                "invalid UTF-8 must arrive via the Io arm — the file never parses, so a Parse \
                 verdict would carry a line number no parser produced: got {result:?}"
            );
        };
        assert!(
            !cause.is_empty(),
            "the cause carries the whole explanation for this case, because there is no line \
             number to report alongside it"
        );
    }
}
