//! N-hop static alias walker for the coverage rule (wayfinder #1609).
//!
//! Given a Subject whose source is `AliasImport { source_module, source_name }` or
//! `LocalAlias { source_name }`, this module follows the chain across module files
//! until it hits a `ClassDef` or `FunctionDef`, and returns:
//! - the terminal file:line where the definition lives
//! - the terminal docstring (may be `None` — coverage rule handles that)
//!
//! Errors:
//! - Cycle detected → `AliasError::Cycle`
//! - Chain terminates on unknown shape (Call RHS, arithmetic, etc.) → `AliasError::UnknownTerminus`
//! - File can't be parsed → `AliasError::ParseFailure`
//! - Name not found in target module (parsed but binding absent) → `AliasError::NameNotFound`
//! - Module file not found on disk → `AliasError::ModuleFileNotFound`

use camino::Utf8PathBuf;

use crate::doctest::subjects::SubjectSource;

/// Root for resolving dotted module paths to filesystem paths.
///
/// Typically the project root joined with the first `testpaths` entry (often `python/`).
#[derive(Debug, Clone)]
pub(crate) struct ModuleRoot {
    pub(crate) root: Utf8PathBuf,
}

impl ModuleRoot {
    /// Look up a dotted module name as an `__init__.py` (package) or `<name>.py` (module).
    /// Returns None if neither exists.
    pub(crate) fn resolve(&self, dotted: &str) -> Option<Utf8PathBuf> {
        let mut candidate = self.root.clone();
        for component in dotted.split('.') {
            candidate.push(component);
        }
        let init = candidate.join("__init__.py");
        if init.exists() {
            return Some(init);
        }
        let module_file = candidate.with_extension("py");
        if module_file.exists() {
            return Some(module_file);
        }
        None
    }
}

#[derive(Debug)]
pub(crate) enum AliasError {
    Cycle { path: Vec<String> },
    UnknownTerminus { at: String },
    ParseFailure { file: Utf8PathBuf },
    NameNotFound { module: String, name: String },
    ModuleFileNotFound { module: String },
}

#[derive(Debug)]
pub(crate) struct Resolved {
    pub(crate) file: Utf8PathBuf,
    pub(crate) lineno: u32,
    pub(crate) docstring: Option<String>,
}

/// Follow the alias chain to its terminal ClassDef/FunctionDef; return (file, lineno, docstring).
pub(crate) fn resolve_alias(
    root: &ModuleRoot,
    source: &SubjectSource,
    starting_module: &str,
) -> Result<Resolved, AliasError> {
    let mut seen: Vec<(String, String)> = Vec::new();
    let (mut current_module, mut current_name) = match source {
        SubjectSource::AliasImport {
            source_module,
            source_name,
        } => (source_module.clone(), source_name.clone()),
        SubjectSource::LocalAlias { source_name } => {
            (starting_module.to_string(), source_name.clone())
        }
        SubjectSource::LocalDefinition => {
            // Caller shouldn't invoke resolve_alias for local defs.
            return Err(AliasError::UnknownTerminus {
                at: starting_module.to_string(),
            });
        }
        SubjectSource::CallRhs | SubjectSource::Unknown => {
            return Err(AliasError::UnknownTerminus {
                at: starting_module.to_string(),
            });
        }
    };

    loop {
        let key = (current_module.clone(), current_name.clone());
        if seen.iter().any(|k| k == &key) {
            let path: Vec<String> = seen.iter().map(|(m, n)| format!("{m}.{n}")).collect();
            return Err(AliasError::Cycle { path });
        }
        seen.push(key);
        let file = root
            .resolve(&current_module)
            .ok_or_else(|| AliasError::ModuleFileNotFound {
                module: current_module.clone(),
            })?;
        let (source_str, stmts) = crate::python_ast::parse_file(&file)
            .ok_or_else(|| AliasError::ParseFailure { file: file.clone() })?;
        let line_index = crate::python_ast::build_line_index(&source_str);
        let classified =
            crate::doctest::subjects::classify_top_level_binding(&stmts, &current_name)
                .ok_or_else(|| AliasError::NameNotFound {
                    module: current_module.clone(),
                    name: current_name.clone(),
                })?;
        match classified {
            SubjectSource::LocalDefinition => {
                let (lineno, docstring) =
                    find_def_line_and_docstring(&stmts, &current_name, &line_index);
                return Ok(Resolved {
                    file,
                    lineno,
                    docstring,
                });
            }
            SubjectSource::LocalAlias { source_name } => {
                current_name = source_name;
                // module stays the same
            }
            SubjectSource::AliasImport {
                source_module,
                source_name,
            } => {
                current_module = source_module;
                current_name = source_name;
            }
            SubjectSource::CallRhs | SubjectSource::Unknown => {
                return Err(AliasError::UnknownTerminus {
                    at: format!("{current_module}.{current_name}"),
                });
            }
        }
    }
}

