//! Coverage rule for the doctest subject set (wayfinder #1606).
//!
//! For each subject:
//! 1. Retrieve its docstring — directly for `LocalDefinition`, via the alias walker for
//!    `AliasImport`/`LocalAlias`.
//! 2. Check for `Examples:` header + at least one `>>>` block in the retrieved docstring.
//! 3. Emit `Diagnostic(context="doctest.coverage")` differentiated by gap type.

use std::sync::Arc;

use camino::Utf8PathBuf;

use crate::doctest::alias::{AliasError, ModuleRoot, resolve_alias};
use crate::doctest::scanner::parse_docstring_examples_v2;
use crate::doctest::subjects::{Subject, SubjectSource};
use crate::reporter::stats::{DiagnosticEntry, DiagnosticSeverity};
use crate::types::LineNo;

/// The two ways a subject can be uncovered — differentiates the diagnostic message per #1606.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum CoverageGap {
    /// No `Examples:` header found in the docstring (or no docstring at all).
    MissingHeader,
    /// Header present but no `>>>` block. The docstring has an empty Examples section.
    HeaderNoExamples,
}

/// Result of checking a single subject.
#[derive(Debug)]
pub(crate) enum CheckOutcome {
    /// Subject is fully covered — no diagnostic.
    Covered,
    /// Subject is uncovered — emit a diagnostic at (file, lineno).
    Gap {
        gap: CoverageGap,
        file: Utf8PathBuf,
        lineno: u32,
    },
    /// Alias walk failed — surface as a scanner-level diagnostic.
    WalkError { error: AliasError },
}

/// Check one subject; return an outcome describing whether it's covered or has a gap.
///
/// `direct_docstring` / `direct_lineno` / `direct_file` are what the orchestrator resolved
/// at the subject's own site — used only for `LocalDefinition` subjects. Aliased subjects
/// (AliasImport, LocalAlias) resolve via the walker and ignore these.
pub(crate) fn check_subject(
    root: &ModuleRoot,
    subject: &Subject,
    starting_module: &str,
    direct_docstring: Option<&str>,
    direct_lineno: u32,
    direct_file: Option<Utf8PathBuf>,
) -> CheckOutcome {
    let (docstring, file, lineno) = match &subject.source {
        SubjectSource::LocalDefinition => {
            let file = direct_file.unwrap_or_default();
            (direct_docstring.map(str::to_string), file, direct_lineno)
        }
        SubjectSource::AliasImport { .. } | SubjectSource::LocalAlias { .. } => {
            match resolve_alias(root, &subject.source, starting_module) {
                Ok(resolved) => (resolved.docstring, resolved.file, resolved.lineno),
                Err(AliasError::UnknownTerminus { .. }) => {
                    // Per wayfinder #1603, Call RHS / Name-to-non-def terminus is
                    // silently skipped in subject enumeration. The walker gets the
                    // same treatment: if the alias chain terminates on a shape
                    // that isn't a class or function, we cannot check a docstring
                    // — silently accept as "covered" (i.e., not a subject).
                    return CheckOutcome::Covered;
                }
                Err(error) => return CheckOutcome::WalkError { error },
            }
        }
        SubjectSource::CallRhs => {
            // Silent skip per #1603 — should not have reached the coverage check.
            return CheckOutcome::Covered;
        }
        SubjectSource::Unknown => {
            // Scanner-level "unknown export shape" — orchestrator emits its own diagnostic.
            return CheckOutcome::Covered;
        }
    };
    let (header_present, examples) = match docstring.as_deref() {
        Some(d) => parse_docstring_examples_v2(d),
        None => (false, vec![]),
    };
    let gap = match (header_present, examples.is_empty()) {
        (true, false) => return CheckOutcome::Covered,
        (false, _) => CoverageGap::MissingHeader,
        (true, true) => CoverageGap::HeaderNoExamples,
    };
    CheckOutcome::Gap { gap, file, lineno }
}

