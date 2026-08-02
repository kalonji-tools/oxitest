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

use camino::{Utf8Path, Utf8PathBuf};
use ignore::WalkBuilder;

use crate::doctest::subjects::SubjectSource;

/// Root for resolving dotted module paths to filesystem paths.
///
/// Typically the project root joined with the first `testpaths` entry (often `python/`).
#[derive(Debug, Clone)]
pub(crate) struct ModuleRoot {
    pub(crate) root: Utf8PathBuf,
    pub(crate) use_gitignore: bool,
}

impl ModuleRoot {
    /// Look up a dotted module name as an `__init__.py` (package) or `<name>.py` (module).
    ///
    /// Tries the root directly first; falls back to trying each auto-detected
    /// source-root candidate as a prefix. A source-root candidate is a direct
    /// child directory of `root` that does NOT contain `__init__.py` (e.g.
    /// `python/` in a maturin-style project layout). Hidden (`.`-prefixed)
    /// directories are skipped to avoid false candidates like `.venv/`.
    ///
    /// Returns None if the module isn't found under any candidate.
    pub(crate) fn resolve(&self, dotted: &str) -> Option<Utf8PathBuf> {
        // Try the root directly first — packages that live at the top level.
        if let Some(path) = self.try_resolve_under(&self.root, dotted) {
            return Some(path);
        }
        // Fall back to source-root candidates (dirs at root without __init__.py).
        for candidate in self.source_root_candidates() {
            if let Some(path) = self.try_resolve_under(&candidate, dotted) {
                return Some(path);
            }
        }
        None
    }

