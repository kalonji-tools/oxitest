//! Instant-tier AST extraction for the unified query system.
//!
//! Builds [`crate::query::resource::QueryEntry`] lists from Python source files
//! without importing modules — pure Rust AST analysis via [`crate::python_ast`].

use std::collections::{BTreeMap, BTreeSet, HashMap};

use camino::Utf8PathBuf;
use rayon::prelude::*;

use crate::python_ast;
use crate::query::resource::QueryEntry;
use rustpython_parser::ast;

// ── Test extraction ────────────────────────────────────────────────────────────

/// Build [`QueryEntry`] records for every test function in the given files.
///
/// Each entry has fields:
/// - `name`: `<file_path>::<function_name>` node-id format
/// - `source`: the file path as a string
/// - `async`: `"true"` or `"false"`
/// - `mark`: comma-joined mark names from decorators (empty string if none)
pub(crate) fn extract_test_entries(test_files: &[Utf8PathBuf]) -> Vec<QueryEntry> {
    let mut entries = Vec::new();

    for file in test_files {
        let Some((_, stmts)) = python_ast::parse_file(file) else {
            continue;
        };

        python_ast::walk_test_defs(&stmts, |def, class| {
            let marks = collect_fn_marks(def.decorator_list());
            let class_name = class.map(|c| c.name.as_str());
            entries.push(make_test_entry(
                file,
                def.name(),
                class_name,
                def.is_async(),
                &marks,
            ));
        });
    }

    entries
}

fn collect_fn_marks(decorator_list: &[ast::Expr]) -> Vec<String> {
    decorator_list
        .iter()
        .filter_map(python_ast::extract_mark_name)
        .collect()
}

fn make_test_entry(
    file: &Utf8PathBuf,
    fn_name: &str,
    class_name: Option<&str>,
    is_async: bool,
    marks: &[String],
) -> QueryEntry {
    let mut fields = HashMap::new();
    let name = match class_name {
        Some(cls) => format!("{}::{}::{}", file, cls, fn_name),
        None => format!("{}::{}", file, fn_name),
    };
    fields.insert("name".to_string(), name);
    fields.insert("source".to_string(), file.to_string());
    fields.insert("async".to_string(), is_async.to_string());
    fields.insert("mark".to_string(), marks.join(","));
    QueryEntry { fields }
}

// ── Mark extraction ────────────────────────────────────────────────────────────

/// Build [`QueryEntry`] records for every marker name seen in the given files
/// plus any registered markers from pyproject.toml.
///
/// Each entry has fields:
/// - `name`: the marker name
/// - `source`: comma-joined file paths where the marker appears (empty for
///   registered-only markers)
pub(crate) fn extract_mark_entries(
    test_files: &[Utf8PathBuf],
    registered_markers: &[String],
) -> Vec<QueryEntry> {
    // BTreeMap preserves insertion order (sorted by mark name for determinism).
    let mut mark_sources: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();

    // Seed with registered markers (source = empty set until we see them in files)
    for marker in registered_markers {
        mark_sources.entry(marker.clone()).or_default();
    }

    // Scan AST for decorator marks
    for file in test_files {
        let Some((_, stmts)) = python_ast::parse_file(file) else {
            continue;
        };
        let marks = python_ast::extract_decorator_marks(&stmts);
        for mark in marks {
            mark_sources
                .entry(mark)
                .or_default()
                .insert(file.to_string());
        }
    }

    mark_sources
        .into_iter()
        .map(|(name, sources)| {
            let mut fields = HashMap::new();
            let source_list: Vec<_> = sources.into_iter().collect();
            fields.insert("name".to_string(), name);
            fields.insert("used_in".to_string(), source_list.join(","));
            QueryEntry { fields }
        })
        .collect()
}

// ── Combined extraction ────────────────────────────────────────────────────────

