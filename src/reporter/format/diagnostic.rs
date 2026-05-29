use std::fmt::Write as _;
use std::sync::OnceLock;

use crate::config::TbStyle;
use crate::types::{TestItem, TestOutcome};

use super::fmt_diff;
use crate::reporter::colors::{color_bold_white, color_dim, color_dim_cyan};

const BOX_TOP_LEFT: &str = "┌─";
const BOX_VERT: &str = "│";
const BOX_BOT_LEFT: &str = "└─";
const BOX_BRANCH: &str = "├─";

const INTERNAL_PREFIXES: &[&str] = &["oxitest/_bridge/", "oxitest/_builtins/", "oxitest/plugin"];

fn is_internal_frame(frame: &crate::types::Frame) -> bool {
    INTERNAL_PREFIXES
        .iter()
        .any(|prefix| frame.file.as_str().contains(prefix))
}

fn filter_frames(frames: &[crate::types::Frame]) -> Vec<&crate::types::Frame> {
    let user_frames: Vec<&crate::types::Frame> =
        frames.iter().filter(|f| !is_internal_frame(f)).collect();
    if user_frames.is_empty() && !frames.is_empty() {
        // Always show at least the last frame (where the error occurred)
        vec![frames.last().unwrap()]
    } else {
        user_frames
    }
}

/// Returns the terminal width for separator lines, capped at 100 columns.
///
/// Queried once and cached; subsequent calls return the same value. The 100-column
/// cap keeps output readable on wide terminals.
pub(crate) fn sep_width() -> usize {
    static WIDTH: OnceLock<usize> = OnceLock::new();
    *WIDTH.get_or_init(|| {
        let (_, cols) = console::Term::stdout().size();
        (cols as usize).min(100)
    })
}

/// Returns the separator string between parametrize case identifiers.
///
/// Color mode gets a middle dot (`·`) for visual polish; plain mode gets a hyphen
/// to stay safe in environments that don't support Unicode.
pub(crate) fn case_sep(use_color: bool) -> &'static str {
    if use_color {
        " · "
    } else {
        " - "
    }
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

fn render_label_block(label_items: &[String], use_color: bool) -> String {
    if label_items.is_empty() {
        return format!("        {}\n", color_dim(BOX_BOT_LEFT, use_color));
    }
    let n = label_items.len();
    let mut out = String::new();
    for (i, item) in label_items.iter().enumerate() {
        let is_last = i == n - 1;
        let prefix = if is_last { BOX_BOT_LEFT } else { BOX_VERT };
        let spacer = if is_last { " " } else { "  " };

        let mut lines = item.lines();
        // First line gets the normal prefix (└─ or │)
        if let Some(first) = lines.next() {
            let _ = writeln!(
                out,
                "        {}{}{}",
                color_dim(prefix, use_color),
                spacer,
                first
            );
        }
        // Continuation lines get │ prefix with padding to align under the value
        for cont in lines {
            let _ = writeln!(out, "        {}  {}", color_dim(BOX_VERT, use_color), cont);
        }
    }
    out
}

/// Render the `├─ params` block showing parametrize key=value pairs.
///
/// Returns an empty string when `params` is empty.
fn render_params_section(params: &[(String, String)], use_color: bool) -> String {
    if params.is_empty() {
        return String::new();
    }
    let mut out = String::new();
    let _ = writeln!(
        out,
        "        {}  {}",
        color_dim(BOX_BRANCH, use_color),
        color_dim("params", use_color)
    );
    let key_width = params.iter().map(|(k, _)| k.len()).max().unwrap_or(0);
    for (k, v) in params {
        let _ = writeln!(
            out,
            "        {}  {:<width$} = {}",
            color_dim(BOX_VERT, use_color),
            k,
            v,
            width = key_width
        );
    }
    let _ = writeln!(out, "        {}", color_dim(BOX_VERT, use_color));
    out
}

/// Render the `├─ frames` block showing traceback frames.
///
/// Returns an empty string when `frames` is empty.
fn render_frames_section(frames: &[&crate::types::Frame], use_color: bool) -> String {
    if frames.is_empty() {
        return String::new();
    }
    let mut out = String::new();
    let _ = writeln!(
        out,
        "        {}  {}",
        color_dim(BOX_BRANCH, use_color),
        color_dim("frames", use_color)
    );
    for f in frames {
        let _ = writeln!(
            out,
            "        {}    {}:{}  {}",
            color_dim(BOX_VERT, use_color),
            f.file,
            f.lineno,
            color_dim(&f.name, use_color)
        );
        if !f.line.is_empty() {
            let _ = writeln!(
                out,
                "        {}      {}",
                color_dim(BOX_VERT, use_color),
                color_bold_white(&f.line, use_color)
            );
        }
    }
    let _ = writeln!(out, "        {}", color_dim(BOX_VERT, use_color));
    out
}

