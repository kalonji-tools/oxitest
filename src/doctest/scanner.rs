//! Doctest discovery: extract `>>>` interactive examples from Python docstrings.

use camino::Utf8Path;
use rustpython_parser::ast;

use crate::python_ast;

/// A single doctest found in a docstring.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct DoctestExample {
    /// Source code (all `>>>` and `...` lines joined).
    pub source: String,
    /// Expected output (lines following source, before next `>>>` or blank line).
    pub want: String,
    /// 0-based line offset within the docstring where this example starts.
    pub lineno: usize,
}

/// Parse a docstring for `>>>` interactive examples.
pub(crate) fn parse_docstring_examples(docstring: &str) -> Vec<DoctestExample> {
    let mut examples = Vec::new();
    let mut source = String::new();
    let mut want = String::new();
    let mut example_start: Option<usize> = None;

    for (i, line) in docstring.lines().enumerate() {
        let trimmed = line.trim();

        if let Some(rest) = trimmed
            .strip_prefix(">>> ")
            .or_else(|| if trimmed == ">>>" { Some("") } else { None })
        {
            if !source.is_empty() && !want.is_empty() {
                examples.push(DoctestExample {
                    source: std::mem::take(&mut source),
                    want: std::mem::take(&mut want),
                    lineno: example_start.unwrap(),
                });
            }
            if source.is_empty() {
                example_start = Some(i);
            }
            if !source.is_empty() && want.is_empty() {
                examples.push(DoctestExample {
                    source: std::mem::take(&mut source),
                    want: String::new(),
                    lineno: example_start.unwrap(),
                });
                example_start = Some(i);
            }
            source.push_str(rest);
            source.push('\n');
            want.clear();
        } else if let Some(rest) = trimmed
            .strip_prefix("... ")
            .or_else(|| if trimmed == "..." { Some("") } else { None })
        {
            if !source.is_empty() {
                source.push_str(rest);
                source.push('\n');
            }
        } else if !source.is_empty() && !trimmed.is_empty() {
            want.push_str(line.trim());
            want.push('\n');
        } else if !source.is_empty() {
            examples.push(DoctestExample {
                source: std::mem::take(&mut source),
                want: std::mem::take(&mut want),
                lineno: example_start.unwrap(),
            });
            example_start = None;
        }
    }

    if !source.is_empty() {
        examples.push(DoctestExample {
            source: std::mem::take(&mut source),
            want: std::mem::take(&mut want),
            lineno: example_start.unwrap(),
        });
    }

    examples
}

/// Extended parser: returns `(header_present, examples)` for the coverage rule (#1607).
///
/// - `header_present`: true if any line, after leading whitespace trimming, is exactly
///   `Examples:` or `Example:`. Google-style header detection anywhere in the docstring;
///   no section scoping per #1607.
/// - `examples`: same as `parse_docstring_examples` — `>>>` blocks with expected outputs.
pub(super) fn parse_docstring_examples_v2(docstring: &str) -> (bool, Vec<DoctestExample>) {
    let header_present = docstring.lines().any(|line| {
        let trimmed = line.trim_start();
        trimmed == "Examples:" || trimmed == "Example:"
    });
    let examples = parse_docstring_examples(docstring);
    (header_present, examples)
}

/// A location in a Python file where a docstring with `>>>` examples was found.
#[derive(Debug, Clone)]
pub(crate) struct DoctestLocation {
    pub name: String,
    pub lineno: usize,
    #[allow(
        dead_code,
        reason = "Populated for future M2 execution wiring; asserted in tests today."
    )]
    pub example_count: usize,
}

/// Extract a docstring (first statement is a string literal) from a body.
pub(super) fn extract_docstring(body: &[ast::Stmt]) -> Option<&str> {
    if let Some(ast::Stmt::Expr(expr)) = body.first()
        && let ast::Expr::Constant(ast::ExprConstant {
            value: ast::Constant::Str(s),
            ..
        }) = &*expr.value
    {
        return Some(s.as_str());
    }
    None
}

/// If `body` has a docstring with `>>>` examples, push a `DoctestLocation`.
fn try_push_doctest(
    locations: &mut Vec<DoctestLocation>,
    name: String,
    body: &[ast::Stmt],
    range: rustpython_parser::text_size::TextRange,
    line_index: &[u32],
) {
    if let Some(doc) = extract_docstring(body) {
        let count = parse_docstring_examples(doc).len();
        if count > 0 {
            let lineno =
                crate::python_ast::offset_to_line(line_index, range.start().into()) as usize;
            locations.push(DoctestLocation {
                name,
                lineno,
                example_count: count,
            });
        }
    }
}

