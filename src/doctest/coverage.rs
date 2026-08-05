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
pub enum CoverageGap {
    /// No `Examples:` header found in the docstring (or no docstring at all).
    MissingHeader,
    /// Header present but no `>>>` block. The docstring has an empty Examples section.
    HeaderNoExamples,
}

/// Result of checking a single subject.
#[derive(Debug)]
pub enum CheckOutcome {
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
/// (`AliasImport`, `LocalAlias`) resolve via the walker and ignore these.
pub fn check_subject(
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
/// Message differentiation per #1606: `MissingHeader` vs `HeaderNoExamples` produce distinct
/// inline fix hints. Both use `context = "doctest.coverage"`.
pub fn diagnostic_for_gap(
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

/// Match a single `ScopeEntry` against a `Subject`.
///
/// Public/private-name filtering is a separate axis handled by the caller.
fn subject_matches(
    entry: &crate::config::ScopeEntry,
    s: &crate::doctest::subjects::Subject,
) -> bool {
    use crate::config::ScopeEntry;
    match entry {
        ScopeEntry::Prefix(p) => s.file.starts_with(p),
        ScopeEntry::File(f) => s.file == *f,
        ScopeEntry::Symbol { file, name } => s.file == *file && s.name == *name,
        ScopeEntry::Member { file, cls, name } => {
            s.file == *file && s.class_context.as_deref() == Some(cls.as_str()) && s.name == *name
        }
    }
}

/// `(scope_matched, skip_matched)` pair of `Vec<bool>` indexed by entry
/// position — surfaced by `filter_subjects_by_scope` for stale-entry detection.
/// `scope_matched` is empty under `Public` scope.
pub type ScopeMatchBits = (Vec<bool>, Vec<bool>);

/// Per-file outcome of the scan's parse step — the three-state scanned set (#1800).
///
/// A file offered to `run_coverage_check` ends up in exactly one of three
/// states: `parsed` (opened and read — full evidence about its symbols),
/// `parse_failed` (the scan tried to read it and could not), or **neither**
/// (the scan never attempted the read — pruned upstream or withheld by the
/// scanner's privacy gate). Entry classification treats the three
/// differently: parsed files ground staleness verdicts, parse-failed files
/// ground a parse-failure report, and unattempted files force abstention.
#[derive(Debug, Default)]
pub struct ScannedFiles {
    /// Files opened and parsed successfully, relative to the module root.
    pub(crate) parsed: Vec<Utf8PathBuf>,
    /// Files the scan attempted to read but could not parse (syntax or I/O
    /// error), relative to the module root.
    pub(crate) parse_failed: Vec<Utf8PathBuf>,
}

/// Filter subjects against scope + skip, applying the public/private name
/// filter only under scalar `Public` scope (list-form entries are explicit opt-in).
///
/// Returns `(kept_subjects, match_bits)`. `match_bits` is threaded out for
/// stale-entry detection at the caller.
///
/// The `scope: &Option<DoctestScope>` mirrors `config.doctest.scope` — `None`
/// (or absent doctest table) defaults to Public behavior.
#[allow(
    clippy::type_complexity,
    reason = "tuple return is local — introducing a struct would add a struct definition + two field renames at each callsite for one function's use"
)]
pub fn filter_subjects_by_scope(
    subjects: Vec<(String, crate::doctest::subjects::Subject)>,
    scope: &Option<crate::config::DoctestScope>,
    skip: &[crate::config::ScopeEntry],
) -> (
    Vec<(String, crate::doctest::subjects::Subject)>,
    ScopeMatchBits,
) {
    use crate::config::DoctestScope;

    let (scope_entries, apply_private_filter): (&[crate::config::ScopeEntry], bool) = match scope {
        None | Some(DoctestScope::Public) => (&[], true),
        Some(DoctestScope::List(entries)) => (entries.as_slice(), false),
    };

    let mut scope_hits = vec![false; scope_entries.len()];
    let mut skip_hits = vec![false; skip.len()];
    let mut kept = Vec::new();

    for (module_dotted, subj) in subjects {
        // Private-name filter (scalar Public only): drop _-prefixed leaf names.
        if apply_private_filter && subj.name.starts_with('_') {
            continue;
        }

        // Under List: subject must match ≥1 scope entry.
        if !scope_entries.is_empty() {
            let mut any_scope_match = false;
            for (i, e) in scope_entries.iter().enumerate() {
                if subject_matches(e, &subj) {
                    scope_hits[i] = true;
                    any_scope_match = true;
                }
            }
            if !any_scope_match {
                continue;
            }
        }

        // Skip subtracts (any form, any list).
        let mut subtracted = false;
        for (i, e) in skip.iter().enumerate() {
            if subject_matches(e, &subj) {
                skip_hits[i] = true;
                subtracted = true;
                // don't break — track matches on other skip entries too
            }
        }
        if subtracted {
            continue;
        }

        kept.push((module_dotted, subj));
    }

    (kept, (scope_hits, skip_hits))
}

/// Full orchestration: scan → filter public modules → enumerate → dedup → check → emit.
///
/// - `files`: paths relative to `root.root`. Layer 1 (privacy) filter is applied per-file
///   unless the file is explicitly named by a `List`-form scope entry.
/// - `root`: filesystem root for `dotted_path_for` inversion and cross-file walks.
/// - `severity`: applied to every emitted diagnostic. Callers derive this from the
///   global `strict` mode: `Enforce` → `Warning`, `Abort` → `Error`.
/// - `scope`: user-configured `[tool.oxitest.doctest].scope` (or `None`/`Public`).
///   Under `List`, files explicitly named bypass the private-path gate; the subject
///   filter then applies list semantics without dropping `_`-prefixed leaves.
/// - `skip`: user-configured `[tool.oxitest.doctest].skip` entries. Applied at the
///   subject level via `filter_subjects_by_scope`.
///
/// Returns `(diagnostics, match_bits, scanned)`. `scanned` is the three-state
/// scanned set (#1800): `scanned.parsed` is the subset of `files` this scan
/// actually opened and parsed — a file the scan never attempted carries no
/// evidence about the symbols inside it, so callers that judge sub-file
/// entries must gate on `scanned`, never on `files`; see `StalenessInputs` in
/// `pipeline::collection` and #1796. A file the scan attempted but could not
/// parse lands in `scanned.parse_failed` and produces its own
/// `doctest.coverage.parse-error` diagnostic here.
pub fn run_coverage_check(
    files: &[Utf8PathBuf],
    root: &ModuleRoot,
    severity: DiagnosticSeverity,
    scope: &Option<crate::config::DoctestScope>,
    skip: &[crate::config::ScopeEntry],
) -> (Vec<DiagnosticEntry>, ScopeMatchBits, ScannedFiles) {
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
    // The three-state scanned set (#1800). Threaded out so staleness verdicts
    // about symbols *inside* a file are only reached for files the scan can
    // speak to, and so entries into parse-failed files report instead of
    // abstaining.
    let mut scanned = ScannedFiles::default();
    let mut diagnostics = Vec::new();

    for file in files {
        let dotted = dotted_path_for_file(file, &root.root);
        let module_is_public = is_public_module_path(&dotted);
        // Under List scope, allow explicitly-named private files into the scanner.
        // Explicit opt-in beats the blanket privacy gate — a user asking for
        // `pkg/_internal/mod.py::_helper` expects to actually check it.
        let file_explicitly_scoped = match scope {
            Some(crate::config::DoctestScope::List(entries)) => {
                crate::pipeline::collection::file_could_match(file, entries)
            }
            _ => false,
        };
        if !module_is_public && !file_explicitly_scoped {
            continue;
        }
        let full = root.root.join(file);
        let Some((src, stmts)) = crate::python_ast::parse_file(&full) else {
            // The scan asked for this file and could not read it — that is a
            // finding, not a silent drop (#1800). Everything pruned before
            // this point (norecursedirs, python_files, conftest.py, the
            // List-scope prescreen, the privacy gate above) was never asked
            // for and stays silent. `parse_file` discards the concrete error,
            // so the message names both possible causes.
            scanned.parse_failed.push(file.clone());
            diagnostics.push(DiagnosticEntry {
                severity: severity.clone(),
                context: Arc::from("doctest.coverage.parse-error"),
                message: format!(
                    "`{file}` could not be parsed (syntax error or I/O error) — \
                     its definitions are invisible to doctest coverage"
                ),
                file: Some(file.clone()),
                lineno: Some(LineNo::new(1)),
            });
            continue;
        };
        scanned.parsed.push(file.clone());
        let line_index = crate::python_ast::build_line_index(&src);
        for subj in enumerate_subjects(&stmts, &dotted, file) {
            all_subjects.push((dotted.clone(), subj));
        }
        per_module.insert(dotted, (stmts, full, line_index));
    }

    // Scope-driven member seeding (#1644): for each `ScopeEntry::Member` entry
    // that points at a real class + method in an already-parsed module, seed
    // one method subject. Missing method → no subject, so the stale-entry
    // diagnostic (via `match_bits`) surfaces the typo.
    if let Some(crate::config::DoctestScope::List(entries)) = scope {
        for entry in entries {
            let crate::config::ScopeEntry::Member { file, cls, name } = entry else {
                continue;
            };
            let dotted = dotted_path_for_file(file, &root.root);
            let Some((stmts, _full, _line_index)) = per_module.get(&dotted) else {
                continue;
            };
            if find_class_method(stmts, cls, name).is_none() {
                continue;
            }
            all_subjects.push((
                dotted.clone(),
                Subject {
                    public_id: format!("{dotted}.{cls}.{name}"),
                    name: name.clone(),
                    source: SubjectSource::LocalDefinition,
                    file: file.clone(),
                    class_context: Some(cls.clone()),
                },
            ));
        }
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

    // Subject-level scope + skip filter. `match_bits` is threaded out to the
    // caller so `collect_coverage_diagnostics` can emit stale-entry diagnostics
    // for scope/skip entries that matched zero subjects (Task 9).
    let (deduped, match_bits) = filter_subjects_by_scope(deduped, scope, skip);

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
                    .map(|(stmts, _, line_index)| match &subject.class_context {
                        Some(cls) => find_class_method(stmts, cls, &subject.name)
                            .map(|t| docstring_and_lineno_from(t, line_index))
                            .unwrap_or((None, 0)),
                        None => {
                            let name = subject.public_id.rsplit('.').next().unwrap_or_default();
                            find_docstring_and_lineno_for_local_def(stmts, name, line_index)
                        }
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
    (diagnostics, match_bits, scanned)
}

/// A def-like statement located by name — the shape unified across the
/// top-level lookup and the `Cls::method` member lookup paths.
///
/// Unifies the three AST shapes coverage checks care about (class, def,
/// async def) so `find_def_by_name` / `find_class_method` can share one
/// walk primitive and one docstring extractor.
enum DefTarget<'a> {
    Class(&'a rustpython_parser::ast::StmtClassDef),
    Function(&'a rustpython_parser::ast::StmtFunctionDef),
    AsyncFunction(&'a rustpython_parser::ast::StmtAsyncFunctionDef),
}

/// Locate the first non-stub `class`/`def`/`async def` by name in a stmt
/// block.
///
/// First-match wins after skipping ellipsis-body function stubs (see
/// [`crate::python_ast::is_stub_body`]) so the walker naturally advances
/// past `@overload` chains to the real implementation — historical
/// behavior preserved from the pre-refactor top-level helper. The same
/// filter applies to class-body walks used by `find_class_method`, which
/// makes an ellipsis-body abstract method surface as a stale-entry
/// diagnostic rather than a false-positive `MissingHeader`.
fn find_def_by_name<'a>(
    stmts: &'a [rustpython_parser::ast::Stmt],
    name: &str,
) -> Option<DefTarget<'a>> {
    use rustpython_parser::ast::Stmt;
    for stmt in stmts {
        match stmt {
            Stmt::ClassDef(c) if c.name.as_str() == name => return Some(DefTarget::Class(c)),
            Stmt::FunctionDef(f)
                if f.name.as_str() == name && !crate::python_ast::is_stub_body(&f.body) =>
            {
                return Some(DefTarget::Function(f));
            }
            Stmt::AsyncFunctionDef(f)
                if f.name.as_str() == name && !crate::python_ast::is_stub_body(&f.body) =>
            {
                return Some(DefTarget::AsyncFunction(f));
            }
            _ => {}
        }
    }
    None
}

/// Extract `(docstring, 1-indexed lineno)` from a def target.
///
/// No stub check — `find_def_by_name` filters ellipsis-body stubs at
/// lookup, so any `DefTarget` reaching this helper is guaranteed to have
/// meaningful body content.
fn docstring_and_lineno_from(target: DefTarget<'_>, line_index: &[u32]) -> (Option<String>, u32) {
    match target {
        DefTarget::Class(c) => {
            let lineno = crate::python_ast::offset_to_line(line_index, c.range.start().to_u32());
            let doc = crate::doctest::scanner::extract_docstring(&c.body).map(str::to_string);
            (doc, lineno)
        }
        DefTarget::Function(f) => {
            let lineno = crate::python_ast::offset_to_line(line_index, f.range.start().to_u32());
            let doc = crate::doctest::scanner::extract_docstring(&f.body).map(str::to_string);
            (doc, lineno)
        }
        DefTarget::AsyncFunction(f) => {
            let lineno = crate::python_ast::offset_to_line(line_index, f.range.start().to_u32());
            let doc = crate::doctest::scanner::extract_docstring(&f.body).map(str::to_string);
            (doc, lineno)
        }
    }
}

/// Thin wrapper preserving the historical name — resolves docstring +
/// lineno for a top-level `LocalDefinition` subject.
fn find_docstring_and_lineno_for_local_def(
    stmts: &[rustpython_parser::ast::Stmt],
    name: &str,
    line_index: &[u32],
) -> (Option<String>, u32) {
    find_def_by_name(stmts, name)
        .map(|t| docstring_and_lineno_from(t, line_index))
        .unwrap_or((None, 0))
}

/// Locate a `def method(...)` / `async def method(...)` inside a top-level
/// class body, by class name and method name.
///
/// Returns `None` if the class doesn't exist, or if the class body has no
/// def by that name (a nested class or other stmt matching the name is
/// explicitly rejected — we only want callable methods). The `Member`
/// seeding path uses `.is_some()` for existence and the doc-resolution
/// path passes the returned `DefTarget` to `docstring_and_lineno_from`.
fn find_class_method<'a>(
    stmts: &'a [rustpython_parser::ast::Stmt],
    class_name: &str,
    method_name: &str,
) -> Option<DefTarget<'a>> {
    let DefTarget::Class(class_stmt) = find_def_by_name(stmts, class_name)? else {
        return None;
    };
    let target = find_def_by_name(&class_stmt.body, method_name)?;
    matches!(target, DefTarget::Function(_) | DefTarget::AsyncFunction(_)).then_some(target)
}

pub fn diagnostic_for_walk_error(
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
            name: "foo".into(),
            source: SubjectSource::LocalDefinition,
            file: Utf8PathBuf::from("mypkg/foo.py"),
            class_context: None,
        }
    }

