//! Public-subject enumeration for the doctest coverage rule (wayfinder #1602).
//!
//! Layer 1: filter modules by dotted-path privacy (any `_`-prefixed component → private).
//! Layer 2 (later task): resolve names within a public module via `__all__` or fallback.

use camino::Utf8Path;
use rustpython_parser::ast;

/// True if every component of the dotted path is public (no `_` prefix).
///
/// A module whose dotted path contains any `_`-prefixed component is package-internal —
/// its `__all__` declaration is not a public API commitment. Never a coverage source.
pub(crate) fn is_public_module_path(dotted: &str) -> bool {
    dotted
        .split('.')
        .all(|component| !component.starts_with('_'))
}

/// Convert a filesystem path relative to a testpaths root into a Python dotted module path.
///
/// `oxitest/_bridge/_fixture_type.py` → `oxitest._bridge._fixture_type`.
/// `oxitest/__init__.py` → `oxitest`.
/// The caller must have already stripped any `python/` (or other `testpaths` root) prefix.
pub(crate) fn dotted_path_for(rel: &Utf8Path) -> String {
    let mut parts: Vec<String> = rel.components().map(|c| c.as_str().to_string()).collect();
    if let Some(last) = parts.last_mut()
        && let Some(stem) = last.strip_suffix(".py")
    {
        *last = stem.to_string();
    }
    if parts.last().is_some_and(|s| s == "__init__") {
        parts.pop();
    }
    parts.join(".")
}

/// Extract the string list from a top-level `__all__ = [...]` assignment, if present.
///
/// Returns `None` when the module has no `__all__` — the fallback code path is expected
/// to handle that case (Layer 2 falls back to non-underscore top-level names).
///
/// Non-string list elements are silently dropped. Real-world `__all__` is always a list
/// of string literals; the tolerance is defensive against malformed code, not intended
/// to support other shapes.
pub(crate) fn extract_dunder_all(stmts: &[ast::Stmt]) -> Option<Vec<String>> {
    for stmt in stmts {
        let ast::Stmt::Assign(assign) = stmt else {
            continue;
        };
        if assign.targets.len() != 1 {
            continue;
        }
        let ast::Expr::Name(name) = &assign.targets[0] else {
            continue;
        };
        if name.id.as_str() != "__all__" {
            continue;
        }
        let ast::Expr::List(list) = &*assign.value else {
            continue;
        };
        let names: Vec<String> = list
            .elts
            .iter()
            .filter_map(|e| match e {
                ast::Expr::Constant(ast::ExprConstant {
                    value: ast::Constant::Str(s),
                    ..
                }) => Some(s.clone()),
                _ => None,
            })
            .collect();
        return Some(names);
    }
    None
}

/// A public-facing subject the coverage rule may target.
#[derive(Debug, Clone)]
pub(crate) struct Subject {
    /// Public dotted path — used in diagnostics and (M2) the allowlist.
    pub(crate) public_id: String,
    /// Where the binding lives in AST terms.
    pub(crate) source: SubjectSource,
}

/// How a public-facing name is bound at the top level of its module.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum SubjectSource {
    /// `class Foo: ...` or `def foo(...)` defined in this module.
    ///
    /// The lineno is resolved at coverage-check time via the module's line index —
    /// see `run_coverage_check` in `coverage.rs`. The AST `range` on the classified
    /// statement is a byte offset, not a lineno.
    LocalDefinition,
    /// `from X.Y import Foo [as PublicName]` — re-export from a module.
    /// `source_name` is the name in the source module (original, not the local rename).
    AliasImport {
        source_module: String,
        source_name: String,
    },
    /// `Foo = something_in_this_module` — same-module alias.
    LocalAlias { source_name: String },
    /// `proxy = _Proxy()` — Call RHS; has no docstring surface. Silent-skip per #1603.
    CallRhs,
    /// Anything else — `x = 42`, `x = a + b`, etc. Scanner emits an "unknown export shape".
    Unknown,
}