    fn try_resolve_under(&self, base: &Utf8Path, dotted: &str) -> Option<Utf8PathBuf> {
        let mut candidate = base.to_owned();
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

    fn source_root_candidates(&self) -> Vec<Utf8PathBuf> {
        let mut out = Vec::new();
        let walker = WalkBuilder::new(&self.root)
            .follow_links(false)
            .hidden(false) // walk hidden dirs — match main collector convention
            .max_depth(Some(1)) // root + direct children only
            .git_ignore(self.use_gitignore)
            .git_global(false) // ~/.gitignore_global bypassed for reproducibility
            .git_exclude(false) // .git/info/exclude bypassed for reproducibility
            .build();
        for entry in walker.filter_map(|e| e.ok()) {
            // Skip the root entry itself (depth 0).
            if entry.depth() == 0 {
                continue;
            }
            // Only direct-child directories qualify as source-root candidates.
            let Some(file_type) = entry.file_type() else {
                continue;
            };
            if !file_type.is_dir() {
                continue;
            }
            let path = entry.path();
            let init = path.join("__init__.py");
            if init.exists() {
                // Directory has __init__.py — it's a package, not a source root.
                continue;
            }
            if let Ok(utf8) = Utf8PathBuf::from_path_buf(path.to_owned()) {
                out.push(utf8);
            }
        }
        out
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
            Stmt::FunctionDef(f)
                if f.name.as_str() == name && !crate::python_ast::is_stub_body(&f.body) =>
            {
                let lineno =
                    crate::python_ast::offset_to_line(line_index, f.range.start().to_u32());
                let doc =
                    crate::doctest::scanner::extract_docstring(&f.body).map(|s| s.to_string());
                return (lineno, doc);
            }
            Stmt::AsyncFunctionDef(f)
                if f.name.as_str() == name && !crate::python_ast::is_stub_body(&f.body) =>
            {
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

    #[test]
    fn module_root_resolve_finds_file_under_source_root_prefix() {
        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        // Layout: python/ (no __init__.py) → oxitest/ (has __init__.py) → _bridge/_errors.py
        fs::create_dir_all(root.join("python/oxitest/_bridge")).unwrap();
        fs::write(root.join("python/oxitest/__init__.py"), "").unwrap();
        fs::write(root.join("python/oxitest/_bridge/__init__.py"), "").unwrap();
        fs::write(root.join("python/oxitest/_bridge/_errors.py"), "").unwrap();
        let mr = ModuleRoot {
            root: root.clone(),
            use_gitignore: true,
        };
        let resolved = mr.resolve("oxitest._bridge._errors");
        assert_eq!(
            resolved,
            Some(root.join("python/oxitest/_bridge/_errors.py")),
            "resolve must try source-root candidates (dirs without __init__.py) as prefixes; got {resolved:?}"
        );
    }

    #[test]
    fn module_root_resolve_prefers_root_level_when_package_lives_at_root() {
        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        fs::create_dir_all(root.join("pkg")).unwrap();
        fs::write(root.join("pkg/__init__.py"), "").unwrap();
        fs::write(root.join("pkg/mod.py"), "").unwrap();
        let mr = ModuleRoot {
            root: root.clone(),
            use_gitignore: true,
        };
        let resolved = mr.resolve("pkg.mod");
        assert_eq!(
            resolved,
            Some(root.join("pkg/mod.py")),
            "resolve must still work when the package lives at the root itself (no source-root prefix)"
        );
    }

    #[test]
    fn module_root_resolve_returns_none_when_module_truly_missing() {
        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        let mr = ModuleRoot {
            root,
            use_gitignore: true,
        };
        let resolved = mr.resolve("nonexistent.module");
        assert_eq!(
            resolved, None,
            "genuine missing modules still return None so the walker can emit ModuleFileNotFound"
        );
    }

    #[test]
    fn module_root_resolve_source_root_detection_ignores_dot_dirs() {
        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        // .venv is a hidden dir that would falsely look like a source root without filtering
        fs::create_dir_all(root.join(".venv/lib")).unwrap();
        fs::create_dir_all(root.join("python/pkg")).unwrap();
        fs::write(root.join("python/pkg/__init__.py"), "").unwrap();
        fs::write(root.join("python/pkg/mod.py"), "").unwrap();
        let mr = ModuleRoot {
            root: root.clone(),
            use_gitignore: true,
        };
        let resolved = mr.resolve("pkg.mod");
        assert_eq!(
            resolved,
            Some(root.join("python/pkg/mod.py")),
            "dot-prefixed dirs must not be treated as source-root candidates"
        );
    }

    fn make_root() -> (tempfile::TempDir, ModuleRoot) {
        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        (
            tmp,
            ModuleRoot {
                root,
                use_gitignore: true,
            },
        )
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
    fn source_root_candidates_respect_gitignore_when_enabled() {
        use std::fs;
        use std::process::Command;
        use tempfile::tempdir;
        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        // Initialise a git repo so the ignore crate honours .gitignore
        // (require_git=true by default — same as the main collector).
        Command::new("git")
            .args(["init", "-q"])
            .current_dir(tmp.path())
            .status()
            .unwrap();
        // Real source-root candidate
        fs::create_dir(root.join("python")).unwrap();
        // Gitignored candidate — should not appear
        fs::create_dir(root.join("build")).unwrap();
        fs::write(root.join(".gitignore"), "build/\n").unwrap();

        let mr = ModuleRoot {
            root,
            use_gitignore: true,
        };
        let candidates = mr.source_root_candidates();
        let candidate_names: Vec<String> = candidates
            .iter()
            .filter_map(|p| p.file_name().map(str::to_owned))
            .collect();
        assert!(
            candidate_names.contains(&"python".to_owned()),
            "non-gitignored directory must be a source-root candidate; got: {candidate_names:?}"
        );
        assert!(
            !candidate_names.contains(&"build".to_owned()),
            "gitignored directory must NOT be a source-root candidate; got: {candidate_names:?}"
        );
    }

    #[test]
    fn source_root_candidates_ignore_gitignore_when_disabled() {
        use std::fs;
        use std::process::Command;
        use tempfile::tempdir;
        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        // Initialise a git repo so the ignore crate would normally see .gitignore.
        Command::new("git")
            .args(["init", "-q"])
            .current_dir(tmp.path())
            .status()
            .unwrap();
        fs::create_dir(root.join("build")).unwrap();
        fs::write(root.join(".gitignore"), "build/\n").unwrap();

        let mr = ModuleRoot {
            root,
            use_gitignore: false,
        };
        let candidates = mr.source_root_candidates();
        let candidate_names: Vec<String> = candidates
            .iter()
            .filter_map(|p| p.file_name().map(str::to_owned))
            .collect();
        assert!(
            candidate_names.contains(&"build".to_owned()),
            "with gitignore disabled, gitignored dirs still qualify (user opted out of the filter); got: {candidate_names:?}"
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
