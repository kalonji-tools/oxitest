//! Pure-Rust import graph analysis for `--affected` test selection.
//!
//! Replaces `python/oxitest/_bridge/import_graph.py` by parsing Python source
//! files with `rustpython-parser` and extracting import statements entirely in
//! Rust — no PyO3 call needed.

use std::collections::HashSet;

use camino::Utf8Path;
use rustpython_parser::ast;

use crate::python_ast;

/// Extract all absolutely-imported module names (and their prefixes) from a
/// Python source file.
fn extract_imported_modules(path: &Utf8Path) -> HashSet<String> {
    let stmts = match python_ast::parse_file(path) {
        Some((_, stmts)) => stmts,
        None => return HashSet::new(),
    };

    let mut modules = HashSet::new();
    for stmt in &stmts {
        collect_imports(stmt, &mut modules);
    }
    expand_prefixes(modules)
}

/// Walk a statement (and recurse into compound bodies) to find Import/ImportFrom.
fn collect_imports(stmt: &ast::Stmt, modules: &mut HashSet<String>) {
    match stmt {
        ast::Stmt::Import(node) => {
            for alias in &node.names {
                modules.insert(alias.name.to_string());
            }
        }
        ast::Stmt::ImportFrom(node) => {
            // Only absolute imports (level == None or level == 0)
            let is_absolute = node.level.is_none_or(|l| l == 0u32);
            if is_absolute && let Some(ref module) = node.module {
                modules.insert(module.to_string());
            }
        }
        _ => {}
    }
    // Recurse into compound statement children
    for child in python_ast::compound_children(stmt) {
        collect_imports(child, modules);
    }
}

/// Expand each dotted module name to include all prefixes.
/// `"a.b.c"` → `{"a", "a.b", "a.b.c"}`
fn expand_prefixes(modules: HashSet<String>) -> HashSet<String> {
    let mut expanded = HashSet::new();
    for module in &modules {
        let parts: Vec<&str> = module.split('.').collect();
        for i in 1..=parts.len() {
            expanded.insert(parts[..i].join("."));
        }
    }
    expanded
}

/// Convert a relative file path to all possible dotted module name prefixes.
///
/// Mirrors `import_graph._file_to_modules()` in Python.
/// E.g. `"myapp/sub/foo.py"` → `["myapp.sub.foo", "myapp.sub", "myapp"]`
fn file_to_modules(rel_path: &str) -> Vec<String> {
    let p = std::path::Path::new(rel_path);
    let stem = p.with_extension("");
    let mut parts: Vec<&str> = stem.iter().filter_map(|s| s.to_str()).collect();

    // __init__.py represents the package itself
    if parts.last() == Some(&"__init__") {
        parts.pop();
    }

    if parts.is_empty() {
        return vec![];
    }

    (0..parts.len())
        .rev()
        .map(|i| parts[..=i].join("."))
        .collect()
}