/// Look up `name` at the module top level and classify how it's bound.
///
/// Returns `None` if the name isn't found (e.g., listed in `__all__` but never defined —
/// a broken module; a higher layer emits its own diagnostic).
///
/// `LocalDefinition` carries no lineno — `run_coverage_check` resolves the def line at
/// check time via the module's line index (the AST `range` is a byte offset, not a lineno).
pub(crate) fn classify_top_level_binding(stmts: &[ast::Stmt], name: &str) -> Option<SubjectSource> {
    for stmt in stmts {
        match stmt {
            ast::Stmt::ClassDef(cls) if cls.name.as_str() == name => {
                return Some(SubjectSource::LocalDefinition);
            }
            ast::Stmt::FunctionDef(f) if f.name.as_str() == name => {
                return Some(SubjectSource::LocalDefinition);
            }
            ast::Stmt::AsyncFunctionDef(f) if f.name.as_str() == name => {
                return Some(SubjectSource::LocalDefinition);
            }
            ast::Stmt::ImportFrom(imp) => {
                // Relative imports (`from . import X`, `from ..foo import Y`)
                // aren't supported by the M1 alias walker — the walker resolves
                // absolute dotted paths only. Skipping ensures we don't
                // misclassify a relative alias as absolute and walk into the
                // wrong module (or into an empty `source_module = ""`). M2 can
                // add proper relative-import resolution.
                if imp.level.is_some_and(|l| l != 0u32) {
                    continue;
                }
                for alias in &imp.names {
                    let bound = alias.asname.as_ref().unwrap_or(&alias.name);
                    if bound.as_str() == name {
                        let module = imp
                            .module
                            .as_ref()
                            .map(|m| m.as_str().to_string())
                            .unwrap_or_default();
                        return Some(SubjectSource::AliasImport {
                            source_module: module,
                            source_name: alias.name.as_str().to_string(),
                        });
                    }
                }
            }
            ast::Stmt::Assign(assign) => {
                if assign.targets.len() != 1 {
                    continue;
                }
                let ast::Expr::Name(target) = &assign.targets[0] else {
                    continue;
                };
                if target.id.as_str() != name {
                    continue;
                }
                return Some(classify_rhs(&assign.value));
            }
            _ => {}
        }
    }
    None
}

fn classify_rhs(rhs: &ast::Expr) -> SubjectSource {
    match rhs {
        ast::Expr::Name(n) => SubjectSource::LocalAlias {
            source_name: n.id.as_str().to_string(),
        },
        ast::Expr::Call(_) => SubjectSource::CallRhs,
        _ => SubjectSource::Unknown,
    }
}

/// Enumerate the coverage subjects for a public module.
///
/// Layer 2 of #1603:
/// - `__all__` present ⇒ authoritative; each entry becomes a subject regardless of whether
///   it's imported or defined here.
/// - `__all__` absent ⇒ fallback: non-underscore top-level names that are *defined here*
///   (ClassDef, FunctionDef, AsyncFunctionDef, or Assign whose LHS is a non-underscore Name).
///
/// Caller is responsible for having filtered the module through Layer 1 (`is_public_module_path`).
pub(crate) fn enumerate_subjects(stmts: &[ast::Stmt], module_dotted: &str) -> Vec<Subject> {
    let names: Vec<String> = match extract_dunder_all(stmts) {
        Some(explicit) => explicit,
        None => fallback_top_level_names(stmts),
    };
    names
        .into_iter()
        .filter_map(|name| {
            classify_top_level_binding(stmts, &name).map(|source| Subject {
                public_id: format!("{module_dotted}.{name}"),
                source,
            })
        })
        .collect()
}

