use std::fmt::Write as _;
#[cfg(not(test))]
use std::sync::OnceLock;

use crate::config::TbStyle;
use crate::types::{FailureDiagnostic, TestItem, TestOutcome};

use super::diff::fmt_field_diffs;
use super::fmt_diff;
use crate::colors::{color_blue, color_bold_white, color_dim, color_dim_cyan};

struct BoxChars {
    open: &'static str,
    vert: &'static str,
    close: &'static str,
    margin: &'static str,
}

const BOX: BoxChars = BoxChars {
    open: "┌",
    vert: "│",
    close: "└",
    margin: "      ",
};

/// Returns the terminal width for separator lines, capped at 100 columns.
///
/// Queried once and cached; subsequent calls return the same value. The 100-column
/// cap keeps output readable on wide terminals.
///
/// In test builds, always returns 80 for deterministic snapshot output.
// `allow`, not `expect`: under `--all-targets` clippy lints the cfg(test) body —
// the literal 80, so const-eligible — while the real cfg(not(test)) body calls
// `OnceLock::get_or_init`, which is not const-callable (E0015). The lint therefore
// fires for the test target and not for the lib target, and an `expect` that goes
// unfulfilled in either configuration is itself a denied warning.
#[allow(clippy::missing_const_for_fn)]
pub(crate) fn sep_width() -> usize {
    #[cfg(test)]
    {
        80
    }
    #[cfg(not(test))]
    {
        static WIDTH: OnceLock<usize> = OnceLock::new();
        *WIDTH.get_or_init(|| {
            let (_, cols) = console::Term::stdout().size();
            (cols as usize).min(100)
        })
    }
}

/// Returns the separator string between parametrize case identifiers.
///
/// Color mode gets a middle dot (`·`) for visual polish; plain mode gets a hyphen
/// to stay safe in environments that don't support Unicode.
pub(crate) const fn case_sep(use_color: bool) -> &'static str {
    if use_color { " · " } else { " - " }
}

/// Pad `s` to `width` visible columns, measuring with ANSI codes stripped.
pub(crate) fn pad_to(s: &str, width: usize) -> String {
    let visual = console::measure_text_width(s);
    if visual >= width {
        s.to_string()
    } else {
        format!("{}{}", s, " ".repeat(width - visual))
    }
}

/// Render the WHERE section: `┌ file:line`.
fn render_where(out: &mut String, file: &str, lineno: crate::types::LineNo, use_color: bool) {
    if file.is_empty() {
        return;
    }
    let loc = format!("{file}:{lineno}");
    let _ = writeln!(
        out,
        "{}{} {}",
        BOX.margin,
        color_dim(BOX.open, use_color),
        color_dim_cyan(&loc, use_color)
    );
    let _ = writeln!(out, "{}{}", BOX.margin, color_dim(BOX.vert, use_color));
}

/// Render the WITH WHAT section: inline param key=value pairs.
fn render_params(out: &mut String, param_values: &[crate::types::ParamPair], use_color: bool) {
    if param_values.is_empty() {
        return;
    }
    let params_str = param_values
        .iter()
        .map(|p| {
            format!(
                "{} {} {}",
                color_dim(&p.name, use_color),
                color_dim("=", use_color),
                p.value,
            )
        })
        .collect::<Vec<_>>()
        .join(&color_dim(", ", use_color));
    let _ = writeln!(
        out,
        "{}{}  {}",
        BOX.margin,
        color_dim(BOX.vert, use_color),
        params_str
    );
    let _ = writeln!(out, "{}{}", BOX.margin, color_dim(BOX.vert, use_color));
}