/// Render a box-style diagnostic block for a failing test.
///
/// Produces the indented `┌─ ... └─` box shown below each failure, including the
/// assertion source line, left/right diff (for `Failed`), traceback frames, and
/// error message. Returns an empty string for `TbStyle::No` and `TbStyle::Line`
/// (those styles suppress the block entirely).
pub(crate) fn fmt_diagnostic_block(
    item: &TestItem,
    outcome: &TestOutcome,
    tb: &TbStyle,
    use_color: bool,
) -> String {
    if *tb == TbStyle::No || *tb == TbStyle::Line {
        return String::new();
    }

    let parts = match outcome.diagnostic_parts() {
        Some(p) => p,
        None => return String::new(),
    };

    // ── Build the outcome-specific "extra" block ────────────────────
    let is_error = matches!(outcome, TestOutcome::Error { .. });
    let extra = build_extra_block(
        parts.message,
        parts.left,
        parts.right,
        parts.op,
        is_error,
        use_color,
    );

    // ── Assemble the diagnostic box ─────────────────────────────────
    let mut out = String::new();

    // Location
    if !parts.file.is_empty() {
        let loc = format!("{}:{}", parts.file, parts.lineno);
        let _ = writeln!(
            out,
            "        {} {}",
            color_dim(BOX_TOP_LEFT, use_color),
            color_dim_cyan(&loc, use_color)
        );
        let _ = writeln!(out, "        {}", color_dim(BOX_VERT, use_color));
    }

    // Params
    out.push_str(&render_params_section(&item.param_values, use_color));

    // Frames
    let visible_frames = filter_frames(parts.frames);
    out.push_str(&render_frames_section(&visible_frames, use_color));

    // Source-line fallback (when no frames visible but location is known)
    if visible_frames.is_empty() && !parts.file.is_empty() {
        let lineno_padded = format!("{:>4}", parts.lineno);
        let _ = writeln!(
            out,
            "        {}   {} {} {}",
            color_dim(BOX_VERT, use_color),
            color_dim(&lineno_padded, use_color),
            color_dim(BOX_VERT, use_color),
            color_bold_white(&format!("   {}", parts.source_line), use_color)
        );
        let _ = writeln!(out, "        {}", color_dim(BOX_VERT, use_color));
    }

    // Extra (diff/labels/hint)
    out.push_str(&extra);

    // Suggestion
    if let Some(hint) = super::suggestions::suggest_fix(outcome, use_color) {
        out.push_str(&hint);
        out.push('\n');
    }

    out
}

