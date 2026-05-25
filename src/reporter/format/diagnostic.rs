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
        .any(|prefix| frame.file.contains(prefix))
}

fn filter_frames<'a>(
    frames: &'a [crate::types::Frame],
    tb: &TbStyle,
) -> Vec<&'a crate::types::Frame> {
    match tb {
        TbStyle::Long => frames.iter().collect(),
        _ => {
            let user_frames: Vec<&crate::types::Frame> =
                frames.iter().filter(|f| !is_internal_frame(f)).collect();
            if user_frames.is_empty() && !frames.is_empty() {
                // Always show at least the last frame (where the error occurred)
                vec![frames.last().unwrap()]
            } else {
                user_frames
            }
        }
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
            out.push_str(&format!(
                "        {}{}{}\n",
                color_dim(prefix, use_color),
                spacer,
                first
            ));
        }
        // Continuation lines get │ prefix with padding to align under the value
        for cont in lines {
            out.push_str(&format!(
                "        {}  {}\n",
                color_dim(BOX_VERT, use_color),
                cont
            ));
        }
    }
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

    let (file, lineno, source_line, extra, frames) = match outcome {
        TestOutcome::Failed {
            message,
            file,
            lineno,
            source_line,
            left,
            right,
            op,
            frames,
        } => {
            let mut label_items: Vec<String> = Vec::new();
            let mut diff_section = String::new();

            if !op.is_empty() && !left.is_empty() && !right.is_empty() {
                // Use colorized diff instead of plain left/right labels.
                let diff = fmt_diff(left, right, op, use_color);
                if !diff.is_empty() {
                    diff_section.push_str(&format!(
                        "        {}  {}\n",
                        color_dim(BOX_BRANCH, use_color),
                        color_dim("diff", use_color),
                    ));
                    for line in diff.lines() {
                        diff_section.push_str(&format!(
                            "        {}  {}\n",
                            color_dim(BOX_VERT, use_color),
                            line
                        ));
                    }
                }
            } else if !op.is_empty() {
                // op set but right is empty — show left only
                label_items.push(format!("{:<7}{}", "left:", color_dim(left, use_color)));
            } else if !left.is_empty() {
                label_items.push(format!("{:<7}{}", "value:", color_dim(left, use_color)));
            }

            if !message.is_empty() {
                label_items.push(format!("{:<7}{}", "why:", color_dim(message, use_color)));
            }

            let extra = format!(
                "{}{}",
                diff_section,
                render_label_block(&label_items, use_color)
            );
            (file.as_str(), *lineno, source_line.as_str(), extra, frames)
        }
        TestOutcome::Error {
            message,
            file,
            lineno,
            source_line,
            frames,
        } => {
            let hint = format!(
                "        {} {}\n",
                color_dim(BOX_BOT_LEFT, use_color),
                color_dim(message, use_color)
            );
            (file.as_str(), *lineno, source_line.as_str(), hint, frames)
        }
        _ => return String::new(),
    };

    let mut out = String::new();

    // WHERE: location — file:path uses dim cyan (secondary context)
    if !file.is_empty() {
        let loc = format!("{}:{}", file, lineno);
        out.push_str(&format!(
            "        {} {}\n",
            color_dim(BOX_TOP_LEFT, use_color),
            color_dim_cyan(&loc, use_color)
        ));
        out.push_str(&format!("        {}\n", color_dim(BOX_VERT, use_color)));
    }

    if !item.param_values.is_empty() {
        out.push_str(&format!(
            "        {}  {}\n",
            color_dim(BOX_BRANCH, use_color),
            color_dim("params", use_color)
        ));
        let key_width = item
            .param_values
            .iter()
            .map(|(k, _)| k.len())
            .max()
            .unwrap_or(0);
        for (k, v) in &item.param_values {
            out.push_str(&format!(
                "        {}  {:<width$} = {}\n",
                color_dim(BOX_VERT, use_color),
                k,
                v,
                width = key_width
            ));
        }
        out.push_str(&format!("        {}\n", color_dim(BOX_VERT, use_color)));
    }

    let visible_frames = filter_frames(frames, tb);

    if !visible_frames.is_empty() {
        out.push_str(&format!(
            "        {}  {}\n",
            color_dim(BOX_BRANCH, use_color),
            color_dim("frames", use_color)
        ));
        for f in &visible_frames {
            out.push_str(&format!(
                "        {}    {}:{}  {}\n",
                color_dim(BOX_VERT, use_color),
                f.file,
                f.lineno,
                color_dim(&f.name, use_color)
            ));
            if !f.line.is_empty() {
                out.push_str(&format!(
                    "        {}      {}\n",
                    color_dim(BOX_VERT, use_color),
                    color_bold_white(&f.line, use_color)
                ));
            }
        }
        out.push_str(&format!("        {}\n", color_dim(BOX_VERT, use_color)));
    }

    if visible_frames.is_empty() && !file.is_empty() {
        let lineno_padded = format!("{:>4}", lineno);
        out.push_str(&format!(
            "        {}   {} {} {}\n",
            color_dim(BOX_VERT, use_color),
            color_dim(&lineno_padded, use_color),
            color_dim(BOX_VERT, use_color),
            color_bold_white(&format!("   {}", source_line), use_color)
        ));
        out.push_str(&format!("        {}\n", color_dim(BOX_VERT, use_color)));
    }

    out.push_str(&extra);

    if let Some(hint) = super::suggestions::suggest_fix(outcome, use_color) {
        out.push_str(&hint);
        out.push('\n');
    }

    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reporter::test_helpers::{make_error, make_failed, make_item, make_item_at};
    use crate::types::TestOutcome;

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
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
        assert!(block.contains("tests/test_foo.py:8"));
        assert!(block.contains("assert add(1, 2) == 4"));
        assert!(block.contains("why:"));
        assert!(block.contains("expected 4"));
    }

    #[test]
    fn test_diagnostic_short_no_message_no_nudge() {
        let item = make_item("test_add");
        let outcome = make_failed("", "tests/test_foo.py", 8, "assert add(1, 2) == 4");
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
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
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
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
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
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
            file: "tests/test_foo.py".to_string(),
            lineno: 8,
            source_line: "assert result == 42".to_string(),
            left: "41".to_string(),
            right: "42".to_string(),
            op: "==".to_string(),
            frames: vec![],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
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
            file: "tests/test_foo.py".to_string(),
            lineno: 5,
            source_line: "assert is_valid".to_string(),
            left: "False".to_string(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
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
            file: "tests/test_foo.py".to_string(),
            lineno: 8,
            source_line: "assert result == 42".to_string(),
            left: "41".to_string(),
            right: "42".to_string(),
            op: "==".to_string(),
            frames: vec![],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
        assert!(block.contains("why:"), "missing why label");
        assert!(block.contains("should be 42"), "missing why value");
    }

    #[test]
    fn test_diagnostic_no_nudge_and_no_hint_label() {
        let item = make_item("test_add");
        let outcome = make_failed("", "tests/test_foo.py", 8, "assert result == 42");
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
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
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
        assert!(block.contains("why:"), "should use why label");
        assert!(block.contains("expected 4"), "should show message");
        assert!(!block.contains("hint:"), "old hint label must be gone");
    }

    #[test]
    fn test_diagnostic_op_set_but_right_empty_suppresses_right_label() {
        let item = make_item("test_op_no_rhs");
        let outcome = TestOutcome::Failed {
            message: String::new(),
            file: "tests/test_foo.py".to_string(),
            lineno: 3,
            source_line: "assert x".to_string(),
            left: "42".to_string(),
            right: String::new(),
            op: "==".to_string(),
            frames: vec![],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
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
            lineno: 0,
            markers: vec![],
            param_id: Some("basic".to_string()),
            param_values: vec![
                ("x".to_string(), "1".to_string()),
                ("y".to_string(), "2".to_string()),
                ("expected".to_string(), "3".to_string()),
            ],
            is_async: false,
        });
        let outcome = make_failed("", "tests/test_foo.py", 8, "assert x + y == expected");
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
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
            lineno: 0,
            markers: vec![],
            param_id: Some("basic".to_string()),
            param_values: vec![("x".to_string(), "1".to_string())],
            is_async: false,
        });
        let outcome = make_failed("", "tests/test_foo.py", 8, "assert x > 0");
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
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
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
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
            file: String::new(),
            lineno: 0,
            source_line: String::new(),
            frames: vec![],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
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
    fn test_diagnostic_block_long_shows_frames() {
        use crate::types::{Frame, TestItem, TestOutcome};

        let item = TestItem {
            node_id: crate::types::NodeId::from_raw("test_foo.py::test_check"),
            module_path: "test_foo.py".into(),
            fn_name: "test_check".to_string(),
            lineno: 10,
            markers: vec![],
            param_id: None,
            param_values: vec![],
            is_async: false,
        };
        let outcome = TestOutcome::Failed {
            message: "assert failed".to_string(),
            file: "test_foo.py".to_string(),
            lineno: 5,
            source_line: "assert x > 0".to_string(),
            left: "".to_string(),
            right: "".to_string(),
            op: "".to_string(),
            frames: vec![
                Frame {
                    file: "test_foo.py".to_string(),
                    lineno: 10,
                    name: "test_check".to_string(),
                    line: "helper(-1)".to_string(),
                },
                Frame {
                    file: "test_foo.py".to_string(),
                    lineno: 5,
                    name: "helper".to_string(),
                    line: "assert x > 0".to_string(),
                },
            ],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Long, false);
        assert!(block.contains("frames"), "must contain frames label");
        assert!(block.contains("test_check"), "must show caller function");
        assert!(block.contains("helper"), "must show callee function");
        assert!(block.contains("helper(-1)"), "must show caller source line");
        // In Long mode with frames, numbered source line should NOT appear
        assert!(
            !block.contains("   5 │"),
            "must NOT show numbered source line when frames present"
        );
    }

    #[test]
    fn test_diagnostic_block_long_empty_frames_falls_back_to_short() {
        use crate::types::{TestItem, TestOutcome};

        let item = TestItem {
            node_id: crate::types::NodeId::from_raw("t.py::test_direct"),
            module_path: "t.py".into(),
            fn_name: "test_direct".to_string(),
            lineno: 3,
            markers: vec![],
            param_id: None,
            param_values: vec![],
            is_async: false,
        };
        let outcome = TestOutcome::Failed {
            message: "oops".to_string(),
            file: "t.py".to_string(),
            lineno: 3,
            source_line: "assert False".to_string(),
            left: "".to_string(),
            right: "".to_string(),
            op: "".to_string(),
            frames: vec![],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Long, false);
        // Empty frames → falls back to showing source line like Short
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
            file: "tests/test_app.py".to_string(),
            lineno: 10,
            source_line: "assert x == 1".to_string(),
            left: "0".to_string(),
            right: "1".to_string(),
            op: "==".to_string(),
            frames: vec![
                Frame {
                    file: "tests/test_app.py".to_string(),
                    lineno: 10,
                    name: "test_user_code".to_string(),
                    line: "result = helper()".to_string(),
                },
                Frame {
                    file: "oxitest/_bridge/executor.py".to_string(),
                    lineno: 55,
                    name: "_run_base".to_string(),
                    line: "fn()".to_string(),
                },
                Frame {
                    file: "oxitest/_bridge/_middleware.py".to_string(),
                    lineno: 30,
                    name: "_compose".to_string(),
                    line: "wrapper(fn)".to_string(),
                },
            ],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
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
    fn test_diagnostic_long_shows_all_frames() {
        use crate::types::Frame;

        let item = make_item_at("test_user_code", "tests/test_app.py", 10);
        let outcome = TestOutcome::Failed {
            message: "assert x == 1".to_string(),
            file: "tests/test_app.py".to_string(),
            lineno: 10,
            source_line: "assert x == 1".to_string(),
            left: "0".to_string(),
            right: "1".to_string(),
            op: "==".to_string(),
            frames: vec![
                Frame {
                    file: "tests/test_app.py".to_string(),
                    lineno: 10,
                    name: "test_user_code".to_string(),
                    line: "result = helper()".to_string(),
                },
                Frame {
                    file: "oxitest/_bridge/executor.py".to_string(),
                    lineno: 55,
                    name: "_run_base".to_string(),
                    line: "fn()".to_string(),
                },
            ],
        };
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Long, false);
        assert!(
            block.contains("executor.py"),
            "long mode must show internal frames: {block}"
        );
    }

    #[test]
    fn test_filter_frames_fallback_to_last() {
        use crate::types::Frame;

        // When ALL frames are internal, still show the last one
        let frames = vec![Frame {
            file: "oxitest/_bridge/executor.py".to_string(),
            lineno: 10,
            name: "_run_base".to_string(),
            line: "fn()".to_string(),
        }];
        let filtered = filter_frames(&frames, &TbStyle::Short);
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
        let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
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
            let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
            assert_snapshot!(block);
        }

        #[test]
        fn error_with_frames() {
            use crate::types::Frame;

            let item = make_item_at("test_raises", "tests/test_errors.py", 10);
            let outcome = TestOutcome::Error {
                message: "ValueError: invalid input".to_string(),
                file: "tests/test_errors.py".to_string(),
                lineno: 10,
                source_line: "result = process(data)".to_string(),
                frames: vec![
                    Frame {
                        file: "tests/test_errors.py".to_string(),
                        lineno: 10,
                        name: "test_raises".to_string(),
                        line: "result = process(data)".to_string(),
                    },
                    Frame {
                        file: "src/processor.py".to_string(),
                        lineno: 42,
                        name: "process".to_string(),
                        line: "raise ValueError(\"invalid input\")".to_string(),
                    },
                ],
            };
            let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Long, false);
            assert_snapshot!(block);
        }

        #[test]
        fn failed_with_left_right_op() {
            let item = make_item_at("test_equality", "tests/test_values.py", 7);
            let outcome = TestOutcome::Failed {
                message: String::new(),
                file: "tests/test_values.py".to_string(),
                lineno: 7,
                source_line: "assert result == expected".to_string(),
                left: "1".to_string(),
                right: "2".to_string(),
                op: "==".to_string(),
                frames: vec![],
            };
            let block = fmt_diagnostic_block(&item, &outcome, &TbStyle::Short, false);
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
