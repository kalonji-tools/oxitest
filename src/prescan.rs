//! Fast Rust-side pre-scan of Python test files.
//!
//! Parses Python source with `rustpython-parser` to check whether a file
//! contains any `test_*` functions or `Test*` classes *before* calling into
//! Python. This allows the collection pipeline to skip files with no tests
//! entirely, avoiding an expensive Python import + exec.

use camino::Utf8Path;
use rustpython_parser::{ast, Parse};

/// Check whether a Python file contains any test functions.
///
/// Returns `Some(true)` if tests found, `Some(false)` if none found.
/// Returns `None` on read error or syntax error (caller should fall through
/// to Python collection, which handles these cases with proper diagnostics).
pub(crate) fn has_test_functions(path: &Utf8Path) -> Option<bool> {
    let source = std::fs::read_to_string(path.as_std_path()).ok()?;
    let stmts = ast::Suite::parse(&source, path.as_str()).ok()?;

    for stmt in &stmts {
        if is_test_function(stmt) {
            return Some(true);
        }
        if let ast::Stmt::ClassDef(cls) = stmt {
            if cls.name.starts_with("Test") && cls.body.iter().any(is_test_function) {
                return Some(true);
            }
        }
    }

    Some(false)
}

fn is_test_function(stmt: &ast::Stmt) -> bool {
    match stmt {
        ast::Stmt::FunctionDef(f) => f.name.starts_with("test_"),
        ast::Stmt::AsyncFunctionDef(f) => f.name.starts_with("test_"),
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use camino::Utf8PathBuf;
    use std::io::Write;

    fn write_temp_py(content: &str) -> tempfile::NamedTempFile {
        let mut f = tempfile::Builder::new().suffix(".py").tempfile().unwrap();
        f.write_all(content.as_bytes()).unwrap();
        f
    }

    fn temp_path(f: &tempfile::NamedTempFile) -> Utf8PathBuf {
        Utf8PathBuf::from_path_buf(f.path().to_path_buf()).unwrap()
    }

    #[test]
    fn empty_file() {
        let f = write_temp_py("");
        assert_eq!(has_test_functions(&temp_path(&f)), Some(false));
    }

    #[test]
    fn single_test_function() {
        let f = write_temp_py("def test_foo():\n    pass\n");
        assert_eq!(has_test_functions(&temp_path(&f)), Some(true));
    }

    #[test]
    fn multiple_test_functions() {
        let f = write_temp_py("def test_a(): pass\ndef test_b(): pass\ndef helper(): pass\n");
        assert_eq!(has_test_functions(&temp_path(&f)), Some(true));
    }

    #[test]
    fn async_test_function() {
        let f = write_temp_py("async def test_async(): pass\n");
        assert_eq!(has_test_functions(&temp_path(&f)), Some(true));
    }

    #[test]
    fn test_class_with_methods() {
        let f = write_temp_py(
            "class TestFoo:\n    def test_bar(self): pass\n    def test_baz(self): pass\n",
        );
        assert_eq!(has_test_functions(&temp_path(&f)), Some(true));
    }

    #[test]
    fn non_test_class_ignored() {
        let f = write_temp_py("class Helper:\n    def test_bar(self): pass\n");
        assert_eq!(has_test_functions(&temp_path(&f)), Some(false));
    }

    #[test]
    fn non_test_functions_ignored() {
        let f = write_temp_py("def helper(): pass\ndef setup(): pass\n");
        assert_eq!(has_test_functions(&temp_path(&f)), Some(false));
    }

    #[test]
    fn mixed_module_and_class() {
        let f = write_temp_py(
            "def test_top(): pass\nclass TestGroup:\n    def test_inner(self): pass\n",
        );
        assert_eq!(has_test_functions(&temp_path(&f)), Some(true));
    }

    #[test]
    fn syntax_error_returns_none() {
        let f = write_temp_py("def broken(\n");
        assert_eq!(has_test_functions(&temp_path(&f)), None);
    }

    #[test]
    fn nonexistent_file_returns_none() {
        assert_eq!(
            has_test_functions(Utf8Path::new("/nonexistent/file.py")),
            None
        );
    }

    #[test]
    fn file_with_only_imports() {
        let f = write_temp_py("import os\nfrom pathlib import Path\n");
        assert_eq!(has_test_functions(&temp_path(&f)), Some(false));
    }
}