/// Determine which test files are affected, with per-file diagnostics.
///
/// Like [`resolve_affected`], but returns an [`ImportAnalysis`] per test file
/// showing which imports matched changed sources.
pub(crate) fn resolve_affected_with_diagnostics(
    test_files: &[camino::Utf8PathBuf],
    changed_sources: &[String],
) -> Vec<crate::affected::ImportAnalysis> {
    let mut changed_modules = HashSet::new();
    for src in changed_sources {
        for m in file_to_modules(src) {
            changed_modules.insert(m);
        }
    }

    test_files
        .iter()
        .map(|test_file| {
            let imports = extract_imported_modules(test_file);
            let matched: Vec<String> = imports.intersection(&changed_modules).cloned().collect();
            crate::affected::ImportAnalysis {
                test_file: test_file.to_string(),
                affected: !matched.is_empty(),
                matched_imports: matched,
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::python_ast::tests::{temp_path, write_temp_py};

    /// Test helper: delegates to `resolve_affected_with_diagnostics` and
    /// returns only the affected file paths.
    fn resolve_affected(
        test_files: &[camino::Utf8PathBuf],
        changed_sources: &[String],
    ) -> Vec<camino::Utf8PathBuf> {
        resolve_affected_with_diagnostics(test_files, changed_sources)
            .into_iter()
            .filter(|a| a.affected)
            .map(|a| camino::Utf8PathBuf::from(a.test_file))
            .collect()
    }

    // ── extract_imported_modules ─────────────────────────────────────

    #[test]
    fn extract_simple_import() {
        let f = write_temp_py("import os\nimport sys\n");
        let modules = extract_imported_modules(&temp_path(&f));
        assert!(modules.contains("os"));
        assert!(modules.contains("sys"));
    }

    #[test]
    fn extract_dotted_import_with_prefixes() {
        let f = write_temp_py("import os.path\n");
        let modules = extract_imported_modules(&temp_path(&f));
        assert!(modules.contains("os"));
        assert!(modules.contains("os.path"));
    }

    #[test]
    fn extract_from_import_absolute() {
        let f = write_temp_py("from os.path import join\n");
        let modules = extract_imported_modules(&temp_path(&f));
        assert!(modules.contains("os"));
        assert!(modules.contains("os.path"));
    }

    #[test]
    fn extract_skips_relative_imports() {
        let f = write_temp_py("from . import sibling\nfrom ..parent import thing\n");
        let modules = extract_imported_modules(&temp_path(&f));
        assert!(modules.is_empty());
    }

    #[test]
    fn extract_syntax_error_returns_empty() {
        let f = write_temp_py("def broken(\n");
        let modules = extract_imported_modules(&temp_path(&f));
        assert!(modules.is_empty());
    }

    #[test]
    fn extract_nonexistent_file_returns_empty() {
        let modules = extract_imported_modules(Utf8Path::new("/nonexistent/file.py"));
        assert!(modules.is_empty());
    }

    #[test]
    fn extract_import_inside_function() {
        let f = write_temp_py("def foo():\n    import json\n");
        let modules = extract_imported_modules(&temp_path(&f));
        assert!(modules.contains("json"));
    }

    #[test]
    fn extract_import_inside_if() {
        let f = write_temp_py("if True:\n    import csv\n");
        let modules = extract_imported_modules(&temp_path(&f));
        assert!(modules.contains("csv"));
    }

    #[test]
    fn extract_import_inside_try() {
        let f =
            write_temp_py("try:\n    import fast_lib\nexcept ImportError:\n    import slow_lib\n");
        let modules = extract_imported_modules(&temp_path(&f));
        assert!(modules.contains("fast_lib"));
        assert!(modules.contains("slow_lib"));
    }

    #[test]
    fn extract_multiple_from_imports() {
        let f = write_temp_py(
            "from pathlib import Path\nfrom collections.abc import Sequence\nimport ast\n",
        );
        let modules = extract_imported_modules(&temp_path(&f));
        assert!(modules.contains("pathlib"));
        assert!(modules.contains("collections"));
        assert!(modules.contains("collections.abc"));
        assert!(modules.contains("ast"));
    }

    // ── file_to_modules ──────────────────────────────────────────────

    #[test]
    fn file_to_modules_simple() {
        let mods = file_to_modules("myapp/utils.py");
        assert_eq!(mods, vec!["myapp.utils", "myapp"]);
    }

    #[test]
    fn file_to_modules_deep() {
        let mods = file_to_modules("myapp/sub/foo.py");
        assert_eq!(mods, vec!["myapp.sub.foo", "myapp.sub", "myapp"]);
    }

    #[test]
    fn file_to_modules_init() {
        let mods = file_to_modules("myapp/__init__.py");
        assert_eq!(mods, vec!["myapp"]);
    }

    #[test]
    fn file_to_modules_single_file() {
        let mods = file_to_modules("setup.py");
        assert_eq!(mods, vec!["setup"]);
    }

    #[test]
    fn file_to_modules_unicode_path() {
        let mods = file_to_modules("utils/données.py");
        assert!(mods.contains(&"utils.données".to_string()));
        assert!(mods.contains(&"utils".to_string()));
    }

    // ── resolve_affected ─────────────────────────────────────────────

    #[test]
    fn resolve_affected_matches_import() {
        let f = write_temp_py("import myapp.utils\n");
        let test_paths = vec![temp_path(&f)];
        let changed = vec!["myapp/utils.py".to_string()];

        let affected = resolve_affected(&test_paths, &changed);
        assert_eq!(affected.len(), 1);
    }

    #[test]
    fn resolve_affected_no_match() {
        let f = write_temp_py("import unrelated\n");
        let test_paths = vec![temp_path(&f)];
        let changed = vec!["myapp/utils.py".to_string()];

        let affected = resolve_affected(&test_paths, &changed);
        assert!(affected.is_empty());
    }

    #[test]
    fn resolve_affected_prefix_match() {
        let f = write_temp_py("from myapp.sub.module import thing\n");
        let test_paths = vec![temp_path(&f)];
        let changed = vec!["myapp/sub/module.py".to_string()];

        let affected = resolve_affected(&test_paths, &changed);
        assert_eq!(affected.len(), 1);
    }

    #[test]
    fn resolve_affected_empty_changed() {
        let f = write_temp_py("import os\n");
        let test_paths = vec![temp_path(&f)];

        let affected = resolve_affected(&test_paths, &[]);
        assert!(affected.is_empty());
    }

    #[test]
    fn resolve_affected_with_diagnostics_reports_matches() {
        // Create two test files: one imports "myapp.utils", the other doesn't.
        let dir = tempfile::tempdir().unwrap();
        let dir_path = camino::Utf8Path::from_path(dir.path()).unwrap();

        let test_a = dir_path.join("test_a.py");
        std::fs::write(&test_a, "import myapp.utils\ndef test_a(): pass\n").unwrap();

        let test_b = dir_path.join("test_b.py");
        std::fs::write(&test_b, "import os\ndef test_b(): pass\n").unwrap();

        let test_files = vec![
            camino::Utf8PathBuf::from(test_a.as_str()),
            camino::Utf8PathBuf::from(test_b.as_str()),
        ];
        let changed = vec!["myapp/utils.py".to_string()];

        let results = resolve_affected_with_diagnostics(&test_files, &changed);
        assert_eq!(results.len(), 2, "should report on both test files");

        let a_result = results
            .iter()
            .find(|r| r.test_file.contains("test_a"))
            .unwrap();
        assert!(
            a_result.affected,
            "test_a imports myapp.utils, should be affected"
        );
        assert!(
            a_result
                .matched_imports
                .contains(&"myapp.utils".to_string()),
            "should report myapp.utils as matched import"
        );

        let b_result = results
            .iter()
            .find(|r| r.test_file.contains("test_b"))
            .unwrap();
        assert!(
            !b_result.affected,
            "test_b only imports os, should not be affected"
        );
        assert!(
            b_result.matched_imports.is_empty(),
            "test_b should have no matched imports"
        );
    }
}