/// Scan a Python file's AST for all docstrings containing `>>>` examples.
///
/// Returns a `DoctestLocation` per docstring that has at least one example.
/// Walks module-level, function, class, and class method docstrings.
pub(crate) fn scan_doctests(path: &Utf8Path) -> Vec<DoctestLocation> {
    let parsed = match crate::python_ast::parse_file(path) {
        Some(p) => p,
        None => return vec![],
    };
    let (source, stmts) = parsed;
    let module_name = path.file_stem().unwrap_or(path.as_str());
    let line_index = crate::python_ast::build_line_index(&source);
    let mut locations = Vec::new();

    // Module-level docstring
    if let Some(doc) = extract_docstring(&stmts) {
        let count = parse_docstring_examples(doc).len();
        if count > 0 {
            locations.push(DoctestLocation {
                name: module_name.to_string(),
                lineno: 1,
                example_count: count,
            });
        }
    }

    for stmt in &stmts {
        if let Some(def) = python_ast::FnDef::try_from_stmt(stmt) {
            try_push_doctest(
                &mut locations,
                format!("{}.{}", module_name, def.name()),
                def.body(),
                def.range(),
                &line_index,
            );
        } else if let ast::Stmt::ClassDef(cls) = stmt {
            // Class-level docstring
            try_push_doctest(
                &mut locations,
                format!("{}.{}", module_name, cls.name),
                &cls.body,
                cls.range,
                &line_index,
            );
            // Method docstrings
            for method in &cls.body {
                if let Some(def) = python_ast::FnDef::try_from_stmt(method) {
                    try_push_doctest(
                        &mut locations,
                        format!("{}.{}.{}", module_name, cls.name, def.name()),
                        def.body(),
                        def.range(),
                        &line_index,
                    );
                }
            }
        }
    }

    locations
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_docstring() {
        assert!(parse_docstring_examples("").is_empty());
    }

    #[test]
    fn no_examples() {
        assert!(parse_docstring_examples("Just a description.\n\nNo code here.").is_empty());
    }

    #[test]
    fn single_example_with_output() {
        let doc = "Example:\n\n    >>> 1 + 1\n    2\n";
        let examples = parse_docstring_examples(doc);
        assert_eq!(examples.len(), 1);
        assert_eq!(examples[0].source, "1 + 1\n");
        assert_eq!(examples[0].want, "2\n");
        assert_eq!(examples[0].lineno, 2);
    }

    #[test]
    fn example_without_output() {
        let doc = "    >>> x = 42\n";
        let examples = parse_docstring_examples(doc);
        assert_eq!(examples.len(), 1);
        assert_eq!(examples[0].source, "x = 42\n");
        assert!(examples[0].want.is_empty());
    }

    #[test]
    fn continuation_lines() {
        let doc = "    >>> for i in range(3):\n    ...     print(i)\n    0\n    1\n    2\n";
        let examples = parse_docstring_examples(doc);
        assert_eq!(examples.len(), 1);
        assert_eq!(examples[0].source, "for i in range(3):\n    print(i)\n");
        assert_eq!(examples[0].want, "0\n1\n2\n");
    }

    #[test]
    fn multiple_examples() {
        let doc = "    >>> 1 + 1\n    2\n\n    >>> 2 + 2\n    4\n";
        let examples = parse_docstring_examples(doc);
        assert_eq!(examples.len(), 2);
        assert_eq!(examples[0].source, "1 + 1\n");
        assert_eq!(examples[1].source, "2 + 2\n");
    }

    #[test]
    fn bare_prompt_no_space() {
        let doc = "    >>>\n";
        let examples = parse_docstring_examples(doc);
        assert_eq!(examples.len(), 1);
        assert_eq!(examples[0].source, "\n");
    }

    // ── scan_doctests ──────────────────────────────────────────────────

    #[test]
    fn scan_doctests_finds_function_docstrings() {
        let dir = tempfile::TempDir::new().unwrap();
        let path = dir.path().join("example.py");
        std::fs::write(
            &path,
            "def add(a, b):\n    \"\"\"Add two numbers.\n\n    >>> add(1, 2)\n    3\n    \"\"\"\n    return a + b\n",
        )
        .unwrap();
        let utf8_path = camino::Utf8Path::from_path(&path).unwrap();
        let locs = scan_doctests(utf8_path);
        assert_eq!(locs.len(), 1);
        assert_eq!(locs[0].name, "example.add");
        assert_eq!(locs[0].example_count, 1);
    }

    #[test]
    fn scan_doctests_finds_class_and_method() {
        let dir = tempfile::TempDir::new().unwrap();
        let path = dir.path().join("calc.py");
        std::fs::write(
            &path,
            "class Calculator:\n    \"\"\"A calculator.\n\n    >>> Calculator\n    <class 'calc.Calculator'>\n    \"\"\"\n    def add(self, a, b):\n        \"\"\"Add.\n\n        >>> Calculator().add(1, 2)\n        3\n        \"\"\"\n        return a + b\n",
        )
        .unwrap();
        let utf8_path = camino::Utf8Path::from_path(&path).unwrap();
        let locs = scan_doctests(utf8_path);
        assert_eq!(locs.len(), 2);
        assert_eq!(locs[0].name, "calc.Calculator");
        assert_eq!(locs[1].name, "calc.Calculator.add");
    }

    #[test]
    fn scan_doctests_skips_no_examples() {
        let dir = tempfile::TempDir::new().unwrap();
        let path = dir.path().join("empty.py");
        std::fs::write(
            &path,
            "def foo():\n    \"\"\"No examples here.\"\"\"\n    pass\n",
        )
        .unwrap();
        let utf8_path = camino::Utf8Path::from_path(&path).unwrap();
        let locs = scan_doctests(utf8_path);
        assert!(locs.is_empty());
    }

    // ── parse_docstring_examples_v2: `Examples:` / `Example:` header detection ──

    #[test]
    fn header_examples_plural_detected() {
        let (header, examples) =
            parse_docstring_examples_v2("Some prose.\n\nExamples:\n    >>> 1 + 1\n    2\n");
        assert!(
            header,
            "`Examples:` (plural) is the primary Google spelling"
        );
        assert_eq!(examples.len(), 1);
    }

    #[test]
    fn header_example_singular_detected() {
        let (header, examples) =
            parse_docstring_examples_v2("Some prose.\n\nExample:\n    >>> 1 + 1\n    2\n");
        assert!(
            header,
            "`Example:` (singular) is the accepted alternative per #1607"
        );
        assert_eq!(examples.len(), 1);
    }

    #[test]
    fn no_header_but_with_examples() {
        let (header, examples) = parse_docstring_examples_v2(">>> 1 + 1\n2\n");
        assert!(!header, "no `Examples:` line ⇒ header_present = false");
        assert_eq!(
            examples.len(),
            1,
            "still captures the `>>>` block; header/coverage are separate concerns"
        );
    }

    #[test]
    fn header_but_no_examples_block() {
        let (header, examples) =
            parse_docstring_examples_v2("Examples:\n\nJust prose here, no `>>>`.\n");
        assert!(header);
        assert_eq!(
            examples.len(),
            0,
            "header alone ⇒ Task 12/13 will diagnose HeaderNoExamples"
        );
    }

    #[test]
    fn header_and_examples_may_be_out_of_order() {
        // Per #1607: presence anywhere in the docstring is sufficient; no scoping.
        let (header, examples) = parse_docstring_examples_v2(">>> 1 + 1\n2\n\nExamples:\n");
        assert!(header, "header can appear after the `>>>` block");
        assert_eq!(examples.len(), 1);
    }

    #[test]
    fn indented_header_line_still_detected() {
        // Common in class/method docstrings — the header is indented under the containing block.
        let (header, examples) = parse_docstring_examples_v2(
            "    A class docstring.\n\n    Examples:\n        >>> 1 + 1\n        2\n",
        );
        assert!(header, "trim_start should catch indented `Examples:` lines");
        assert_eq!(examples.len(), 1);
    }

    #[test]
    fn examples_word_in_prose_not_a_header() {
        // Prose that mentions "Examples" as part of a sentence — NOT the section header.
        // The header is a full-line `Examples:` (with the colon), possibly indented.
        let (header, _) = parse_docstring_examples_v2("See Examples of usage in the guide.\n");
        assert!(
            !header,
            "prose mention without a colon-terminated section line is not a header"
        );
    }
}