    fn parse_stmts(src: &str) -> Vec<rustpython_parser::ast::Stmt> {
        use rustpython_parser::{Parse, ast};
        ast::Suite::parse(src, "<test>").unwrap()
    }

    // ── Unified DefTarget helper tests (#1644) ────────────────────────────

    #[test]
    fn find_def_by_name_returns_none_when_no_matching_def() {
        let stmts = parse_stmts("def foo(): pass\n");
        assert!(
            find_def_by_name(&stmts, "Missing").is_none(),
            "primitive returns None when no class/def/async-def matches — callers (top-level and Member) treat this as the not-found signal",
        );
    }

    #[test]
    fn find_def_by_name_finds_class() {
        let stmts = parse_stmts("class Foo:\n    pass\n");
        assert!(
            matches!(find_def_by_name(&stmts, "Foo"), Some(DefTarget::Class(_))),
            "top-level class must be locatable and classified as Class so `find_class_method` can then walk its body",
        );
    }

    #[test]
    fn find_def_by_name_finds_sync_function() {
        let stmts = parse_stmts("def foo(): pass\n");
        assert!(
            matches!(
                find_def_by_name(&stmts, "foo"),
                Some(DefTarget::Function(_))
            ),
            "sync def must be classified as Function so the extractor can apply the is_stub guard",
        );
    }