/// Parse each file ONCE and return both test entries and mark entries.
///
/// This avoids double-parsing when both kinds of data are needed (e.g. in
/// `oxitest inspect`).  The two returned `Vec`s are equivalent to calling
/// [`extract_test_entries`] and [`extract_mark_entries`] separately on the
/// same inputs.
pub(crate) fn extract_test_and_mark_entries(
    test_files: &[Utf8PathBuf],
    registered_markers: &[String],
) -> (Vec<QueryEntry>, Vec<QueryEntry>) {
    // Parallel phase: parse each file independently on a rayon thread pool.
    // Each worker returns (file_path, test_entries_for_file, mark_names_for_file).
    let per_file: Vec<(String, Vec<QueryEntry>, Vec<String>)> = test_files
        .par_iter()
        .filter_map(|file| {
            let (_, stmts) = python_ast::parse_file(file)?;

            let mut tests = Vec::new();
            python_ast::walk_test_defs(&stmts, |def, class| {
                let marks = collect_fn_marks(def.decorator_list());
                let class_name = class.map(|c| c.name.as_str());
                tests.push(make_test_entry(
                    file,
                    def.name(),
                    class_name,
                    def.is_async(),
                    &marks,
                ));
            });

            let marks = python_ast::extract_decorator_marks(&stmts);
            Some((file.to_string(), tests, marks))
        })
        .collect();

    // Sequential merge phase (cheap — just Vec/BTreeMap insertions).
    let mut test_entries = Vec::new();
    let mut mark_sources: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();

    // Seed with registered markers (source = empty set until we see them in files).
    for marker in registered_markers {
        mark_sources.entry(marker.clone()).or_default();
    }

    for (file_path, tests, marks) in per_file {
        test_entries.extend(tests);
        for mark in marks {
            mark_sources
                .entry(mark)
                .or_default()
                .insert(file_path.clone());
        }
    }

    let mark_entries = mark_sources
        .into_iter()
        .map(|(name, sources)| {
            let mut fields = HashMap::new();
            let source_list: Vec<_> = sources.into_iter().collect();
            fields.insert("name".to_string(), name);
            fields.insert("used_in".to_string(), source_list.join(","));
            QueryEntry { fields }
        })
        .collect();

    (test_entries, mark_entries)
}

// ── Helper extraction ──────────────────────────────────────────────────────────

/// Build [`QueryEntry`] records for every public helper function in the given
/// conftest files.
///
/// Each entry has fields:
/// - `name`: the function name
/// - `source`: the conftest file path
/// - `docstring`: the function's docstring (empty string if none)
/// - `signature`: the formatted signature, e.g. `"make_db(name: str)"`
pub(crate) fn extract_helper_entries(conftest_files: &[Utf8PathBuf]) -> Vec<QueryEntry> {
    let mut entries = Vec::new();

    for file in conftest_files {
        let Some((source, stmts)) = python_ast::parse_file(file) else {
            continue;
        };
        let helpers = python_ast::extract_helpers(&stmts, &source);
        for info in helpers {
            let mut fields = HashMap::new();
            fields.insert("name".to_string(), info.name);
            fields.insert("source".to_string(), file.to_string());
            fields.insert("docstring".to_string(), info.docstring.unwrap_or_default());
            fields.insert("signature".to_string(), info.signature);
            entries.push(QueryEntry { fields });
        }
    }

    entries
}