/// Build the outcome-specific extra block (diff section + label items for Failed,
/// error hint for Error).
fn build_extra_block(
    message: &str,
    left: &str,
    right: &str,
    op: &str,
    is_error: bool,
    use_color: bool,
) -> String {
    // Error outcomes produce a simple hint line with the exception message.
    if is_error {
        return format!(
            "        {} {}\n",
            color_dim(BOX_BOT_LEFT, use_color),
            color_dim(message, use_color)
        );
    }

    let mut label_items: Vec<String> = Vec::new();
    let mut diff_section = String::new();

    if !op.is_empty() && !left.is_empty() && !right.is_empty() {
        let diff = fmt_diff(left, right, op, use_color);
        if !diff.is_empty() {
            let _ = writeln!(
                diff_section,
                "        {}  {}",
                color_dim(BOX_BRANCH, use_color),
                color_dim("diff", use_color)
            );
            for line in diff.lines() {
                let _ = writeln!(
                    diff_section,
                    "        {}  {}",
                    color_dim(BOX_VERT, use_color),
                    line
                );
            }
        }
    } else if !op.is_empty() {
        label_items.push(format!("{:<7}{}", "left:", color_dim(left, use_color)));
    } else if !left.is_empty() {
        label_items.push(format!("{:<7}{}", "value:", color_dim(left, use_color)));
    }

    if !message.is_empty() {
        label_items.push(format!("{:<7}{}", "why:", color_dim(message, use_color)));
    }

    format!(
        "{}{}",
        diff_section,
        render_label_block(&label_items, use_color)
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reporter::test_helpers::{make_error, make_failed, make_item, make_item_at};
    use crate::types::{LineNo, TestOutcome};
    use camino::Utf8PathBuf;

    #[test]
    fn test_box_constants_are_nonempty() {
        assert!(!BOX_TOP_LEFT.is_empty());
        assert!(!BOX_VERT.is_empty());
        assert!(!BOX_BOT_LEFT.is_empty());
        assert!(!BOX_BRANCH.is_empty());
    }

    #[test]
    fn test_diagnostic_short_with_message() {
        let item = make_item("test_add");
        let outcome = make_failed(
            "expected 4",
            "tests/test_foo.py",
            8,
            "assert add(1, 2) == 4",
        );
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        assert!(block.contains("tests/test_foo.py:8"));
        assert!(block.contains("assert add(1, 2) == 4"));
        assert!(block.contains("why:"));
        assert!(block.contains("expected 4"));
    }

    #[test]
    fn test_diagnostic_short_no_message_no_nudge() {
        let item = make_item("test_add");
        let outcome = make_failed("", "tests/test_foo.py", 8, "assert add(1, 2) == 4");
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        assert!(!block.contains("add an assertion message"));
        assert!(!block.contains("hint:"));
    }

    #[test]
    fn test_diagnostic_error_shows_exception() {
        let item = make_item("test_div");
        let outcome = make_error(
            "ValueError: Cannot divide by zero",
            "tests/test_foo.py",
            22,
            "result = divide(10, 0)",
        );
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        assert!(block.contains("ValueError: Cannot divide by zero"));
    }

    #[test]
    fn test_diagnostic_line_style_returns_empty() {
        let item = make_item("test_add");
        let outcome = make_failed("msg", "tests/test_foo.py", 8, "assert add(1, 2) == 4");
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Line, false);
        assert!(
            block.is_empty(),
            "--tb=line must produce no diagnostic block"
        );
    }

    #[test]
    fn test_diagnostic_no_style_is_empty() {
        let item = make_item("test_add");
        let outcome = make_failed("msg", "tests/test_foo.py", 8, "assert");
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::No, false);
        assert!(block.is_empty());
    }

    #[test]
    fn test_diagnostic_does_not_repeat_fn_name() {
        // fn_name is shown (colored) on the reporter line; the block must not repeat it
        let item = make_item("test_add_two_positives");
        let outcome = make_failed("", "tests/test_foo.py", 8, "assert result == 42");
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        assert!(
            !block.contains("test_add_two_positives"),
            "fn name must not be repeated in the diagnostic block"
        );
    }

    #[test]
    fn test_diagnostic_shows_left_right_for_compare() {
        let item = make_item("test_add");
        let outcome = TestOutcome::Failed {
            message: String::new(),
            file: Utf8PathBuf::from("tests/test_foo.py"),
            lineno: LineNo::new(8),
            source_line: "assert result == 42".to_string(),
            left: "41".to_string(),
            right: "42".to_string(),
            op: "==".to_string(),
            frames: vec![],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        assert!(block.contains("left:"), "missing left label");
        assert!(block.contains("41"), "missing left value");
        assert!(block.contains("right:"), "missing right label");
        assert!(block.contains("42"), "missing right value");
    }

    #[test]
    fn test_diagnostic_shows_value_for_bool_assert() {
        let item = make_item("test_validate");
        let outcome = TestOutcome::Failed {
            message: String::new(),
            file: Utf8PathBuf::from("tests/test_foo.py"),
            lineno: LineNo::new(5),
            source_line: "assert is_valid".to_string(),
            left: "False".to_string(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        assert!(block.contains("value:"), "missing value label");
        assert!(block.contains("False"), "missing value");
        assert!(
            !block.contains("left:"),
            "should not show left for bool assert"
        );
    }

    #[test]
    fn test_diagnostic_shows_why_when_message_present() {
        let item = make_item("test_add");
        let outcome = TestOutcome::Failed {
            message: "should be 42".to_string(),
            file: Utf8PathBuf::from("tests/test_foo.py"),
            lineno: LineNo::new(8),
            source_line: "assert result == 42".to_string(),
            left: "41".to_string(),
            right: "42".to_string(),
            op: "==".to_string(),
            frames: vec![],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        assert!(block.contains("why:"), "missing why label");
        assert!(block.contains("should be 42"), "missing why value");
    }

    #[test]
    fn test_diagnostic_no_nudge_and_no_hint_label() {
        let item = make_item("test_add");
        let outcome = make_failed("", "tests/test_foo.py", 8, "assert result == 42");
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        assert!(
            !block.contains("add an assertion message"),
            "nudge should be removed"
        );
        assert!(
            !block.contains("hint:"),
            "hint label should be replaced with why"
        );
    }

    #[test]
    fn test_diagnostic_why_replaces_hint_label() {
        let item = make_item("test_add");
        let outcome = make_failed(
            "expected 4",
            "tests/test_foo.py",
            8,
            "assert add(1, 2) == 4",
        );
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        assert!(block.contains("why:"), "should use why label");
        assert!(block.contains("expected 4"), "should show message");
        assert!(!block.contains("hint:"), "old hint label must be gone");
    }

    #[test]
    fn test_diagnostic_op_set_but_right_empty_suppresses_right_label() {
        let item = make_item("test_op_no_rhs");
        let outcome = TestOutcome::Failed {
            message: String::new(),
            file: Utf8PathBuf::from("tests/test_foo.py"),
            lineno: LineNo::new(3),
            source_line: "assert x".to_string(),
            left: "42".to_string(),
            right: String::new(),
            op: "==".to_string(),
            frames: vec![],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        assert!(block.contains("left:"), "left should appear");
        assert!(block.contains("42"), "left value should appear");
        assert!(
            !block.contains("right:"),
            "right should be suppressed when empty"
        );
    }

    #[test]
    fn test_diagnostic_shows_params_block_when_param_values_present() {
        let item = std::sync::Arc::new(TestItem {
            node_id: crate::types::NodeId::new("tests/test_foo.py", "test_add", Some("basic")),
            module_path: camino::Utf8PathBuf::from("tests/test_foo.py"),
            fn_name: "test_add".to_string(),
            lineno: LineNo::ZERO,
            markers: vec![],
            param_id: Some("basic".to_string()),
            param_values: vec![
                ("x".to_string(), "1".to_string()),
                ("y".to_string(), "2".to_string()),
                ("expected".to_string(), "3".to_string()),
            ],
            is_async: false,
            fixture_names: vec![],
        });
        let outcome = make_failed("", "tests/test_foo.py", 8, "assert x + y == expected");
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        assert!(block.contains("params"), "missing params header");
        assert!(block.contains("x"), "missing param key x");
        assert!(block.contains("1"), "missing param value 1");
        assert!(block.contains("expected"), "missing param key expected");
        assert!(block.contains("3"), "missing param value 3");
    }

    #[test]
    fn test_diagnostic_params_appear_between_path_and_source() {
        let item = std::sync::Arc::new(TestItem {
            node_id: crate::types::NodeId::new("tests/test_foo.py", "test_add", Some("basic")),
            module_path: camino::Utf8PathBuf::from("tests/test_foo.py"),
            fn_name: "test_add".to_string(),
            lineno: LineNo::ZERO,
            markers: vec![],
            param_id: Some("basic".to_string()),
            param_values: vec![("x".to_string(), "1".to_string())],
            is_async: false,
            fixture_names: vec![],
        });
        let outcome = make_failed("", "tests/test_foo.py", 8, "assert x > 0");
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        let path_pos = block.find("tests/test_foo.py:8").unwrap();
        let params_pos = block.find("params").unwrap();
        let source_pos = block.find("assert x > 0").unwrap();
        assert!(path_pos < params_pos, "params must appear after path");
        assert!(params_pos < source_pos, "params must appear before source");
    }

    #[test]
    fn test_diagnostic_no_params_block_when_param_values_empty() {
        let item = make_item("test_add");
        let outcome = make_failed("", "tests/test_foo.py", 8, "assert x > 0");
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        assert!(
            !block.contains("params"),
            "params block should not appear for non-parametrize"
        );
    }

    #[test]
    fn test_diagnostic_error_with_empty_file_omits_location_line() {
        // Bridge-level errors have file="" and lineno=0 — the renderer
        // must not produce a meaningless ":0" location line.
        let item = make_item("test_bridge");
        let outcome = TestOutcome::Error {
            message: "PyImportError: No module named 'foo'".to_string(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            frames: vec![],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
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
    fn test_diagnostic_block_detail_shows_frames() {
        use crate::types::{Frame, TestItem, TestOutcome};

        let item = TestItem {
            node_id: crate::types::NodeId::from_raw("test_foo.py::test_check"),
            module_path: "test_foo.py".into(),
            fn_name: "test_check".to_string(),
            lineno: LineNo::new(10),
            markers: vec![],
            param_id: None,
            param_values: vec![],
            is_async: false,
            fixture_names: vec![],
        };
        let outcome = TestOutcome::Failed {
            message: "assert failed".to_string(),
            file: Utf8PathBuf::from("test_foo.py"),
            lineno: LineNo::new(5),
            source_line: "assert x > 0".to_string(),
            left: "".to_string(),
            right: "".to_string(),
            op: "".to_string(),
            frames: vec![
                Frame {
                    file: Utf8PathBuf::from("test_foo.py"),
                    lineno: LineNo::new(10),
                    name: "test_check".to_string(),
                    line: "helper(-1)".to_string(),
                },
                Frame {
                    file: Utf8PathBuf::from("test_foo.py"),
                    lineno: LineNo::new(5),
                    name: "helper".to_string(),
                    line: "assert x > 0".to_string(),
                },
            ],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        assert!(block.contains("frames"), "must contain frames label");
        assert!(block.contains("test_check"), "must show caller function");
        assert!(block.contains("helper"), "must show callee function");
        assert!(block.contains("helper(-1)"), "must show caller source line");
        // In Detail mode with frames, numbered source line should NOT appear
        assert!(
            !block.contains("   5 │"),
            "must NOT show numbered source line when frames present"
        );
    }

    #[test]
    fn test_diagnostic_block_detail_empty_frames_falls_back_to_source_line() {
        use crate::types::{TestItem, TestOutcome};

        let item = TestItem {
            node_id: crate::types::NodeId::from_raw("t.py::test_direct"),
            module_path: "t.py".into(),
            fn_name: "test_direct".to_string(),
            lineno: LineNo::new(3),
            markers: vec![],
            param_id: None,
            param_values: vec![],
            is_async: false,
            fixture_names: vec![],
        };
        let outcome = TestOutcome::Failed {
            message: "oops".to_string(),
            file: Utf8PathBuf::from("t.py"),
            lineno: LineNo::new(3),
            source_line: "assert False".to_string(),
            left: "".to_string(),
            right: "".to_string(),
            op: "".to_string(),
            frames: vec![],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        // Empty frames → falls back to showing numbered source line
        assert!(
            block.contains("   3 │"),
            "must show numbered source line when no frames"
        );
        assert!(!block.contains("frames"), "must NOT show frames section");
    }

    #[test]
    fn test_diagnostic_short_hides_internal_frames() {
        use crate::types::Frame;

        let item = make_item_at("test_user_code", "tests/test_app.py", 10);
        let outcome = TestOutcome::Failed {
            message: "assert x == 1".to_string(),
            file: Utf8PathBuf::from("tests/test_app.py"),
            lineno: LineNo::new(10),
            source_line: "assert x == 1".to_string(),
            left: "0".to_string(),
            right: "1".to_string(),
            op: "==".to_string(),
            frames: vec![
                Frame {
                    file: Utf8PathBuf::from("tests/test_app.py"),
                    lineno: LineNo::new(10),
                    name: "test_user_code".to_string(),
                    line: "result = helper()".to_string(),
                },
                Frame {
                    file: Utf8PathBuf::from("oxitest/_bridge/executor.py"),
                    lineno: LineNo::new(55),
                    name: "_run_base".to_string(),
                    line: "fn()".to_string(),
                },
                Frame {
                    file: Utf8PathBuf::from("oxitest/_bridge/_middleware.py"),
                    lineno: LineNo::new(30),
                    name: "_compose".to_string(),
                    line: "wrapper(fn)".to_string(),
                },
            ],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        assert!(
            block.contains("test_app.py"),
            "user frame must appear: {block}"
        );
        assert!(
            !block.contains("executor.py"),
            "internal frame must be hidden: {block}"
        );
        assert!(
            !block.contains("_middleware.py"),
            "internal frame must be hidden: {block}"
        );
    }

    #[test]
    fn test_diagnostic_detail_hides_internal_frames_keeps_user() {
        use crate::types::Frame;

        let item = make_item_at("test_user_code", "tests/test_app.py", 10);
        let outcome = TestOutcome::Failed {
            message: "assert x == 1".to_string(),
            file: Utf8PathBuf::from("tests/test_app.py"),
            lineno: LineNo::new(10),
            source_line: "assert x == 1".to_string(),
            left: "0".to_string(),
            right: "1".to_string(),
            op: "==".to_string(),
            frames: vec![
                Frame {
                    file: Utf8PathBuf::from("tests/test_app.py"),
                    lineno: LineNo::new(10),
                    name: "test_user_code".to_string(),
                    line: "result = helper()".to_string(),
                },
                Frame {
                    file: Utf8PathBuf::from("oxitest/_bridge/executor.py"),
                    lineno: LineNo::new(55),
                    name: "_run_base".to_string(),
                    line: "fn()".to_string(),
                },
            ],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        assert!(
            block.contains("test_app.py"),
            "detail mode must show user frames: {block}"
        );
        assert!(
            !block.contains("executor.py"),
            "detail mode must hide internal frames: {block}"
        );
    }

    #[test]
    fn test_filter_frames_fallback_to_last() {
        use crate::types::Frame;

        // When ALL frames are internal, still show the last one
        let frames = vec![Frame {
            file: Utf8PathBuf::from("oxitest/_bridge/executor.py"),
            lineno: LineNo::new(10),
            name: "_run_base".to_string(),
            line: "fn()".to_string(),
        }];
        let filtered = filter_frames(&frames);
        assert_eq!(filtered.len(), 1, "should show at least the last frame");
    }

    #[test]
    fn test_diagnostic_multiline_why_stays_inside_box() {
        let item = make_item("test_sub");
        let outcome = make_failed(
            "missing value:\ncollected 1 item\n\nFAILURES\nFAILED test.py::test_x",
            "tests/test_foo.py",
            10,
            "assert x in out",
        );
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
        // Every non-empty continuation line must be prefixed with BOX_VERT
        for line in block.lines().skip(1) {
            let trimmed = line.trim_start();
            if trimmed.is_empty() {
                continue;
            }
            assert!(
                trimmed.starts_with(BOX_VERT)
                    || trimmed.starts_with(BOX_BOT_LEFT)
                    || trimmed.starts_with(BOX_BRANCH)
                    || trimmed.starts_with(BOX_TOP_LEFT),
                "line escapes diagnostic box: {line:?}"
            );
        }
        // The multi-line content must still appear
        assert!(
            block.contains("collected 1 item"),
            "multi-line content must be present"
        );
        assert!(
            block.contains("FAILURES"),
            "multi-line content must be present"
        );
    }

    mod snapshot_tests {
        use super::*;
        use insta::assert_snapshot;

        #[test]
        fn failed_assertion_with_diff() {
            let item = make_item_at("test_compare", "tests/test_math.py", 15);
            let outcome = make_failed("assert x == y", "tests/test_math.py", 15, "assert x == y");
            let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
            assert_snapshot!(block);
        }

        #[test]
        fn error_with_frames() {
            use crate::types::Frame;

            let item = make_item_at("test_raises", "tests/test_errors.py", 10);
            let outcome = TestOutcome::Error {
                message: "ValueError: invalid input".to_string(),
                file: Utf8PathBuf::from("tests/test_errors.py"),
                lineno: LineNo::new(10),
                source_line: "result = process(data)".to_string(),
                frames: vec![
                    Frame {
                        file: Utf8PathBuf::from("tests/test_errors.py"),
                        lineno: LineNo::new(10),
                        name: "test_raises".to_string(),
                        line: "result = process(data)".to_string(),
                    },
                    Frame {
                        file: Utf8PathBuf::from("src/processor.py"),
                        lineno: LineNo::new(42),
                        name: "process".to_string(),
                        line: "raise ValueError(\"invalid input\")".to_string(),
                    },
                ],
            };
            let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
            assert_snapshot!(block);
        }

        #[test]
        fn failed_with_left_right_op() {
            let item = make_item_at("test_equality", "tests/test_values.py", 7);
            let outcome = TestOutcome::Failed {
                message: String::new(),
                file: Utf8PathBuf::from("tests/test_values.py"),
                lineno: LineNo::new(7),
                source_line: "assert result == expected".to_string(),
                left: "1".to_string(),
                right: "2".to_string(),
                op: "==".to_string(),
                frames: vec![],
            };
            let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Detail, false);
            assert_snapshot!(block);
        }

        #[test]
        fn tb_no_returns_empty() {
            let item = make_item_at("test_something", "tests/test_mod.py", 5);
            let outcome = make_failed("should pass", "tests/test_mod.py", 5, "assert x");
            let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::No, false);
            assert_snapshot!(block);
        }
    }
}