    #[test]
    fn find_def_by_name_finds_async_function() {
        let stmts = parse_stmts("async def afoo(): pass\n");
        assert!(
            matches!(
                find_def_by_name(&stmts, "afoo"),
                Some(DefTarget::AsyncFunction(_))
            ),
            "async def must be classified as AsyncFunction so the extractor can apply the is_stub guard on its own body",
        );
    }

    #[test]
    fn find_class_method_returns_none_when_class_absent() {
        let stmts = parse_stmts("def foo(): pass\n");
        assert!(
            find_class_method(&stmts, "Missing", "method").is_none(),
            "class not found means None so the Member seeding path drops the entry and the stale-entry diagnostic surfaces the typo",
        );
    }

    #[test]
    fn find_class_method_returns_none_when_method_absent() {
        let stmts = parse_stmts("class Cls:\n    def other(self): pass\n");
        assert!(
            find_class_method(&stmts, "Cls", "missing").is_none(),
            "class present but no method by that name means None — same stale-entry path as class-absent",
        );
    }

    #[test]
    fn find_class_method_finds_sync_method() {
        let stmts = parse_stmts("class Cls:\n    def method(self): pass\n");
        assert!(
            matches!(
                find_class_method(&stmts, "Cls", "method"),
                Some(DefTarget::Function(_))
            ),
            "sync def inside class body must be found so Member seeding produces a subject for it",
        );
    }