/// Construct a `DiagnosticEntry` for a coverage gap.
///
/// Message differentiation per #1606: MissingHeader vs HeaderNoExamples produce distinct
/// inline fix hints. Both use `context = "doctest.coverage"`.
pub(crate) fn diagnostic_for_gap(
    subject: &Subject,
    gap: &CoverageGap,
    file: &Utf8PathBuf,
    lineno: u32,
    severity: DiagnosticSeverity,
) -> DiagnosticEntry {
    let message = match gap {
        CoverageGap::MissingHeader => format!(
            "`{}` missing `Examples:` header — add an `Examples:` section with a `>>>` block",
            subject.public_id,
        ),
        CoverageGap::HeaderNoExamples => format!(
            "`{}` has `Examples:` header but no `>>>` block — add at least one runnable example",
            subject.public_id,
        ),
    };
    DiagnosticEntry {
        severity,
        context: Arc::from("doctest.coverage"),
        message,
        file: Some(file.clone()),
        lineno: Some(LineNo::new(lineno.max(1) as usize)),
    }
}

/// Full orchestration: scan → filter public modules → enumerate → dedup → check → emit.
///
/// - `files`: paths relative to `root.root`. Layer 1 (privacy) filter is applied per-file.
/// - `root`: filesystem root for `dotted_path_for` inversion and cross-file walks.
/// - `severity`: applied to every emitted diagnostic. Callers derive this from the
///   global `strict` mode: `Enforce` → `Warning`, `Abort` → `Error`.
pub(crate) fn run_coverage_check(
    files: &[Utf8PathBuf],
    root: &ModuleRoot,
    severity: DiagnosticSeverity,
) -> Vec<DiagnosticEntry> {
    use std::collections::HashMap;

    use crate::doctest::subjects::{
        dotted_path_for_file, enumerate_subjects, is_public_module_path,
    };

    // Parse each public file once; keep stmts + resolved file path + line index. The line
    // index is used to resolve LocalDefinition subjects' def line (fix for the audit
    // finding that LocalDefinition diagnostics previously all pointed at line 1).
    let mut per_module: HashMap<
        String,
        (Vec<rustpython_parser::ast::Stmt>, Utf8PathBuf, Vec<u32>),
    > = HashMap::new();
    let mut all_subjects: Vec<(String, Subject)> = Vec::new();

    for file in files {
        let dotted = dotted_path_for_file(file, &root.root);
        if !is_public_module_path(&dotted) {
            continue;
        }
        let full = root.root.join(file);
        let Some((src, stmts)) = crate::python_ast::parse_file(&full) else {
            continue;
        };
        let line_index = crate::python_ast::build_line_index(&src);
        for subj in enumerate_subjects(&stmts, &dotted) {
            all_subjects.push((dotted.clone(), subj));
        }
        per_module.insert(dotted, (stmts, full, line_index));
    }

    // Q1 dedup by identity to definition site (#1609 Q1): drop AliasImport subjects
    // whose target is also a LocalDefinition subject in this scan.
    let definition_sites: std::collections::HashSet<String> = all_subjects
        .iter()
        .filter(|(_, s)| matches!(s.source, SubjectSource::LocalDefinition))
        .map(|(_, s)| s.public_id.clone())
        .collect();
    let deduped: Vec<(String, Subject)> = all_subjects
        .into_iter()
        .filter(|(_, s)| match &s.source {
            SubjectSource::AliasImport {
                source_module,
                source_name,
            } => !definition_sites.contains(&format!("{source_module}.{source_name}")),
            _ => true,
        })
        .collect();

    let mut diagnostics = Vec::new();
    for (module_dotted, subject) in deduped {
        // Emit scanner-level "unknown export shape" for Unknown subjects up front.
        // We don't resolve a lineno for these (the Unknown RHS could be arbitrary);
        // point at line 1 as a fallback — reporter dedup groups by context anyway.
        if matches!(subject.source, SubjectSource::Unknown) {
            diagnostics.push(DiagnosticEntry {
                severity: severity.clone(),
                context: Arc::from("doctest.coverage.analysis"),
                message: format!(
                    "`{}` unknown export shape — cannot check coverage",
                    subject.public_id
                ),
                file: root.resolve(&module_dotted),
                lineno: Some(LineNo::new(1)),
            });
            continue;
        }

        let (direct_doc, direct_lineno, direct_file) = match &subject.source {
            SubjectSource::LocalDefinition => {
                let entry = per_module.get(&module_dotted);
                let (doc, lineno) = entry
                    .map(|(stmts, _, line_index)| {
                        let name = subject.public_id.rsplit('.').next().unwrap_or_default();
                        find_docstring_and_lineno_for_local_def(stmts, name, line_index)
                    })
                    .unwrap_or((None, 0));
                let file = entry.map(|(_, f, _)| f.clone());
                (doc, lineno, file)
            }
            _ => (None, 0, None),
        };

        match check_subject(
            root,
            &subject,
            &module_dotted,
            direct_doc.as_deref(),
            direct_lineno,
            direct_file,
        ) {
            CheckOutcome::Covered => {}
            CheckOutcome::Gap { gap, file, lineno } => {
                diagnostics.push(diagnostic_for_gap(
                    &subject,
                    &gap,
                    &file,
                    lineno,
                    severity.clone(),
                ));
            }
            CheckOutcome::WalkError { error } => {
                diagnostics.push(diagnostic_for_walk_error(
                    &subject,
                    &error,
                    root.resolve(&module_dotted),
                    severity.clone(),
                ));
            }
        }
    }
    diagnostics
}