/// Fallback name list when `__all__` is absent.
///
/// Includes: ClassDef, FunctionDef, AsyncFunctionDef with non-underscore names, plus
/// Assign statements whose single LHS Name is non-underscore. This mirrors #1603's
/// "defined here, not imported" filter — imports (`from X import Y`) are excluded.
fn fallback_top_level_names(stmts: &[ast::Stmt]) -> Vec<String> {
    let mut out = Vec::new();
    for stmt in stmts {
        match stmt {
            ast::Stmt::ClassDef(cls) if !cls.name.as_str().starts_with('_') => {
                out.push(cls.name.as_str().to_string());
            }
            ast::Stmt::FunctionDef(f) if !f.name.as_str().starts_with('_') => {
                out.push(f.name.as_str().to_string());
            }
            ast::Stmt::AsyncFunctionDef(f) if !f.name.as_str().starts_with('_') => {
                out.push(f.name.as_str().to_string());
            }
            ast::Stmt::Assign(assign) if assign.targets.len() == 1 => {
                if let ast::Expr::Name(target) = &assign.targets[0] {
                    let n = target.id.as_str();
                    if !n.starts_with('_') {
                        out.push(n.to_string());
                    }
                }
            }
            _ => {}
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use camino::Utf8PathBuf;
    use rustpython_parser::{Parse, ast};

    fn parse_stmts(src: &str) -> Vec<ast::Stmt> {
        ast::Suite::parse(src, "<test>").unwrap()
    }

    #[test]
    fn extract_dunder_all_returns_string_list_when_present() {
        let stmts = parse_stmts(
            r#"
__all__ = ["foo", "bar", "Baz"]
def foo(): pass
class Baz: pass
"#,
        );
        let names = extract_dunder_all(&stmts).expect("should find __all__");
        assert_eq!(
            names,
            vec!["foo".to_string(), "bar".to_string(), "Baz".to_string()],
            "returns entries in source order",
        );
    }

    #[test]
    fn extract_dunder_all_returns_none_when_missing() {
        let stmts = parse_stmts(
            r#"
def foo(): pass
class Baz: pass
"#,
        );
        assert!(
            extract_dunder_all(&stmts).is_none(),
            "no __all__ assignment ⇒ None (fallback path handles this)",
        );
    }

    #[test]
    fn extract_dunder_all_ignores_non_string_elements() {
        // Real code rarely does this, but graceful degradation matters.
        let stmts = parse_stmts(
            r#"
__all__ = ["foo", 42, "bar"]
"#,
        );
        let names = extract_dunder_all(&stmts).expect("still returns Some");
        assert_eq!(
            names,
            vec!["foo".to_string(), "bar".to_string()],
            "non-string entries silently dropped — scanner tolerates malformed __all__",
        );
    }

    #[test]
    fn public_module_paths_pass_the_filter() {
        assert!(is_public_module_path("oxitest"), "root package is public");
        assert!(
            is_public_module_path("oxitest.plugin"),
            "public sub-module is public"
        );
    }

    #[test]
    fn any_underscore_prefixed_component_is_private() {
        assert!(
            !is_public_module_path("oxitest._bridge"),
            "underscore-prefixed sub-module is private"
        );
        assert!(
            !is_public_module_path("oxitest._bridge._fixture_type"),
            "multiple underscore-prefixed components stay private"
        );
        assert!(
            !is_public_module_path("_leading_underscore"),
            "leading underscore on top-level component is private"
        );
        assert!(
            !is_public_module_path("a.b._c.d"),
            "any component being underscore-prefixed makes the whole path private"
        );
    }

    #[test]
    fn dotted_path_strips_py_and_init() {
        let rel = Utf8PathBuf::from("oxitest/__init__.py");
        assert_eq!(
            dotted_path_for(&rel),
            "oxitest",
            "__init__.py yields the parent package name"
        );

        let rel = Utf8PathBuf::from("oxitest/plugin.py");
        assert_eq!(
            dotted_path_for(&rel),
            "oxitest.plugin",
            "regular module: strip .py, no __init__ handling"
        );

        let rel = Utf8PathBuf::from("oxitest/_bridge/_fixture_type.py");
        assert_eq!(
            dotted_path_for(&rel),
            "oxitest._bridge._fixture_type",
            "nested private module: components preserved verbatim"
        );
    }

    #[test]
    fn classify_class_def_is_local_definition() {
        let stmts = parse_stmts("class Foo:\n    '''doc'''\n    pass\n");
        let src = classify_top_level_binding(&stmts, "Foo").expect("Foo exists");
        assert!(
            matches!(src, SubjectSource::LocalDefinition),
            "class definitions are the canonical local case"
        );
    }

    #[test]
    fn classify_function_def_is_local_definition() {
        let stmts = parse_stmts("def foo():\n    '''doc'''\n    pass\n");
        let src = classify_top_level_binding(&stmts, "foo").expect("foo exists");
        assert!(matches!(src, SubjectSource::LocalDefinition));
    }

    #[test]
    fn classify_async_function_def_is_local_definition() {
        let stmts = parse_stmts("async def foo():\n    '''doc'''\n    pass\n");
        let src = classify_top_level_binding(&stmts, "foo").expect("foo exists");
        assert!(
            matches!(src, SubjectSource::LocalDefinition),
            "async def is a valid public API surface too"
        );
    }

    #[test]
    fn classify_from_import_is_alias_import() {
        let stmts = parse_stmts("from a.b import Foo\n");
        let src = classify_top_level_binding(&stmts, "Foo").unwrap();
        match src {
            SubjectSource::AliasImport {
                source_module,
                source_name,
            } => {
                assert_eq!(source_module, "a.b");
                assert_eq!(source_name, "Foo");
            }
            other => panic!("expected AliasImport, got {:?}", other),
        }
    }

    #[test]
    fn classify_from_import_with_as_alias_uses_original_name_as_source() {
        let stmts = parse_stmts("from a.b import Foo as Foo\n");
        let src = classify_top_level_binding(&stmts, "Foo").unwrap();
        match src {
            SubjectSource::AliasImport {
                source_module,
                source_name,
            } => {
                assert_eq!(source_module, "a.b");
                assert_eq!(
                    source_name, "Foo",
                    "source_name is the imported name, not the local alias"
                );
            }
            other => panic!("expected AliasImport, got {:?}", other),
        }
    }

    #[test]
    fn classify_from_import_renamed_uses_original_name_as_source() {
        // `from a.b import RealName as PublicName` — Task 6 says exported name is PublicName,
        // and the source lives at a.b.RealName.
        let stmts = parse_stmts("from a.b import RealName as PublicName\n");
        let src = classify_top_level_binding(&stmts, "PublicName").unwrap();
        match src {
            SubjectSource::AliasImport {
                source_module,
                source_name,
            } => {
                assert_eq!(source_module, "a.b");
                assert_eq!(
                    source_name, "RealName",
                    "source_name is the original in the source module, regardless of local rename"
                );
            }
            other => panic!("expected AliasImport, got {:?}", other),
        }
    }

    #[test]
    fn classify_relative_import_is_skipped() {
        // Relative imports aren't classified as AliasImport in M1 — the walker
        // resolves absolute dotted paths only. Skipping them means the caller
        // sees the name as absent (fallback path). M2 can add proper
        // relative-import resolution.
        let stmts = parse_stmts("from . import Foo\n");
        assert!(
            classify_top_level_binding(&stmts, "Foo").is_none(),
            "relative `from . import` should skip classification in M1"
        );

        let stmts = parse_stmts("from .foo import Bar\n");
        assert!(
            classify_top_level_binding(&stmts, "Bar").is_none(),
            "relative `from .foo import` should skip classification in M1"
        );

        let stmts = parse_stmts("from ..evil import Zap\n");
        assert!(
            classify_top_level_binding(&stmts, "Zap").is_none(),
            "double-dot relative import should skip classification in M1"
        );
    }

    #[test]
    fn classify_name_rhs_is_local_alias() {
        let stmts = parse_stmts("Foo = _internal_foo\n_internal_foo = 42\n");
        let src = classify_top_level_binding(&stmts, "Foo").unwrap();
        match src {
            SubjectSource::LocalAlias { source_name } => {
                assert_eq!(
                    source_name, "_internal_foo",
                    "same-module alias — Task 9's walker resolves within this module"
                );
            }
            other => panic!("expected LocalAlias, got {:?}", other),
        }
    }

    #[test]
    fn classify_call_rhs_is_silent_skip() {
        let stmts = parse_stmts("proxy = _Proxy()\n");
        let src = classify_top_level_binding(&stmts, "proxy").unwrap();
        assert!(
            matches!(src, SubjectSource::CallRhs),
            "instance assignments have no docstring surface — silent skip per #1603"
        );
    }

    #[test]
    fn classify_unknown_shape_is_unknown() {
        let stmts = parse_stmts("x = 42\n");
        let src = classify_top_level_binding(&stmts, "x").unwrap();
        assert!(
            matches!(src, SubjectSource::Unknown),
            "raw literal has no docstring surface and doesn't fit re-export patterns"
        );
    }

    #[test]
    fn classify_missing_name_returns_none() {
        let stmts = parse_stmts("class Foo: pass\n");
        assert!(
            classify_top_level_binding(&stmts, "Bar").is_none(),
            "name not defined ⇒ None (Layer 2 skips it silently)"
        );
    }

    #[test]
    fn enumerate_subjects_uses_dunder_all_first() {
        let stmts = parse_stmts(
            r#"
__all__ = ["foo", "Bar"]
def foo(): pass
class Bar: pass
class NotExported: pass
"#,
        );
        let subjects = enumerate_subjects(&stmts, "oxitest");
        let ids: Vec<&str> = subjects.iter().map(|s| s.public_id.as_str()).collect();
        assert_eq!(
            ids,
            vec!["oxitest.foo", "oxitest.Bar"],
            "only __all__ entries become subjects when __all__ is present"
        );
    }

    #[test]
    fn enumerate_subjects_fallback_uses_non_underscore_top_level_defined_here() {
        let stmts = parse_stmts(
            r#"
from typing import Callable
def foo(): pass
class Bar: pass
def _private(): pass
"#,
        );
        let subjects = enumerate_subjects(&stmts, "oxitest.plugin");
        let ids: Vec<&str> = subjects.iter().map(|s| s.public_id.as_str()).collect();
        assert_eq!(
            ids,
            vec!["oxitest.plugin.foo", "oxitest.plugin.Bar"],
            "no __all__ ⇒ non-underscore, defined-here-only names — Callable is imported (excluded), _private starts with _ (excluded)"
        );
    }

    #[test]
    fn enumerate_subjects_dunder_all_overrides_defined_here_filter() {
        let stmts = parse_stmts(
            r#"
from a.b import Imported
__all__ = ["Imported"]
"#,
        );
        let subjects = enumerate_subjects(&stmts, "oxitest");
        let ids: Vec<&str> = subjects.iter().map(|s| s.public_id.as_str()).collect();
        assert_eq!(
            ids,
            vec!["oxitest.Imported"],
            "__all__ explicitly listing an import ⇒ subject even though it's imported"
        );
    }

    #[test]
    fn enumerate_subjects_local_alias_via_fallback() {
        // Fallback path should include `Foo = _something` (Name RHS) — a same-module re-export.
        // The LHS is non-underscore; the RHS shape (LocalAlias) is fine.
        let stmts = parse_stmts(
            r#"
class _Internal: pass
Foo = _Internal
"#,
        );
        let subjects = enumerate_subjects(&stmts, "oxitest");
        let ids: Vec<&str> = subjects.iter().map(|s| s.public_id.as_str()).collect();
        assert_eq!(
            ids,
            vec!["oxitest.Foo"],
            "fallback path includes non-underscore assigns; classifier tags LocalAlias"
        );
    }

    #[test]
    fn enumerate_subjects_empty_module_returns_empty() {
        let stmts = parse_stmts("");
        let subjects = enumerate_subjects(&stmts, "oxitest");
        assert!(subjects.is_empty(), "no statements ⇒ no subjects");
    }

    #[test]
    fn enumerate_subjects_all_underscore_names_returns_empty() {
        let stmts = parse_stmts(
            r#"
def _private(): pass
class _Private: pass
"#,
        );
        let subjects = enumerate_subjects(&stmts, "oxitest");
        assert!(
            subjects.is_empty(),
            "all names underscore-prefixed and no __all__ ⇒ no public subjects"
        );
    }
}