/// Render the WHAT section: bold source line hero + optional failure-frame locals.
fn render_source(out: &mut String, diag: &FailureDiagnostic, show_locals: bool, use_color: bool) {
    if diag.source_line.is_empty() {
        return;
    }
    let _ = writeln!(
        out,
        "{}{}  {}",
        BOX.margin,
        color_dim(BOX.vert, use_color),
        color_bold_white(&diag.source_line, use_color)
    );
    if show_locals {
        let failure_locals = diag.frames.iter().find_map(|f| {
            if f.file.as_str() == diag.file.as_str()
                && f.lineno == diag.lineno
                && !f.locals.is_empty()
            {
                Some(&f.locals)
            } else {
                None
            }
        });
        if let Some(locals) = failure_locals {
            render_locals(out, locals, 4, use_color);
        }
    }
    let _ = writeln!(out, "{}{}", BOX.margin, color_dim(BOX.vert, use_color));
}

/// Render local variable bindings at a given indentation depth.
fn render_locals(
    out: &mut String,
    locals: &[crate::types::LocalVar],
    indent: usize,
    use_color: bool,
) {
    let pad = " ".repeat(indent);
    for local in locals {
        let _ = writeln!(
            out,
            "{}{}{pad}{} = {}",
            BOX.margin,
            color_dim(BOX.vert, use_color),
            color_dim(&local.name, use_color),
            color_dim(&local.repr, use_color)
        );
    }
}

/// Render the TRACE section: non-failure traceback frames.
fn render_trace(out: &mut String, diag: &FailureDiagnostic, show_locals: bool, use_color: bool) {
    let trace_frames: Vec<_> = diag
        .frames
        .iter()
        .filter(|f| !(f.file.as_str() == diag.file.as_str() && f.lineno == diag.lineno))
        .collect();
    if trace_frames.is_empty() {
        return;
    }
    let _ = writeln!(
        out,
        "{}{}  {}",
        BOX.margin,
        color_dim(BOX.vert, use_color),
        color_dim("trace", use_color)
    );
    for (i, f) in trace_frames.iter().enumerate() {
        if i > 0 {
            let _ = writeln!(out, "{}{}", BOX.margin, color_dim(BOX.vert, use_color));
        }
        let loc = format!("{}:{} in {}", f.file, f.lineno, f.name);
        let _ = writeln!(
            out,
            "{}{}    {}",
            BOX.margin,
            color_dim(BOX.vert, use_color),
            color_dim(&loc, use_color)
        );
        if !f.line.is_empty() {
            let _ = writeln!(
                out,
                "{}{}      {}",
                BOX.margin,
                color_dim(BOX.vert, use_color),
                color_bold_white(&f.line, use_color)
            );
        }
        if show_locals && !f.locals.is_empty() {
            render_locals(out, &f.locals, 8, use_color);
        }
    }
    let _ = writeln!(out, "{}{}", BOX.margin, color_dim(BOX.vert, use_color));
}

/// Render the HINT section: fix suggestion inside box.
fn render_hint(out: &mut String, outcome: &TestOutcome, use_color: bool) {
    if let Some(hint_text) = super::suggestions::suggest_fix(outcome) {
        let _ = writeln!(
            out,
            "{}{}  {}",
            BOX.margin,
            color_dim(BOX.vert, use_color),
            color_blue(&format!("hint: {hint_text}"), use_color)
        );
        let _ = writeln!(out, "{}{}", BOX.margin, color_dim(BOX.vert, use_color));
    }
}

/// Render the WHY section: closing `└` message.
fn render_closing(out: &mut String, message: &str, use_color: bool) {
    if message.is_empty() {
        let _ = writeln!(out, "{}{}", BOX.margin, color_dim(BOX.close, use_color));
        return;
    }
    let mut lines = message.lines();
    if let Some(first) = lines.next() {
        let _ = writeln!(
            out,
            "{}{} {}",
            BOX.margin,
            color_dim(BOX.close, use_color),
            color_dim(first, use_color)
        );
    }
    for cont in lines {
        let _ = writeln!(
            out,
            "{}{}  {}",
            BOX.margin,
            color_dim(BOX.vert, use_color),
            color_dim(cont, use_color)
        );
    }
}