/// Locate a class/function definition by name; return (docstring, 1-indexed lineno).
///
/// The line index is used to convert the AST `range` (a byte offset) into a lineno. If
/// the name isn't found, returns `(None, 0)` — coverage falls back to no docstring +
/// line 1 in the diagnostic. This shouldn't normally happen because the caller only
/// invokes this for LocalDefinition subjects (already classified from the same stmts).
fn find_docstring_and_lineno_for_local_def(
    stmts: &[rustpython_parser::ast::Stmt],
    name: &str,
    line_index: &[u32],
) -> (Option<String>, u32) {
    use rustpython_parser::ast::Stmt;
    for stmt in stmts {
        match stmt {
            Stmt::ClassDef(c) if c.name.as_str() == name => {
                let lineno =
                    crate::python_ast::offset_to_line(line_index, c.range.start().to_u32());
                let doc = crate::doctest::scanner::extract_docstring(&c.body).map(str::to_string);
                return (doc, lineno);
            }
            Stmt::FunctionDef(f)
                if f.name.as_str() == name && !crate::python_ast::is_stub_body(&f.body) =>
            {
                let lineno =
                    crate::python_ast::offset_to_line(line_index, f.range.start().to_u32());
                let doc = crate::doctest::scanner::extract_docstring(&f.body).map(str::to_string);
                return (doc, lineno);
            }
            Stmt::AsyncFunctionDef(f)
                if f.name.as_str() == name && !crate::python_ast::is_stub_body(&f.body) =>
            {
                let lineno =
                    crate::python_ast::offset_to_line(line_index, f.range.start().to_u32());
                let doc = crate::doctest::scanner::extract_docstring(&f.body).map(str::to_string);
                return (doc, lineno);
            }
            _ => {}
        }
    }
    (None, 0)
}