    #[test]
    fn find_class_method_finds_async_method() {
        let stmts = parse_stmts("class Cls:\n    async def amethod(self): pass\n");
        assert!(
            matches!(
                find_class_method(&stmts, "Cls", "amethod"),
                Some(DefTarget::AsyncFunction(_))
            ),
            "async def inside class body counts as a method — the two AST shapes are equivalent for coverage purposes",
        );
    }

    #[test]
    fn find_class_method_rejects_nested_class_named_like_method() {
        let stmts = parse_stmts("class Cls:\n    class inner:\n        pass\n");
        assert!(
            find_class_method(&stmts, "Cls", "inner").is_none(),
            "a nested class matching the method name must NOT be returned — Member scope entries target callables, not nested classes",
        );
    }

    #[test]
    fn docstring_and_lineno_from_function_returns_docstring() {
        let src = "def foo():\n    '''doc'''\n    pass\n";
        let stmts = parse_stmts(src);
        let line_index = crate::python_ast::build_line_index(src);
        let target = find_def_by_name(&stmts, "foo").unwrap();
        let (doc, lineno) = docstring_and_lineno_from(target, &line_index);
        assert_eq!(
            doc.as_deref(),
            Some("doc"),
            "function's own docstring must be extracted so the coverage check has something to grade against the Examples: contract",
        );
        assert_eq!(
            lineno, 1,
            "lineno must point at the def line so the diagnostic anchors on the source location the user is editing",
        );
    }

