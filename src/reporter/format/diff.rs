use similar::{ChangeTag, TextDiff};

use crate::reporter::colors::{color_dim, color_fail, color_pass};

/// Returns a character-level diff marker string (carets at the first differing position).
/// Only produced for `==` comparisons on single-line values.
fn char_diff_marker(left: &str, right: &str) -> String {
    let pos = left
        .chars()
        .zip(right.chars())
        .position(|(l, r)| l != r)
        .unwrap_or_else(|| left.len().min(right.len()));
    // Build a line of spaces then a caret
    format!("{}^", " ".repeat(pos))
}

/// Renders a colored inline diff for a pair of single-line left/right values.
fn fmt_single_line_diff(left: &str, right: &str, op: &str, use_color: bool) -> String {
    let left_line = format!("- left:  {}", color_fail(left, use_color));
    let right_line = format!("+ right: {}", color_pass(right, use_color));

    if op == "==" {
        let marker = char_diff_marker(left, right);
        // The marker aligns under the value portion (after "- left:  ")
        let prefix_len = "- left:  ".len();
        let marker_line = format!(
            "{}{}",
            " ".repeat(prefix_len),
            color_dim(&marker, use_color)
        );
        format!("{}\n{}\n{}\n", left_line, right_line, marker_line)
    } else {
        format!("{}\n{}\n", left_line, right_line)
    }
}

/// Renders a unified diff for multi-line left/right values.
fn fmt_multi_line_diff(left: &str, right: &str, use_color: bool) -> String {
    let diff = TextDiff::from_lines(left, right);
    let mut out = String::new();
    for change in diff.iter_all_changes() {
        let line = change.value().trim_end_matches('\n');
        match change.tag() {
            ChangeTag::Delete => {
                out.push_str(&format!(
                    "{}\n",
                    color_fail(&format!("- {line}"), use_color)
                ));
            }
            ChangeTag::Insert => {
                out.push_str(&format!(
                    "{}\n",
                    color_pass(&format!("+ {line}"), use_color)
                ));
            }
            ChangeTag::Equal => {
                out.push_str(&format!("{}\n", color_dim(&format!("  {line}"), use_color)));
            }
        }
    }
    out
}

/// Produces a colored diff string for assertion failures.
///
/// - Returns empty string if both values are empty or identical.
/// - For single-line values: renders `- left: <value>` / `+ right: <value>` inline diff.
/// - For multi-line values: renders a unified diff using `similar`.
/// - For `==` comparisons with single-line values: appends a caret marker at the
///   first differing character position.
pub(crate) fn fmt_diff(left: &str, right: &str, op: &str, use_color: bool) -> String {
    if left.is_empty() && right.is_empty() {
        return String::new();
    }
    if left == right {
        return String::new();
    }

    let left_multiline = left.contains('\n');
    let right_multiline = right.contains('\n');

    if left_multiline || right_multiline {
        fmt_multi_line_diff(left, right, use_color)
    } else {
        fmt_single_line_diff(left, right, op, use_color)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_values_return_empty() {
        assert!(fmt_diff("", "", "==", false).is_empty());
    }

    #[test]
    fn identical_values_return_empty() {
        assert!(fmt_diff("42", "42", "==", false).is_empty());
    }

    #[test]
    fn single_line_diff_shows_left_and_right() {
        let result = fmt_diff("3", "4", "==", false);
        assert!(result.contains("- left:"), "missing left label: {result}");
        assert!(result.contains("3"), "missing left value: {result}");
        assert!(result.contains("+ right:"), "missing right label: {result}");
        assert!(result.contains("4"), "missing right value: {result}");
    }

    #[test]
    fn multi_line_diff_uses_unified_format() {
        let left = "line1\nline2\nline3";
        let right = "line1\nchanged\nline3";
        let result = fmt_diff(left, right, "==", false);
        assert!(result.contains("- line2"), "missing removed line: {result}");
        assert!(result.contains("+ changed"), "missing added line: {result}");
        assert!(result.contains("  line1"), "missing context line: {result}");
    }

    #[test]
    fn char_diff_marks_first_difference() {
        // char_diff is internal but test via fmt_diff output
        let result = fmt_diff("hello", "hxllo", "==", false);
        assert!(result.contains('^'), "missing caret marker: {result}");
    }

    #[test]
    fn char_diff_not_shown_for_non_eq_op() {
        let result = fmt_diff("hello", "hxllo", "!=", false);
        assert!(
            !result.contains('^'),
            "caret should not appear for != op: {result}"
        );
    }
}