// ── Tests ──────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::python_ast::tests::{temp_path, write_temp_py};

    // ── extract_test_entries ────────────────────────────────────────────────

    #[test]
    fn test_entries_sync_function() {
        let f = write_temp_py("def test_foo(): pass\n");
        let path = temp_path(&f);
        let entries = extract_test_entries(&[path.clone()]);
        assert_eq!(entries.len(), 1);
        let e = &entries[0];
        assert_eq!(e.get("name"), Some(format!("{path}::test_foo").as_str()));
        assert_eq!(e.get("source"), Some(path.as_str()));
        assert_eq!(e.get("async"), Some("false"));
        assert_eq!(e.get("mark"), Some(""));
    }

    #[test]
    fn test_entries_async_function() {
        let f = write_temp_py("async def test_bar(): pass\n");
        let path = temp_path(&f);
        let entries = extract_test_entries(&[path.clone()]);
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].get("async"), Some("true"));
    }

    #[test]
    fn test_entries_with_mark() {
        let f = write_temp_py("import oxitest as oxi\n\n@oxi.mark.slow\ndef test_it(): pass\n");
        let path = temp_path(&f);
        let entries = extract_test_entries(&[path.clone()]);
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].get("mark"), Some("slow"));
    }

    #[test]
    fn test_entries_class_methods() {
        let f = write_temp_py(
            "class TestGroup:\n    def test_a(self): pass\n    def test_b(self): pass\n    def helper(self): pass\n",
        );
        let path = temp_path(&f);
        let entries = extract_test_entries(&[path.clone()]);
        assert_eq!(entries.len(), 2);
        let names: Vec<_> = entries.iter().filter_map(|e| e.get("name")).collect();
        assert!(
            names.iter().any(|n| n.ends_with("::TestGroup::test_a")),
            "expected '::TestGroup::test_a' in names, got: {names:?}"
        );
        assert!(
            names.iter().any(|n| n.ends_with("::TestGroup::test_b")),
            "expected '::TestGroup::test_b' in names, got: {names:?}"
        );
    }

    #[test]
    fn test_entries_async_class_method_includes_class_name() {
        let f = write_temp_py("class TestGroup:\n    async def test_async(self): pass\n");
        let path = temp_path(&f);
        let entries = extract_test_entries(&[path.clone()]);
        assert_eq!(entries.len(), 1);
        let name = entries[0].get("name").unwrap();
        assert!(
            name.ends_with("::TestGroup::test_async"),
            "expected '::TestGroup::test_async', got: {name}"
        );
        assert_eq!(entries[0].get("async"), Some("true"));
    }

    #[test]
    fn test_entries_non_test_class_ignored() {
        let f = write_temp_py("class Helper:\n    def test_method(self): pass\n");
        let path = temp_path(&f);
        let entries = extract_test_entries(&[path.clone()]);
        assert_eq!(entries.len(), 0);
    }

    #[test]
    fn test_entries_nonexistent_file_skipped() {
        let bad = Utf8PathBuf::from("/nonexistent/test_file.py");
        let entries = extract_test_entries(&[bad]);
        assert_eq!(entries.len(), 0);
    }

    #[test]
    fn test_entries_multiple_files() {
        let f1 = write_temp_py("def test_a(): pass\n");
        let f2 = write_temp_py("def test_b(): pass\n");
        let p1 = temp_path(&f1);
        let p2 = temp_path(&f2);
        let entries = extract_test_entries(&[p1, p2]);
        assert_eq!(entries.len(), 2);
    }

    // ── extract_mark_entries ────────────────────────────────────────────────

    #[test]
    fn mark_entries_from_decorator() {
        let f = write_temp_py("import oxitest as oxi\n\n@oxi.mark.slow\ndef test_it(): pass\n");
        let path = temp_path(&f);
        let entries = extract_mark_entries(&[path.clone()], &[]);
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].get("name"), Some("slow"));
        assert!(entries[0].get("used_in").unwrap().contains(path.as_str()));
    }

    #[test]
    fn mark_entries_registered_only() {
        let entries = extract_mark_entries(&[], &["integration".to_string()]);
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].get("name"), Some("integration"));
        assert_eq!(entries[0].get("used_in"), Some(""));
    }

    #[test]
    fn mark_entries_registered_plus_file() {
        let f =
            write_temp_py("import oxitest as oxi\n\n@oxi.mark.integration\ndef test_it(): pass\n");
        let path = temp_path(&f);
        let entries = extract_mark_entries(&[path.clone()], &["integration".to_string()]);
        // integration appears once (deduplicated)
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].get("name"), Some("integration"));
        assert!(entries[0].get("used_in").unwrap().contains(path.as_str()));
    }

    #[test]
    fn mark_entries_deduplicated_across_files() {
        let f1 = write_temp_py("import oxitest as oxi\n\n@oxi.mark.slow\ndef test_a(): pass\n");
        let f2 = write_temp_py("import oxitest as oxi\n\n@oxi.mark.slow\ndef test_b(): pass\n");
        let p1 = temp_path(&f1);
        let p2 = temp_path(&f2);
        let entries = extract_mark_entries(&[p1, p2], &[]);
        // "slow" appears in two files but is one entry with both sources
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].get("name"), Some("slow"));
    }

    #[test]
    fn mark_entries_parametrize_excluded() {
        let f = write_temp_py(
            "import oxitest as oxi\n\n@oxi.mark.parametrize(\"x\", [1, 2])\ndef test_it(x): pass\n",
        );
        let path = temp_path(&f);
        let entries = extract_mark_entries(&[path.clone()], &[]);
        assert_eq!(entries.len(), 0);
    }

    // ── extract_test_and_mark_entries ───────────────────────────────────────

    #[test]
    fn combined_extraction_matches_separate() {
        let f = write_temp_py("import oxitest as oxi\n\n@oxi.mark.slow\ndef test_it(): pass\n");
        let path = temp_path(&f);
        let files = vec![path.clone()];
        let markers = vec!["integration".to_string()];

        let (combined_tests, combined_marks) = extract_test_and_mark_entries(&files, &markers);
        let separate_tests = extract_test_entries(&files);
        let separate_marks = extract_mark_entries(&files, &markers);

        assert_eq!(
            combined_tests, separate_tests,
            "combined test entries must match separate extraction"
        );
        assert_eq!(
            combined_marks, separate_marks,
            "combined mark entries must match separate extraction"
        );
    }

    // ── extract_helper_entries ──────────────────────────────────────────────

    #[test]
    fn helper_entries_public_functions() {
        let f = write_temp_py("def make_thing(): pass\ndef _private(): pass\n");
        let path = temp_path(&f);
        let entries = extract_helper_entries(&[path.clone()]);
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].get("name"), Some("make_thing"));
        assert_eq!(entries[0].get("source"), Some(path.as_str()));
    }

    #[test]
    fn helper_entries_skips_test_functions() {
        let f = write_temp_py("def test_foo(): pass\ndef helper_fn(): pass\n");
        let path = temp_path(&f);
        let entries = extract_helper_entries(&[path.clone()]);
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].get("name"), Some("helper_fn"));
    }

    #[test]
    fn helper_entries_skips_dunder() {
        let f = write_temp_py("def __helpers_namespace__(): pass\ndef public_fn(): pass\n");
        let path = temp_path(&f);
        let entries = extract_helper_entries(&[path.clone()]);
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].get("name"), Some("public_fn"));
    }

    #[test]
    fn helper_entries_nonexistent_file_skipped() {
        let bad = Utf8PathBuf::from("/nonexistent/conftest.py");
        let entries = extract_helper_entries(&[bad]);
        assert_eq!(entries.len(), 0);
    }

    #[test]
    fn helper_entries_multiple_conftest_files() {
        let f1 = write_temp_py("def helper_a(): pass\n");
        let f2 = write_temp_py("def helper_b(): pass\n");
        let p1 = temp_path(&f1);
        let p2 = temp_path(&f2);
        let entries = extract_helper_entries(&[p1, p2]);
        assert_eq!(entries.len(), 2);
        let names: Vec<_> = entries.iter().filter_map(|e| e.get("name")).collect();
        assert!(names.contains(&"helper_a"));
        assert!(names.contains(&"helper_b"));
    }

    #[test]
    fn helper_entries_include_docstring() {
        let f =
            write_temp_py("def make_db():\n    \"\"\"Create a test database.\"\"\"\n    pass\n");
        let path = temp_path(&f);
        let entries = extract_helper_entries(&[path]);
        assert_eq!(entries.len(), 1, "should extract exactly one helper entry");
        assert_eq!(
            entries[0].get("docstring"),
            Some("Create a test database."),
            "QueryEntry should have docstring field populated from function docstring"
        );
    }

    #[test]
    fn helper_entries_include_signature() {
        let f = write_temp_py("def make_db(name: str, shared: bool = False):\n    pass\n");
        let path = temp_path(&f);
        let entries = extract_helper_entries(&[path]);
        assert_eq!(entries.len(), 1, "should extract exactly one helper entry");
        assert_eq!(
            entries[0].get("signature"),
            Some("make_db(name: str, shared: bool = False)"),
            "QueryEntry should have signature field populated from function parameters"
        );
    }
}