    #[test]
    fn find_def_by_name_skips_ellipsis_stub_function() {
        // Ellipsis body (`def foo(): ...`) is a stub per `is_stub_body` —
        // `find_def_by_name` skips it so `@overload` chains resolve to the
        // real implementation instead of the first `@overload`'d stub.
        let stmts = parse_stmts("def foo(): ...\n");
        assert!(
            find_def_by_name(&stmts, "foo").is_none(),
            "ellipsis-only bodies must be filtered at lookup so @overload chains don't emit false-positive MissingHeader diagnostics against the stub",
        );
    }

    #[test]
    fn find_def_by_name_finds_pass_body_function() {
        // `def foo(): pass` is NOT a stub per `is_stub_body` — only
        // ellipsis-body counts. A pass-body function IS a real subject that
        // deserves a MissingHeader diagnostic if it lacks a docstring.
        let stmts = parse_stmts("def foo(): pass\n");
        assert!(
            matches!(
                find_def_by_name(&stmts, "foo"),
                Some(DefTarget::Function(_))
            ),
            "pass-body must NOT be filtered as a stub — the diagnostic path treats it as a real def missing its docstring, anchoring at the def line",
        );
    }

    #[test]
    fn docstring_and_lineno_from_class_returns_docstring() {
        let src = "class Cls:\n    '''class doc'''\n    pass\n";
        let stmts = parse_stmts(src);
        let line_index = crate::python_ast::build_line_index(src);
        let target = find_def_by_name(&stmts, "Cls").unwrap();
        let (doc, _lineno) = docstring_and_lineno_from(target, &line_index);
        assert_eq!(
            doc.as_deref(),
            Some("class doc"),
            "class docstring extracts through the same primitive as functions — the unified helper is the whole point of the DefTarget shape",
        );
    }