/// Locate a class/function definition by name; return (1-indexed lineno, docstring text).
fn find_def_line_and_docstring(
    stmts: &[rustpython_parser::ast::Stmt],
    name: &str,
    line_index: &[u32],
) -> (u32, Option<String>) {
    use rustpython_parser::ast::Stmt;
    for stmt in stmts {
        match stmt {
            Stmt::ClassDef(cls) if cls.name.as_str() == name => {
                let lineno =
                    crate::python_ast::offset_to_line(line_index, cls.range.start().to_u32());
                let doc =
                    crate::doctest::scanner::extract_docstring(&cls.body).map(|s| s.to_string());
                return (lineno, doc);
            }
            Stmt::FunctionDef(f) if f.name.as_str() == name => {
                let lineno =
                    crate::python_ast::offset_to_line(line_index, f.range.start().to_u32());
                let doc =
                    crate::doctest::scanner::extract_docstring(&f.body).map(|s| s.to_string());
                return (lineno, doc);
            }
            Stmt::AsyncFunctionDef(f) if f.name.as_str() == name => {
                let lineno =
                    crate::python_ast::offset_to_line(line_index, f.range.start().to_u32());
                let doc =
                    crate::doctest::scanner::extract_docstring(&f.body).map(|s| s.to_string());
                return (lineno, doc);
            }
            _ => {}
        }
    }
    debug_assert!(
        false,
        "find_def_line_and_docstring: no ClassDef/FunctionDef/AsyncFunctionDef for {name} — classify_top_level_binding said LocalDefinition but this fn couldn't find it"
    );
    (0, None)
}

#[cfg(test)]
mod tests {
    use super::*;
    use camino::Utf8PathBuf;
    use std::fs;
    use tempfile::tempdir;

    fn make_root() -> (tempfile::TempDir, ModuleRoot) {
        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        (tmp, ModuleRoot { root })
    }

    #[test]
    fn single_hop_reaches_local_def_and_docstring() {
        let (_tmp, root) = make_root();
        fs::create_dir_all(root.root.join("mypkg/_bridge")).unwrap();
        fs::write(root.root.join("mypkg/__init__.py"), "").unwrap();
        fs::write(root.root.join("mypkg/_bridge/__init__.py"), "").unwrap();
        fs::write(
            root.root.join("mypkg/_bridge/_impl.py"),
            r#"
def approx():
    """Approx doc."""
    pass
"#,
        )
        .unwrap();
        let src = SubjectSource::AliasImport {
            source_module: "mypkg._bridge._impl".into(),
            source_name: "approx".into(),
        };
        let resolved = resolve_alias(&root, &src, "mypkg").unwrap();
        assert!(
            resolved.file.as_str().contains("_impl.py"),
            "resolved to source file, got {}",
            resolved.file
        );
        assert!(
            resolved
                .docstring
                .as_deref()
                .unwrap_or_default()
                .contains("Approx doc."),
            "docstring retrieved from source def"
        );
        assert!(
            resolved.lineno >= 1,
            "1-indexed lineno on source def, got {}",
            resolved.lineno
        );
    }

    #[test]
    fn multi_hop_follows_local_alias_to_class_def() {
        let (_tmp, root) = make_root();
        fs::create_dir_all(root.root.join("mypkg/_bridge")).unwrap();
        fs::write(root.root.join("mypkg/__init__.py"), "").unwrap();
        fs::write(root.root.join("mypkg/_bridge/__init__.py"), "").unwrap();
        fs::write(
            root.root.join("mypkg/_bridge/_ft.py"),
            r#"
class _FixtureType:
    """Fixture doc via private class."""
    pass

Fixture = _FixtureType
"#,
        )
        .unwrap();
        let src = SubjectSource::AliasImport {
            source_module: "mypkg._bridge._ft".into(),
            source_name: "Fixture".into(),
        };
        let resolved = resolve_alias(&root, &src, "mypkg").unwrap();
        assert!(
            resolved
                .docstring
                .as_deref()
                .unwrap_or_default()
                .contains("Fixture doc via private class."),
            "two-hop: Fixture → _FixtureType, docstring lives on _FixtureType"
        );
    }

    #[test]
    fn cycle_returns_cycle_error() {
        let (_tmp, root) = make_root();
        fs::create_dir_all(root.root.join("mypkg")).unwrap();
        fs::write(
            root.root.join("mypkg/__init__.py"),
            r#"
A = B
B = A
"#,
        )
        .unwrap();
        let src = SubjectSource::LocalAlias {
            source_name: "A".into(),
        };
        let err = resolve_alias(&root, &src, "mypkg").unwrap_err();
        match err {
            AliasError::Cycle { path } => {
                assert_eq!(
                    path,
                    vec!["mypkg.A".to_string(), "mypkg.B".to_string()],
                    "cycle path preserves walk order: start(A) → B, then A repeats"
                );
            }
            other => panic!(
                "A → B → A should surface as AliasError::Cycle with walk-ordered path, got {other:?}"
            ),
        }
    }

    #[test]
    fn unknown_terminus_returns_unknown_error() {
        let (_tmp, root) = make_root();
        fs::create_dir_all(root.root.join("mypkg")).unwrap();
        fs::write(
            root.root.join("mypkg/__init__.py"),
            r#"
X = 42
"#,
        )
        .unwrap();
        let src = SubjectSource::LocalAlias {
            source_name: "X".into(),
        };
        let err = resolve_alias(&root, &src, "mypkg").unwrap_err();
        assert!(
            matches!(err, AliasError::UnknownTerminus { .. }),
            "int literal terminus ⇒ UnknownTerminus per #1609 Q2 sub-2.5"
        );
    }

    #[test]
    fn docstring_less_terminus_returns_ok_with_none() {
        let (_tmp, root) = make_root();
        fs::create_dir_all(root.root.join("mypkg")).unwrap();
        fs::write(
            root.root.join("mypkg/__init__.py"),
            r#"
def foo():
    pass
"#,
        )
        .unwrap();
        let src = SubjectSource::AliasImport {
            source_module: "mypkg".into(),
            source_name: "foo".into(),
        };
        let resolved = resolve_alias(&root, &src, "mypkg").unwrap();
        assert!(
            resolved.docstring.is_none(),
            "def without docstring ⇒ Ok(Resolved {{ docstring: None }}); coverage rule handles missing-header"
        );
        assert!(resolved.lineno >= 1);
    }
}