pub(crate) fn diagnostic_for_walk_error(
    subject: &Subject,
    error: &AliasError,
    file: Option<Utf8PathBuf>,
    severity: DiagnosticSeverity,
) -> DiagnosticEntry {
    let message = match error {
        AliasError::Cycle { path } => format!(
            "`{}` alias cycle detected: {} — cannot resolve source docstring",
            subject.public_id,
            path.join(" → "),
        ),
        AliasError::UnknownTerminus { at } => format!(
            "`{}` alias chain terminates on unknown shape at `{}` — cannot check coverage",
            subject.public_id, at,
        ),
        AliasError::ParseFailure { file: bad } => format!(
            "`{}` alias source file failed to parse: {} — cannot check coverage",
            subject.public_id, bad,
        ),
        AliasError::NameNotFound { module, name } => format!(
            "`{}` alias target `{}.{}` not defined in that module",
            subject.public_id, module, name,
        ),
        AliasError::ModuleFileNotFound { module } => format!(
            "`{}` alias target module `{}` file not found",
            subject.public_id, module,
        ),
    };
    DiagnosticEntry {
        severity,
        context: Arc::from("doctest.coverage.analysis"),
        message,
        file,
        // Walk failed before we could resolve the terminal def line — fall back to
        // line 1. The message identifies the subject by dotted path anyway.
        lineno: Some(LineNo::new(1)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_subject() -> Subject {
        Subject {
            public_id: "mypkg.foo".into(),
            source: SubjectSource::LocalDefinition,
        }
    }

    #[test]
    fn missing_header_produces_missing_header_message() {
        let subj = sample_subject();
        let file = Utf8PathBuf::from("mypkg/foo.py");
        let d = diagnostic_for_gap(
            &subj,
            &CoverageGap::MissingHeader,
            &file,
            42,
            DiagnosticSeverity::Warning,
        );
        assert!(
            d.message.contains("missing `Examples:` header"),
            "missing-header message names the gap explicitly, got: {}",
            d.message
        );
        assert!(
            d.message.contains("mypkg.foo"),
            "message includes the subject public_id so users can locate the offender"
        );
        assert_eq!(
            d.context.as_ref(),
            "doctest.coverage",
            "single context string per #1606 — reporter dedup groups on this"
        );
        assert_eq!(
            d.severity,
            DiagnosticSeverity::Warning,
            "severity is passed through, not hardcoded — orchestrator picks based on config"
        );
    }

    #[test]
    fn header_no_examples_produces_no_examples_message() {
        let subj = sample_subject();
        let file = Utf8PathBuf::from("mypkg/foo.py");
        let d = diagnostic_for_gap(
            &subj,
            &CoverageGap::HeaderNoExamples,
            &file,
            42,
            DiagnosticSeverity::Warning,
        );
        assert!(
            d.message.contains("no `>>>` block"),
            "header-no-examples message names the missing block, got: {}",
            d.message
        );
    }

    #[test]
    fn check_subject_local_def_with_full_coverage_returns_covered() {
        // LocalDefinition path doesn't hit the walker, so a fake ModuleRoot is fine.
        let root = ModuleRoot {
            root: Utf8PathBuf::from("/tmp"),
            use_gitignore: true,
        };
        let subj = sample_subject();
        let outcome = check_subject(
            &root,
            &subj,
            "mypkg",
            Some("Some doc.\n\nExamples:\n    >>> 1 + 1\n    2\n"),
            42,
            Some(Utf8PathBuf::from("mypkg/foo.py")),
        );
        assert!(
            matches!(outcome, CheckOutcome::Covered),
            "docstring with header + >>> ⇒ Covered"
        );
    }

    #[test]
    fn check_subject_local_def_missing_header_returns_gap() {
        let root = ModuleRoot {
            root: Utf8PathBuf::from("/tmp"),
            use_gitignore: true,
        };
        let subj = sample_subject();
        let outcome = check_subject(
            &root,
            &subj,
            "mypkg",
            Some("Just prose, no examples.\n"),
            42,
            Some(Utf8PathBuf::from("mypkg/foo.py")),
        );
        match outcome {
            CheckOutcome::Gap {
                gap: CoverageGap::MissingHeader,
                ..
            } => {}
            other => panic!(
                "docstring without `Examples:` header should classify as MissingHeader, got {:?}",
                other
            ),
        }
    }

    #[test]
    fn check_subject_local_def_header_no_examples_returns_gap() {
        let root = ModuleRoot {
            root: Utf8PathBuf::from("/tmp"),
            use_gitignore: true,
        };
        let subj = sample_subject();
        let outcome = check_subject(
            &root,
            &subj,
            "mypkg",
            Some("Prose.\n\nExamples:\n\nStill just prose.\n"),
            42,
            Some(Utf8PathBuf::from("mypkg/foo.py")),
        );
        match outcome {
            CheckOutcome::Gap {
                gap: CoverageGap::HeaderNoExamples,
                ..
            } => {}
            other => panic!(
                "docstring with `Examples:` header but no `>>>` block should classify as HeaderNoExamples, got {:?}",
                other
            ),
        }
    }

    #[test]
    fn check_subject_no_docstring_returns_missing_header() {
        let root = ModuleRoot {
            root: Utf8PathBuf::from("/tmp"),
            use_gitignore: true,
        };
        let subj = sample_subject();
        let outcome = check_subject(
            &root,
            &subj,
            "mypkg",
            None,
            42,
            Some(Utf8PathBuf::from("mypkg/foo.py")),
        );
        match outcome {
            CheckOutcome::Gap {
                gap: CoverageGap::MissingHeader,
                ..
            } => {}
            other => panic!(
                "no docstring at all is the strongest form of MissingHeader — the header can't exist without a docstring, got {:?}",
                other
            ),
        }
    }

    // ── run_coverage_check orchestrator ───────────────────────────────

    #[test]
    fn end_to_end_synthetic_module_missing_header_emits_warning() {
        use std::fs;
        use tempfile::tempdir;

        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        fs::create_dir_all(root.join("mypkg")).unwrap();
        fs::write(
            root.join("mypkg/__init__.py"),
            r#""""A public package."""

__all__ = ["foo"]

def foo():
    """No Examples section here — should trigger the missing-header diagnostic."""
    pass
"#,
        )
        .unwrap();

        let module_root = ModuleRoot {
            root,
            use_gitignore: true,
        };
        let files = vec![Utf8PathBuf::from("mypkg/__init__.py")];
        let diags = run_coverage_check(&files, &module_root, DiagnosticSeverity::Warning);
        assert_eq!(
            diags.len(),
            1,
            "one missing-header diagnostic expected, got {} diagnostics",
            diags.len()
        );
        let d = &diags[0];
        assert_eq!(d.severity, DiagnosticSeverity::Warning);
        assert_eq!(d.context.as_ref(), "doctest.coverage");
        assert!(
            d.message.contains("mypkg.foo"),
            "subject public_id in message, got: {}",
            d.message
        );
        assert!(
            d.message.contains("missing `Examples:` header"),
            "missing-header wording, got: {}",
            d.message
        );
    }

    #[test]
    fn end_to_end_covered_subject_no_diagnostic() {
        use std::fs;
        use tempfile::tempdir;

        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        fs::create_dir_all(root.join("mypkg")).unwrap();
        fs::write(
            root.join("mypkg/__init__.py"),
            r#""""A public package."""

__all__ = ["foo"]

def foo():
    """Covered!

    Examples:
        >>> 1 + 1
        2
    """
    pass
"#,
        )
        .unwrap();

        let module_root = ModuleRoot {
            root,
            use_gitignore: true,
        };
        let files = vec![Utf8PathBuf::from("mypkg/__init__.py")];
        let diags = run_coverage_check(&files, &module_root, DiagnosticSeverity::Warning);
        assert!(
            diags.is_empty(),
            "fully covered subject ⇒ no diagnostics, got: {:?}",
            diags
        );
    }

    #[test]
    fn end_to_end_private_module_skipped() {
        use std::fs;
        use tempfile::tempdir;

        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        fs::create_dir_all(root.join("mypkg/_private")).unwrap();
        fs::write(root.join("mypkg/__init__.py"), "").unwrap();
        fs::write(root.join("mypkg/_private/__init__.py"), "").unwrap();
        fs::write(
            root.join("mypkg/_private/thing.py"),
            r#"
def uncovered():
    """No examples."""
    pass
"#,
        )
        .unwrap();

        let module_root = ModuleRoot {
            root,
            use_gitignore: true,
        };
        let files = vec![
            Utf8PathBuf::from("mypkg/__init__.py"),
            Utf8PathBuf::from("mypkg/_private/__init__.py"),
            Utf8PathBuf::from("mypkg/_private/thing.py"),
        ];
        let diags = run_coverage_check(&files, &module_root, DiagnosticSeverity::Warning);
        assert!(
            diags.is_empty(),
            "underscore-prefixed module path ⇒ subjects not scanned (Layer 1 filter), got: {:?}",
            diags
        );
    }

    #[test]
    fn run_coverage_check_local_def_diagnostic_points_at_real_def_line() {
        use std::fs;
        use tempfile::tempdir;
        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        fs::create_dir_all(root.join("mypkg")).unwrap();
        // `foo` is on line 4 (after """docstring""", __all__, blank line).
        fs::write(
            root.join("mypkg/__init__.py"),
            "\"\"\"pkg\"\"\"\n__all__ = [\"foo\"]\n\ndef foo():\n    \"\"\"No examples.\"\"\"\n    pass\n",
        )
        .unwrap();
        let module_root = ModuleRoot {
            root,
            use_gitignore: true,
        };
        let files = vec![Utf8PathBuf::from("mypkg/__init__.py")];
        let diags = run_coverage_check(&files, &module_root, DiagnosticSeverity::Warning);
        assert_eq!(diags.len(), 1, "one missing-header diagnostic expected");
        let d = &diags[0];
        // Line 4 is where `def foo():` starts. This regression-tests the audit's
        // finding that LocalDefinition diagnostics used to always point at line 1.
        assert_eq!(
            d.lineno.map(|l| *l),
            Some(4),
            "LocalDefinition diagnostic must point at the real def line, not line 1"
        );
    }

    #[test]
    fn end_to_end_alias_cycle_emits_scanner_diagnostic() {
        use std::fs;
        use tempfile::tempdir;

        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        fs::create_dir_all(root.join("mypkg")).unwrap();
        fs::write(
            root.join("mypkg/__init__.py"),
            r#"
__all__ = ["A"]
A = B
B = A
"#,
        )
        .unwrap();

        let module_root = ModuleRoot {
            root,
            use_gitignore: true,
        };
        let files = vec![Utf8PathBuf::from("mypkg/__init__.py")];
        let diags = run_coverage_check(&files, &module_root, DiagnosticSeverity::Warning);
        assert!(!diags.is_empty(), "cycle should produce a diagnostic");
        let cycle_diag = diags
            .iter()
            .find(|d| d.message.contains("alias cycle"))
            .expect("expected an `alias cycle` diagnostic");
        assert_eq!(
            cycle_diag.context.as_ref(),
            "doctest.coverage.analysis",
            "walk errors emit under the analysis context — the ratchet must not treat them as coverage gaps"
        );
    }

    #[test]
    fn check_subject_alias_to_call_rhs_terminus_is_silently_covered() {
        // Subject that aliases into a Name that isn't a class/function — the
        // walker terminates on a Call RHS. Per wayfinder #1603, Call RHS is a
        // silent skip for subject enumeration; the walker must honor the same
        // rule for alias terminus.
        use std::fs;
        use tempfile::tempdir;
        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        fs::create_dir_all(root.join("mypkg")).unwrap();
        fs::write(
            root.join("mypkg/__init__.py"),
            "\"\"\"pkg.\"\"\"\n\n__all__ = [\"thing\"]\n\nfrom mypkg._internal import thing\n",
        )
        .unwrap();
        fs::write(
            root.join("mypkg/_internal.py"),
            "class _Thing:\n    pass\n\nthing = _Thing()\n",
        )
        .unwrap();

        let module_root = ModuleRoot {
            root: root.clone(),
            use_gitignore: true,
        };
        let files = vec![Utf8PathBuf::from("mypkg/__init__.py")];
        let diags = run_coverage_check(&files, &module_root, DiagnosticSeverity::Error);

        // Under the fix, the Call RHS terminus produces NO diagnostic — silent skip.
        // Both `mypkg.thing` (coverage subject) and any alias walk terminating on
        // `thing = _Thing()` should be silently accepted.
        assert!(
            diags.is_empty(),
            "alias terminating on Call RHS must be silently skipped — the walker cannot verify a docstring on an instance, and #1603 says Call RHS is not a coverage subject; got diags: {:?}",
            diags
                .iter()
                .map(|d| (d.context.as_ref(), &d.message))
                .collect::<Vec<_>>()
        );
    }
}