    #[test]
    fn member_docstring_flows_through_find_class_method_and_extractor() {
        let src = "class Cls:\n    def method(self):\n        '''doc'''\n        pass\n";
        let stmts = parse_stmts(src);
        let line_index = crate::python_ast::build_line_index(src);
        let target = find_class_method(&stmts, "Cls", "method").unwrap();
        let (doc, lineno) = docstring_and_lineno_from(target, &line_index);
        assert_eq!(
            doc.as_deref(),
            Some("doc"),
            "method's own docstring (not the class's) must be extracted so the coverage check grades the method's contract",
        );
        assert!(
            lineno >= 2,
            "lineno must point at the method def line (>= 2 given class on line 1) so the diagnostic anchors on the method, not the class",
        );
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
                "docstring without `Examples:` header should classify as MissingHeader, got {other:?}"
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
                "docstring with `Examples:` header but no `>>>` block should classify as HeaderNoExamples, got {other:?}"
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
                "no docstring at all is the strongest form of MissingHeader — the header can't exist without a docstring, got {other:?}"
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
        let (diags, _, _) = run_coverage_check(
            &files,
            &module_root,
            DiagnosticSeverity::Warning,
            &None,
            &[],
        );
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
        let (diags, _, _) = run_coverage_check(
            &files,
            &module_root,
            DiagnosticSeverity::Warning,
            &None,
            &[],
        );
        assert!(
            diags.is_empty(),
            "fully covered subject ⇒ no diagnostics, got: {diags:?}"
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
        let (diags, _, _) = run_coverage_check(
            &files,
            &module_root,
            DiagnosticSeverity::Warning,
            &None,
            &[],
        );
        assert!(
            diags.is_empty(),
            "underscore-prefixed module path ⇒ subjects not scanned (Layer 1 filter), got: {diags:?}"
        );
    }

    #[test]
    fn run_coverage_check_parse_failure_lands_in_parse_failed_not_parsed() {
        // The three-state scanned set (#1800): a file the scan attempted but
        // could not read must be reported back as parse-failed, with its own
        // diagnostic — before #1800 it vanished from both the scanned set and
        // the diagnostic stream. This test could not be written red-first: it
        // cannot compile without `ScannedFiles`; the observed pre-fix silent
        // drop is pinned red-first by the `collect_coverage_diagnostics`
        // family in `pipeline::collection`.
        use std::fs;
        use tempfile::tempdir;

        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        fs::create_dir_all(root.join("mypkg")).unwrap();
        fs::write(root.join("mypkg/__init__.py"), "\"\"\"pkg.\"\"\"\n").unwrap();
        fs::write(root.join("mypkg/broken.py"), "def broken(:\n    pass\n").unwrap();
        let module_root = ModuleRoot {
            root,
            use_gitignore: true,
        };
        let files = vec![
            Utf8PathBuf::from("mypkg/__init__.py"),
            Utf8PathBuf::from("mypkg/broken.py"),
        ];

        let (diags, _, scanned) = run_coverage_check(
            &files,
            &module_root,
            DiagnosticSeverity::Warning,
            &None,
            &[],
        );

        assert_eq!(
            scanned.parse_failed,
            vec![Utf8PathBuf::from("mypkg/broken.py")],
            "the scan attempted the read and failed — entry classification \
             needs this third state to report instead of abstaining (#1800)",
        );
        assert_eq!(
            scanned.parsed,
            vec![Utf8PathBuf::from("mypkg/__init__.py")],
            "a parse-failed file must not leak into the parsed set — parsed \
             files ground staleness verdicts, and an unread file holds no \
             evidence about its symbols (#1796)",
        );
        let parse_error = diags
            .iter()
            .find(|diag| diag.context.as_ref() == "doctest.coverage.parse-error")
            .expect("the parse failure must produce its own diagnostic (#1800)");
        assert!(
            parse_error.message.contains("mypkg/broken.py"),
            "the diagnostic must name the file so the user knows what to fix; \
             got: {}",
            parse_error.message,
        );
        assert_eq!(
            parse_error.file.as_deref(),
            Some(camino::Utf8Path::new("mypkg/broken.py")),
            "the structured file field must carry the path too, so reporters \
             that group by file place the finding correctly",
        );
    }

    #[test]
    fn run_coverage_check_privacy_gated_file_lands_in_neither_scanned_state() {
        // The privacy gate runs before `parse_file`: a withheld file was
        // never attempted, so it must appear in neither `parsed` nor
        // `parse_failed` — the "never offered" third state that forces
        // entry-classification abstention (#1800).
        use std::fs;
        use tempfile::tempdir;

        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        fs::create_dir_all(root.join("mypkg/_internal")).unwrap();
        fs::write(root.join("mypkg/__init__.py"), "").unwrap();
        fs::write(root.join("mypkg/_internal/__init__.py"), "").unwrap();
        fs::write(
            root.join("mypkg/_internal/broken.py"),
            "def broken(:\n    pass\n",
        )
        .unwrap();
        let module_root = ModuleRoot {
            root,
            use_gitignore: true,
        };
        let files = vec![Utf8PathBuf::from("mypkg/_internal/broken.py")];

        let (diags, _, scanned) = run_coverage_check(
            &files,
            &module_root,
            DiagnosticSeverity::Warning,
            &None,
            &[],
        );

        assert!(
            scanned.parse_failed.is_empty(),
            "the privacy gate withheld the file before the parse — recording \
             it as parse-failed would make entries into private modules \
             report a failure for a read the scan never attempted (#1800)",
        );
        assert!(
            scanned.parsed.is_empty(),
            "a withheld file was never opened, so it cannot be in the parsed \
             set either — that is the #1796 evidence rule",
        );
        assert!(
            diags.is_empty(),
            "no diagnostic for a file the scan never asked for — the \
             norecursedirs/privacy exclusions are what keep deliberately \
             broken fixture files legal in strict repos; got: {diags:?}",
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
        let (diags, _, _) = run_coverage_check(
            &files,
            &module_root,
            DiagnosticSeverity::Warning,
            &None,
            &[],
        );
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
        let (diags, _, _) = run_coverage_check(
            &files,
            &module_root,
            DiagnosticSeverity::Warning,
            &None,
            &[],
        );
        assert!(!diags.is_empty(), "cycle should produce a diagnostic");
        let cycle_diag = diags
            .iter()
            .find(|d| d.message.contains("alias cycle"))
            .expect("expected an `alias cycle` diagnostic");
        assert_eq!(
            cycle_diag.context.as_ref(),
            "doctest.coverage.analysis",
            "walk errors emit under the analysis context — must not be conflated with coverage gaps"
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
            root,
            use_gitignore: true,
        };
        let files = vec![Utf8PathBuf::from("mypkg/__init__.py")];
        let (diags, _, _) =
            run_coverage_check(&files, &module_root, DiagnosticSeverity::Error, &None, &[]);

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

#[cfg(test)]
mod scope_filter_tests {
    use super::filter_subjects_by_scope;
    use crate::config::ScopeEntry;
    use crate::doctest::subjects::{Subject, SubjectSource};
    use camino::Utf8PathBuf;

    fn subj(module: &str, name: &str, file: &str) -> Subject {
        Subject {
            public_id: format!("{module}.{name}"),
            name: name.to_owned(),
            source: SubjectSource::LocalDefinition,
            file: Utf8PathBuf::from(file),
            class_context: None,
        }
    }

    #[test]
    fn public_scope_applies_private_filter() {
        let subjects = vec![
            (
                String::from("pkg.mod"),
                subj("pkg.mod", "foo", "pkg/mod.py"),
            ),
            (
                String::from("pkg.mod"),
                subj("pkg.mod", "_hidden", "pkg/mod.py"),
            ),
        ];
        let scope: Option<crate::config::DoctestScope> = None; // treated as Public
        let (kept, _) = filter_subjects_by_scope(subjects, &scope, &[]);
        let names: Vec<String> = kept.iter().map(|(_, s)| s.public_id.clone()).collect();
        assert_eq!(
            names,
            vec!["pkg.mod.foo".to_string()],
            "Public scope drops _-prefixed names",
        );
    }

    #[test]
    fn list_scope_bypasses_private_filter_when_explicit() {
        let subjects = vec![
            (
                String::from("pkg.mod"),
                subj("pkg.mod", "foo", "pkg/mod.py"),
            ),
            (
                String::from("pkg.mod"),
                subj("pkg.mod", "_hidden", "pkg/mod.py"),
            ),
        ];
        let scope = Some(crate::config::DoctestScope::List(vec![
            ScopeEntry::Symbol {
                file: Utf8PathBuf::from("pkg/mod.py"),
                name: "_hidden".to_string(),
            },
        ]));
        let (kept, _) = filter_subjects_by_scope(subjects, &scope, &[]);
        let names: Vec<String> = kept.iter().map(|(_, s)| s.public_id.clone()).collect();
        assert_eq!(
            names,
            vec!["pkg.mod._hidden".to_string()],
            "List scope with explicit _hidden entry bypasses the private filter",
        );
    }

    #[test]
    fn list_scope_multiple_entries_union() {
        let subjects = vec![
            (String::from("a"), subj("a", "x", "a.py")),
            (String::from("b"), subj("b", "y", "b.py")),
            (String::from("c"), subj("c", "z", "c.py")),
        ];
        let scope = Some(crate::config::DoctestScope::List(vec![
            ScopeEntry::File(Utf8PathBuf::from("a.py")),
            ScopeEntry::File(Utf8PathBuf::from("b.py")),
        ]));
        let (kept, _) = filter_subjects_by_scope(subjects, &scope, &[]);
        let names: Vec<String> = kept.iter().map(|(_, s)| s.public_id.clone()).collect();
        assert_eq!(
            names,
            vec!["a.x".to_string(), "b.y".to_string()],
            "union of two File entries covers both files' subjects",
        );
    }

    #[test]
    fn skip_subtracts_from_scope() {
        let subjects = vec![
            (String::from("a"), subj("a", "x", "a.py")),
            (String::from("a"), subj("a", "y", "a.py")),
        ];
        let scope = Some(crate::config::DoctestScope::List(vec![ScopeEntry::File(
            Utf8PathBuf::from("a.py"),
        )]));
        let skip = vec![ScopeEntry::Symbol {
            file: Utf8PathBuf::from("a.py"),
            name: "y".to_string(),
        }];
        let (kept, _) = filter_subjects_by_scope(subjects, &scope, &skip);
        let names: Vec<String> = kept.iter().map(|(_, s)| s.public_id.clone()).collect();
        assert_eq!(
            names,
            vec!["a.x".to_string()],
            "skip subtracts individual subjects even when scope is coarser",
        );
    }

    #[test]
    fn symbol_entry_matches_only_named_top_level() {
        let subjects = vec![
            (String::from("m"), subj("m", "foo", "m.py")),
            (String::from("m"), subj("m", "bar", "m.py")),
        ];
        let scope = Some(crate::config::DoctestScope::List(vec![
            ScopeEntry::Symbol {
                file: Utf8PathBuf::from("m.py"),
                name: "foo".to_string(),
            },
        ]));
        let (kept, _) = filter_subjects_by_scope(subjects, &scope, &[]);
        let names: Vec<String> = kept.iter().map(|(_, s)| s.public_id.clone()).collect();
        assert_eq!(
            names,
            vec!["m.foo".to_string()],
            "Symbol entry with name='foo' matches only that top-level symbol",
        );
    }
}