/// Render a box-style diagnostic block for a failing test.
///
/// Produces the indented `┌ ... └` box shown below each failure line.
/// Narrative order: WHERE → WITH WHAT → WHAT → VALUES → TRACE → HINT → WHY.
///
/// Returns an empty string for `TbStyle::No` and `TbStyle::Line`.
pub(crate) fn fmt_diagnostic_block(
    item: &TestItem,
    outcome: &TestOutcome,
    tb: &TbStyle,
    show_locals: bool,
    use_color: bool,
) -> String {
    if *tb == TbStyle::No || *tb == TbStyle::Line {
        return String::new();
    }

    let diag = match outcome.diagnostic() {
        Some(d) => d,
        None => return String::new(),
    };

    let mut out = String::new();

    render_where(&mut out, diag.file.as_str(), diag.lineno, use_color);
    render_params(&mut out, &item.param_values, use_color);
    render_source(&mut out, diag, show_locals, use_color);
    if let Some(cmp) = &diag.comparison {
        render_values(&mut out, cmp, use_color);
    }
    render_trace(&mut out, diag, show_locals, use_color);
    render_hint(&mut out, outcome, use_color);
    render_closing(&mut out, &diag.message, use_color);

    out
}

/// Render the VALUES section (diff lines without a section label).
fn render_values(out: &mut String, comparison: &crate::types::ComparisonDetail, use_color: bool) {
    let left = &comparison.left;
    let right = &comparison.right;
    let op = &comparison.op;
    let field_diffs = &comparison.field_diffs;

    if !op.is_empty() && !left.is_empty() && !right.is_empty() {
        let diff = fmt_diff(left, right, op, use_color);
        if !diff.is_empty() {
            for line in diff.lines() {
                let _ = writeln!(
                    out,
                    "{}{}  {}",
                    BOX.margin,
                    color_dim(BOX.vert, use_color),
                    line
                );
            }
            let _ = writeln!(out, "{}{}", BOX.margin, color_dim(BOX.vert, use_color));
        }
        if !field_diffs.is_empty() && op == "==" {
            let field_diff_output = fmt_field_diffs(field_diffs, left, use_color);
            if !field_diff_output.is_empty() {
                let _ = writeln!(out, "{}{}", BOX.margin, color_dim(BOX.vert, use_color));
                for line in field_diff_output.lines() {
                    let _ = writeln!(
                        out,
                        "{}{}  {}",
                        BOX.margin,
                        color_dim(BOX.vert, use_color),
                        line
                    );
                }
            }
        }
    } else if !op.is_empty() {
        // op set but right empty — show left only
        let _ = writeln!(
            out,
            "{}{}  {:<7}{}",
            BOX.margin,
            color_dim(BOX.vert, use_color),
            "left:",
            color_dim(left, use_color)
        );
        let _ = writeln!(out, "{}{}", BOX.margin, color_dim(BOX.vert, use_color));
    } else if !left.is_empty() {
        // Bool assert — show value
        let _ = writeln!(
            out,
            "{}{}  {:<7}{}",
            BOX.margin,
            color_dim(BOX.vert, use_color),
            "value:",
            color_dim(left, use_color)
        );
        let _ = writeln!(out, "{}{}", BOX.margin, color_dim(BOX.vert, use_color));
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use super::*;
    use crate::types::TestItem;
    use crate::types::{LineNo, MarkerSet, TestOutcome};
    use camino::Utf8PathBuf;

    #[test]
    fn test_box_constants_are_nonempty() {
        assert!(!BOX.open.is_empty());
        assert!(!BOX.vert.is_empty());
        assert!(!BOX.close.is_empty());
    }

    #[test]
    fn test_diagnostic_short_with_message() {
        let item = TestItem::builder("tests/test_foo.py", "test_add").arc();
        let outcome = TestOutcome::failed("expected 4")
            .lineno(8)
            .source("assert add(1, 2) == 4")
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        assert!(block.contains("tests/test_foo.py:8"));
        assert!(block.contains("assert add(1, 2) == 4"));
        assert!(block.contains("expected 4"));
    }

    #[test]
    fn test_diagnostic_short_no_message_no_nudge() {
        let item = TestItem::builder("tests/test_foo.py", "test_add").arc();
        let outcome = TestOutcome::failed("")
            .lineno(8)
            .source("assert add(1, 2) == 4")
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        assert!(!block.contains("add an assertion message"));
        assert!(!block.contains("hint:"));
    }

    #[test]
    fn test_diagnostic_error_shows_exception() {
        let item = TestItem::builder("tests/test_foo.py", "test_div").arc();
        let outcome = TestOutcome::error("ValueError: Cannot divide by zero")
            .lineno(22)
            .source("result = divide(10, 0)")
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        assert!(block.contains("ValueError: Cannot divide by zero"));
    }

    #[test]
    fn test_diagnostic_line_style_returns_empty() {
        let item = TestItem::builder("tests/test_foo.py", "test_add").arc();
        let outcome = TestOutcome::failed("msg")
            .lineno(8)
            .source("assert add(1, 2) == 4")
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Line, false, false);
        assert!(
            block.is_empty(),
            "--tb=line must produce no diagnostic block"
        );
    }

    #[test]
    fn test_diagnostic_no_style_is_empty() {
        let item = TestItem::builder("tests/test_foo.py", "test_add").arc();
        let outcome = TestOutcome::failed("msg")
            .lineno(8)
            .source("assert")
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::No, false, false);
        assert!(block.is_empty());
    }

    #[test]
    fn test_diagnostic_does_not_repeat_fn_name() {
        let item = TestItem::builder("tests/test_foo.py", "test_add_two_positives").arc();
        let outcome = TestOutcome::failed("")
            .lineno(8)
            .source("assert result == 42")
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        assert!(
            !block.contains("test_add_two_positives"),
            "fn name must not be repeated in the diagnostic block"
        );
    }

    #[test]
    fn test_diagnostic_shows_left_right_for_compare() {
        let item = TestItem::builder("tests/test_foo.py", "test_add").arc();
        let outcome = TestOutcome::failed("")
            .lineno(8)
            .source("assert result == 42")
            .comparison("41", "==", "42")
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        assert!(block.contains("left:"), "missing left label");
        assert!(block.contains("41"), "missing left value");
        assert!(block.contains("right:"), "missing right label");
        assert!(block.contains("42"), "missing right value");
    }

    #[test]
    fn test_diagnostic_shows_value_for_bool_assert() {
        let item = TestItem::builder("tests/test_foo.py", "test_validate").arc();
        let outcome = TestOutcome::failed("")
            .lineno(5)
            .source("assert is_valid")
            .left("False")
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        assert!(block.contains("value:"), "missing value label");
        assert!(block.contains("False"), "missing value");
        assert!(
            !block.contains("left:"),
            "should not show left for bool assert"
        );
    }

    #[test]
    fn test_diagnostic_shows_closing_message_when_present() {
        let item = TestItem::builder("tests/test_foo.py", "test_add").arc();
        let outcome = TestOutcome::failed("should be 42")
            .lineno(8)
            .source("assert result == 42")
            .comparison("41", "==", "42")
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        assert!(
            block.contains("should be 42"),
            "closing message must appear"
        );
        // Message appears on the closing └ line
        assert!(block.contains(&format!("{} should be 42", BOX.close)));
    }

    #[test]
    fn test_diagnostic_no_nudge_and_no_why_label() {
        let item = TestItem::builder("tests/test_foo.py", "test_add").arc();
        let outcome = TestOutcome::failed("")
            .lineno(8)
            .source("assert result == 42")
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        assert!(
            !block.contains("add an assertion message"),
            "nudge should be removed"
        );
        assert!(
            !block.contains("why:"),
            "why label should not appear in new format"
        );
    }

    #[test]
    fn test_diagnostic_closing_line_shows_message() {
        let item = TestItem::builder("tests/test_foo.py", "test_add").arc();
        let outcome = TestOutcome::failed("expected 4")
            .lineno(8)
            .source("assert add(1, 2) == 4")
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        assert!(
            block.contains("expected 4"),
            "message must appear on closing line"
        );
    }

    #[test]
    fn test_diagnostic_op_set_but_right_empty_suppresses_right_label() {
        let item = TestItem::builder("tests/test_foo.py", "test_op_no_rhs").arc();
        let outcome = TestOutcome::failed("")
            .lineno(3)
            .source("assert x")
            .left("42")
            .op("==")
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        assert!(block.contains("left:"), "left should appear");
        assert!(block.contains("42"), "left value should appear");
        assert!(
            !block.contains("right:"),
            "right should be suppressed when empty"
        );
    }

    #[test]
    fn test_diagnostic_shows_inline_params() {
        let item = std::sync::Arc::new(TestItem {
            node_id: crate::types::NodeId::new("tests/test_foo.py", "test_add", Some("basic")),

            fn_name: Arc::from("test_add"),
            lineno: LineNo::ZERO,
            markers: MarkerSet::new(),
            param_id: Some("basic".to_string()),
            param_values: vec![
                crate::types::ParamPair {
                    name: "x".to_string(),
                    value: "1".to_string(),
                },
                crate::types::ParamPair {
                    name: "y".to_string(),
                    value: "2".to_string(),
                },
                crate::types::ParamPair {
                    name: "expected".to_string(),
                    value: "3".to_string(),
                },
            ],
            is_async: false,
            fixture_deps: vec![],
            fixref_deps: vec![],
            arranged: vec![],
        });
        let outcome = TestOutcome::failed("")
            .lineno(8)
            .source("assert x + y == expected")
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        // Inline params format: "x = 1, y = 2, expected = 3"
        assert!(block.contains("x = 1"), "missing param x");
        assert!(block.contains("y = 2"), "missing param y");
        assert!(block.contains("expected = 3"), "missing param expected");
        // Should NOT have the old "params" header
        assert!(
            !block.contains("params"),
            "should not have old params header"
        );
    }

    #[test]
    fn test_diagnostic_params_appear_between_path_and_source() {
        let item = std::sync::Arc::new(TestItem {
            node_id: crate::types::NodeId::new("tests/test_foo.py", "test_add", Some("basic")),

            fn_name: Arc::from("test_add"),
            lineno: LineNo::ZERO,
            markers: MarkerSet::new(),
            param_id: Some("basic".to_string()),
            param_values: vec![crate::types::ParamPair {
                name: "x".to_string(),
                value: "1".to_string(),
            }],
            is_async: false,
            fixture_deps: vec![],
            fixref_deps: vec![],
            arranged: vec![],
        });
        let outcome = TestOutcome::failed("")
            .lineno(8)
            .source("assert x > 0")
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        let path_pos = block.find("tests/test_foo.py:8").unwrap();
        let params_pos = block.find("x = 1").unwrap();
        let source_pos = block.find("assert x > 0").unwrap();
        assert!(path_pos < params_pos, "params must appear after path");
        assert!(params_pos < source_pos, "params must appear before source");
    }

    #[test]
    fn test_diagnostic_no_params_block_when_param_values_empty() {
        let item = TestItem::builder("tests/test_foo.py", "test_add").arc();
        let outcome = TestOutcome::failed("")
            .lineno(8)
            .source("assert x > 0")
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        assert!(
            !block.contains("params"),
            "params block should not appear for non-parametrize"
        );
    }

    #[test]
    fn test_diagnostic_error_with_empty_file_omits_location_line() {
        let item = TestItem::builder("tests/test_foo.py", "test_bridge").arc();
        let outcome = TestOutcome::error("PyImportError: No module named 'foo'")
            .file("")
            .lineno(0)
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        assert!(
            !block.contains(":0"),
            "must not show ':0' when file is empty"
        );
        assert!(
            block.contains("PyImportError"),
            "error message must still appear in the block"
        );
    }

    #[test]
    fn test_sep_width_returns_positive_value() {
        let w = sep_width();
        assert!(w > 0, "sep_width must be positive");
        assert!(w <= 100, "sep_width must not exceed cap of 100");
    }

    #[test]
    fn test_sep_width_is_consistent() {
        assert_eq!(sep_width(), sep_width());
    }

    #[test]
    fn test_diagnostic_block_detail_shows_trace() {
        use crate::types::{Frame, TestItem, TestOutcome};

        let item = TestItem {
            node_id: crate::types::NodeId::from_raw("test_foo.py::test_check"),

            fn_name: Arc::from("test_check"),
            lineno: LineNo::new(10),
            markers: MarkerSet::new(),
            param_id: None,
            param_values: vec![],
            is_async: false,
            fixture_deps: vec![],
            fixref_deps: vec![],
            arranged: vec![],
        };
        let outcome = TestOutcome::failed("assert failed")
            .file("test_foo.py")
            .lineno(5)
            .source("assert x > 0")
            .frames(vec![
                Frame {
                    file: Utf8PathBuf::from("test_foo.py"),
                    lineno: LineNo::new(10),
                    name: "test_check".to_string(),
                    line: "helper(-1)".to_string(),
                    locals: vec![],
                },
                Frame {
                    file: Utf8PathBuf::from("test_foo.py"),
                    lineno: LineNo::new(5),
                    name: "helper".to_string(),
                    line: "assert x > 0".to_string(),
                    locals: vec![],
                },
            ])
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        // New format uses "trace" not "frames"
        assert!(block.contains("trace"), "must contain trace label");
        assert!(block.contains("test_check"), "must show caller function");
        assert!(block.contains("helper(-1)"), "must show caller source line");
        // The failure frame (lineno=5) should NOT appear in trace
        assert!(
            !block.contains("in helper"),
            "failure frame must NOT appear in trace section"
        );
    }

    #[test]
    fn test_diagnostic_block_detail_no_trace_when_only_failure_frame() {
        use crate::types::{Frame, TestItem, TestOutcome};

        let item = TestItem {
            node_id: crate::types::NodeId::from_raw("t.py::test_direct"),

            fn_name: Arc::from("test_direct"),
            lineno: LineNo::new(3),
            markers: MarkerSet::new(),
            param_id: None,
            param_values: vec![],
            is_async: false,
            fixture_deps: vec![],
            fixref_deps: vec![],
            arranged: vec![],
        };
        let outcome = TestOutcome::failed("oops")
            .file("t.py")
            .lineno(3)
            .source("assert False")
            .frames(vec![Frame {
                file: Utf8PathBuf::from("t.py"),
                lineno: LineNo::new(3),
                name: "test_direct".to_string(),
                line: "assert False".to_string(),
                locals: vec![],
            }])
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        // Only the failure frame exists, so no trace section
        assert!(
            !block.contains("trace"),
            "must NOT show trace when only failure frame"
        );
        // Source line still shown as hero
        assert!(block.contains("assert False"), "must show source line");
    }

    #[test]
    fn test_diagnostic_multiline_closing_message_stays_inside_box() {
        let item = TestItem::builder("tests/test_foo.py", "test_sub").arc();
        let outcome = TestOutcome::failed(
            "missing value:\ncollected 1 item\n\nFAILURES\nFAILED test.py::test_x",
        )
        .lineno(10)
        .source("assert x in out")
        .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        // Every non-empty continuation line must be prefixed with box chrome
        for line in block.lines().skip(1) {
            let trimmed = line.trim_start();
            if trimmed.is_empty() {
                continue;
            }
            assert!(
                trimmed.starts_with(BOX.vert)
                    || trimmed.starts_with(BOX.close)
                    || trimmed.starts_with(BOX.open),
                "line escapes diagnostic box: {line:?}"
            );
        }
        assert!(
            block.contains("collected 1 item"),
            "multi-line content must be present"
        );
        assert!(
            block.contains("FAILURES"),
            "multi-line content must be present"
        );
    }

    #[test]
    fn test_diagnostic_show_locals_renders_failure_frame_locals() {
        use crate::types::Frame;

        let item = TestItem::builder("test.py", "test_check").lineno(5).arc();
        let outcome = TestOutcome::failed("")
            .file("test.py")
            .lineno(5)
            .source("assert result == 10")
            .comparison("7", "==", "10")
            .frames(vec![Frame {
                file: Utf8PathBuf::from("test.py"),
                lineno: LineNo::new(5),
                name: "test_check".to_string(),
                line: "assert result == 10".to_string(),
                locals: vec![crate::types::LocalVar {
                    name: "result".to_string(),
                    repr: "7".to_string(),
                }],
            }])
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, true, false);
        assert!(
            block.contains("result = 7"),
            "show_locals must render failure frame locals: {block}"
        );
    }

    #[test]
    fn test_diagnostic_no_locals_without_show_locals() {
        use crate::types::Frame;

        let item = TestItem::builder("test.py", "test_check").lineno(5).arc();
        let outcome = TestOutcome::failed("")
            .file("test.py")
            .lineno(5)
            .source("assert result == 10")
            .frames(vec![Frame {
                file: Utf8PathBuf::from("test.py"),
                lineno: LineNo::new(5),
                name: "test_check".to_string(),
                line: "assert result == 10".to_string(),
                locals: vec![crate::types::LocalVar {
                    name: "result".to_string(),
                    repr: "7".to_string(),
                }],
            }])
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        assert!(
            !block.contains("result = 7"),
            "locals must NOT appear without show_locals: {block}"
        );
    }

    #[test]
    fn test_diagnostic_hint_appears_inside_box() {
        let item = TestItem::builder("tests/test_foo.py", "test_await").arc();
        let outcome = TestOutcome::error("TypeError: object X can't be used in 'await' expression")
            .file("test.py")
            .lineno(5)
            .source("await fx")
            .build();
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
        assert!(
            block.contains("hint:"),
            "hint must appear inside the box: {block}"
        );
        assert!(
            block.contains("async"),
            "hint text must be present: {block}"
        );
    }

    mod snapshot_tests {
        use super::*;
        use insta::assert_snapshot;

        #[test]
        fn failed_assertion_with_diff() {
            let item = TestItem::builder("tests/test_math.py", "test_compare")
                .lineno(15)
                .arc();
            let outcome = TestOutcome::failed("assert x == y")
                .file("tests/test_math.py")
                .lineno(15)
                .source("assert x == y")
                .build();
            let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
            assert_snapshot!(block);
        }

        #[test]
        fn error_with_frames() {
            use crate::types::Frame;

            let item = TestItem::builder("tests/test_errors.py", "test_raises")
                .lineno(10)
                .arc();
            let outcome = TestOutcome::error("ValueError: invalid input")
                .file("tests/test_errors.py")
                .lineno(10)
                .source("result = process(data)")
                .frames(vec![
                    Frame {
                        file: Utf8PathBuf::from("tests/test_errors.py"),
                        lineno: LineNo::new(10),
                        name: "test_raises".to_string(),
                        line: "result = process(data)".to_string(),
                        locals: vec![],
                    },
                    Frame {
                        file: Utf8PathBuf::from("src/processor.py"),
                        lineno: LineNo::new(42),
                        name: "process".to_string(),
                        line: "raise ValueError(\"invalid input\")".to_string(),
                        locals: vec![],
                    },
                ])
                .build();
            let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
            assert_snapshot!(block);
        }

        #[test]
        fn failed_with_left_right_op() {
            let item = TestItem::builder("tests/test_values.py", "test_equality")
                .lineno(7)
                .arc();
            let outcome = TestOutcome::failed("")
                .file("tests/test_values.py")
                .lineno(7)
                .source("assert result == expected")
                .comparison("1", "==", "2")
                .build();
            let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false, false);
            assert_snapshot!(block);
        }

        #[test]
        fn tb_no_returns_empty() {
            let item = TestItem::builder("tests/test_mod.py", "test_something")
                .lineno(5)
                .arc();
            let outcome = TestOutcome::failed("should pass")
                .file("tests/test_mod.py")
                .lineno(5)
                .source("assert x")
                .build();
            let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::No, false, false);
            assert_snapshot!(block);
        }
    }
}
