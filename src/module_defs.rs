//! Strict check: a module-level definition in a `test_*.py` file must be a
//! test, a fixture declaration, or `_`-prefixed.
//!
//! The rule is **preventive**. Measured over `python/tests` at `326fed2f`, no
//! file imports from a `test_*.py` module, so the failure mode it stops — a
//! shared utility defined at the top of a test file and imported elsewhere —
//! occurs zero times today. What is present is the convention: 220 module-level
//! definitions already carry the `_` prefix (#1783).
//!
//! Deliberately a **fourth** walk. `python_ast::walk_test_defs` visits `test_`
//! names, `prescan::collect_declarations` visits fixture-decorated definitions,
//! and `prescan::collect_fx_usages` descends test bodies; none enumerates every
//! module-level definition, and widening one of them would change what its own
//! callers see.
//!
//! **Top level only, and that is what makes classes legal by construction
//! rather than by a filter.** `docs/user/how-to/use-class-based-tests.md`
//! teaches `class TestUsers:`, and the parametrize idiom puts `@dataclass` case
//! objects at module level; both stay legal because this walk never descends
//! into a class body.

use camino::Utf8Path;
use rustpython_parser::ast;

use crate::bridge::{RawViolation, ViolationKind};
use crate::python_ast;

/// Parse a Python test file and return module-level definition violations.
pub fn collect_module_defs(path: &Utf8Path) -> Vec<RawViolation> {
    let (source, stmts) = match python_ast::parse_file(path) {
        Some(parsed) => parsed,
        None => return vec![],
    };
    collect_module_defs_from_ast(path, &source, &stmts)
}

/// Detect violations from a pre-parsed AST, so strict mode does not re-parse.
pub fn collect_module_defs_from_ast(
    path: &Utf8Path,
    source: &str,
    stmts: &[ast::Stmt],
) -> Vec<RawViolation> {
    let line_index = python_ast::build_line_index(source);
    let mut out = Vec::new();

    for stmt in stmts {
        let (name, decorators, range) = match stmt {
            ast::Stmt::FunctionDef(f) => (f.name.as_str(), &f.decorator_list, f.range),
            ast::Stmt::AsyncFunctionDef(f) => (f.name.as_str(), &f.decorator_list, f.range),
            // Every other top-level form, `class` included, is outside this
            // check by construction.
            _ => continue,
        };
        if name.starts_with("test_") || name.starts_with('_') {
            continue;
        }
        // A `test_`-named definition carrying `@oxi.fixture` already passed the
        // clause above. The overlap is deliberately not resolved here:
        // `prescan.rs` records that doing so silently "would hide a user error
        // that belongs in a diagnostic" (#1783 AC3).
        if decorators.iter().any(is_fixture_decorator) {
            continue;
        }
        let lineno = python_ast::offset_to_line(&line_index, range.start().to_u32());
        out.push(RawViolation {
            node_id: format!("{path}::{name}"),
            kind: ViolationKind::ModuleLevelDef,
            detail: format!("{name} {lineno}"),
        });
    }
    out
}

/// Recognize `@oxi.fixture`, `@oxitest.fixture`, `@fixture` and their called
/// forms.
///
/// Looser than `prescan::extract_fixture_decorator_lifetime`, on purpose: that
/// one answers *"what lifetime does this declare?"* and returns `None` for a
/// bare `@oxi.fixture` with no arguments. This one answers *"is this a fixture
/// declaration at all?"*, and a declaration whose decorator shape it cannot
/// parse must not be reported as an illegal helper.
fn is_fixture_decorator(dec: &ast::Expr) -> bool {
    // `@oxi.fixture` and `@oxi.fixture(...)` both count. The called form's
    // predicate already exists as `prescan::is_fixture_call`; only the
    // uncalled form needs unwrapping first.
    let target = match dec {
        ast::Expr::Call(c) => c.func.as_ref(),
        other => other,
    };
    crate::prescan::is_fixture_call(target)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::python_ast::tests::{temp_path, write_temp_py};

    fn names(src: &str) -> Vec<String> {
        let f = write_temp_py(src);
        collect_module_defs(&temp_path(&f))
            .into_iter()
            .map(|v| v.detail.rsplit_once(' ').unwrap().0.to_string())
            .collect()
    }

    #[test]
    fn the_three_legal_forms_are_legal() {
        let src = "import oxitest as oxi\n\
                   def test_it():\n    pass\n\
                   def _local():\n    pass\n\
                   @oxi.fixture(lifetime=\"function\")\n\
                   def conn():\n    return 1\n";
        assert!(
            names(src).is_empty(),
            "a test, a _-prefixed helper and a fixture declaration are the three \
             forms the rule exists to permit; flagging any of them makes the check \
             unusable in the corpus it was measured against",
        );
    }

    #[test]
    fn a_plain_module_level_helper_is_flagged() {
        assert_eq!(
            names("def build_order():\n    pass\n"),
            vec!["build_order"],
            "the one shape the rule is for -- a shared-looking utility with no \
             prefix, no test name and no decorator",
        );
    }

    #[test]
    fn async_def_is_covered_too() {
        assert_eq!(
            names("async def build():\n    pass\n"),
            vec!["build"],
            "async def is a module-level definition like any other; covering only \
             `def` would leave an async-heavy suite silently exempt",
        );
    }

    #[test]
    fn a_class_is_never_flagged() {
        let src = "from dataclasses import dataclass\n\
                   class TestUsers:\n    def test_a(self):\n        pass\n\
                   @dataclass\nclass Case:\n    value: int\n";
        assert!(
            names(src).is_empty(),
            "use-class-based-tests.md teaches `class TestUsers:` and the \
             parametrize idiom puts @dataclass case objects at module level; the \
             rule refusing either would refuse documented practice",
        );
    }

    #[test]
    fn a_method_inside_a_class_is_never_flagged() {
        let src = "class Helper:\n    def build(self):\n        pass\n";
        assert!(
            names(src).is_empty(),
            "the walk is top-level only, so a method cannot reach it -- this pins \
             that as behaviour rather than as an implementation detail",
        );
    }

    #[test]
    fn the_test_name_and_fixture_overlap_is_legal_here() {
        let src = "import oxitest as oxi\n\
                   @oxi.fixture(lifetime=\"function\")\n\
                   def test_both():\n    return 1\n";
        assert!(
            names(src).is_empty(),
            "prescan records that resolving this overlap silently would hide a \
             user error belonging in its own diagnostic; this check must not be \
             the thing that resolves it",
        );
    }

    #[test]
    fn a_bare_fixture_decorator_still_counts_as_a_declaration() {
        let src = "import oxitest as oxi\n@oxi.fixture\ndef conn():\n    return 1\n";
        assert!(
            names(src).is_empty(),
            "the lifetime-extracting predicate returns None for an uncalled \
             decorator; reusing it here would report a fixture declaration as an \
             illegal helper",
        );
    }

    #[test]
    fn the_detail_carries_the_name_and_the_line() {
        let f = write_temp_py("\n\ndef build():\n    pass\n");
        let v = collect_module_defs(&temp_path(&f));
        assert_eq!(v.len(), 1);
        assert_eq!(v[0].kind, ViolationKind::ModuleLevelDef);
        assert_eq!(
            v[0].detail, "build 3",
            "strict.rs splits this from the right to render the message; a detail \
             that loses the line number renders a violation the user cannot locate",
        );
    }
}
