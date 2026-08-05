use similar::{ChangeTag, TextDiff};

use crate::colors::{color_dim, color_fail, color_pass};

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
        format!("{left_line}\n{right_line}\n{marker_line}\n")
    } else {
        format!("{left_line}\n{right_line}\n")
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

/// Returns true if `s` looks like a Python collection repr.
///
/// Matches `[...]`, `{...}`, `(...)`, and `frozenset({...})`.
/// Rejects truncated reprs containing `...` and strings shorter than 2 chars.
fn is_collection_repr(s: &str) -> bool {
    if s.len() < 2 || s.contains("...") {
        return false;
    }
    let inner = s
        .strip_prefix("frozenset(")
        .and_then(|r| r.strip_suffix(')'))
        .unwrap_or(s);
    matches!(
        (inner.as_bytes().first(), inner.as_bytes().last()),
        (Some(b'['), Some(b']')) | (Some(b'{'), Some(b'}')) | (Some(b'('), Some(b')'))
    )
}

/// Splits `s` at top-level commas, respecting bracket depth and quoted strings.
///
/// Quote state tracks single and double quoted strings, including escaped quotes.
fn split_top_level(s: &str) -> Vec<&str> {
    let mut parts = Vec::new();
    let mut depth: usize = 0;
    let mut in_single_quote = false;
    let mut in_double_quote = false;
    let mut start = 0;
    let bytes = s.as_bytes();
    let mut i = 0;

    while i < bytes.len() {
        let b = bytes[i];

        // Handle escape sequences inside strings
        if (in_single_quote || in_double_quote) && b == b'\\' {
            i += 2;
            continue;
        }

        if in_single_quote {
            if b == b'\'' {
                in_single_quote = false;
            }
            i += 1;
            continue;
        }

        if in_double_quote {
            if b == b'"' {
                in_double_quote = false;
            }
            i += 1;
            continue;
        }

        match b {
            b'\'' => in_single_quote = true,
            b'"' => in_double_quote = true,
            b'[' | b'{' | b'(' => depth += 1,
            b']' | b'}' | b')' => {
                depth = depth.saturating_sub(1);
            }
            b',' if depth == 0 => {
                parts.push(s[start..i].trim());
                start = i + 1;
            }
            _ => {}
        }
        i += 1;
    }

    // Push the last segment (may be empty for trailing comma)
    let last = s[start..].trim();
    if !last.is_empty() {
        parts.push(last);
    }

    parts
}

/// Reformats a Python collection repr to one element per line.
///
/// Empty collections are returned unchanged. For `frozenset({...})`, the outer
/// `frozenset(` prefix and `)` suffix are preserved around the reformatted inner set.
fn pretty_print_collection(s: &str) -> String {
    // Handle frozenset specially — strip the outer wrapper, reformat inner, re-wrap
    if let Some(inner) = s
        .strip_prefix("frozenset(")
        .and_then(|r| r.strip_suffix(')'))
    {
        let reformatted = pretty_print_collection(inner);
        return format!("frozenset({reformatted})");
    }

    let len = s.len();
    if len < 2 {
        return s.to_owned();
    }

    let open = &s[..1];
    let close = &s[len - 1..];
    let contents = s[1..len - 1].trim();

    if contents.is_empty() {
        return s.to_owned();
    }

    let elements = split_top_level(contents);
    let mut out = String::new();
    out.push_str(open);
    out.push('\n');
    for elem in elements {
        out.push_str(&format!("  {elem},\n"));
    }
    out.push_str(close);
    out
}

/// Renders a field-by-field diff section for dataclass comparisons.
///
/// Each entry is `(field_name, left_repr, right_repr)`.
/// Returns empty string if `field_diffs` is empty.
pub(crate) fn fmt_field_diffs(
    field_diffs: &[crate::types::FieldDiff],
    left_repr: &str,
    use_color: bool,
) -> String {
    if field_diffs.is_empty() {
        return String::new();
    }

    use std::fmt::Write;
    let mut out = String::new();

    // Extract type name from left repr: "User(name=..." → "User"
    let type_name = left_repr
        .find('(')
        .map(|i| &left_repr[..i])
        .unwrap_or("dataclass");

    let name_width = field_diffs
        .iter()
        .map(|diff| diff.field.len())
        .max()
        .unwrap_or(0);

    // Discarded rather than unwrapped: `std::fmt::Write` on a `String` has no
    // failing sink, so there is no error to handle (ADR-0011).
    let _ = writeln!(out, "field diffs ({type_name}):");
    for diff in field_diffs {
        let left_colored = color_fail(&diff.left, use_color);
        let right_colored = color_pass(&diff.right, use_color);
        let _ = writeln!(
            out,
            "  {:<name_width$}  {left_colored} != {right_colored}",
            diff.field,
        );
    }
    out
}

/// Produces a colored diff string for assertion failures.
///
/// - Returns empty string if both values are empty or identical.
/// - For `==` comparisons where both values look like Python collection reprs:
///   pretty-prints each to one-element-per-line and renders a unified diff.
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

    if op == "==" && is_collection_repr(left) && is_collection_repr(right) {
        let left_pp = pretty_print_collection(left);
        let right_pp = pretty_print_collection(right);
        if left_pp != right_pp {
            return fmt_multi_line_diff(&left_pp, &right_pp, use_color);
        }
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
mod snapshot_tests {
    use super::*;

    #[test]
    fn single_line_diff_eq() {
        let result = fmt_diff("42", "43", "==", false);
        insta::assert_snapshot!(result);
    }

    #[test]
    fn multi_line_diff() {
        let result = fmt_diff("line1\nline2\nline3", "line1\nchanged\nline3", "==", false);
        insta::assert_snapshot!(result);
    }

    #[test]
    fn empty_values_returns_empty() {
        let result = fmt_diff("", "", "==", false);
        insta::assert_snapshot!(result);
    }

    #[test]
    fn identical_values_returns_empty() {
        let result = fmt_diff("hello", "hello", "==", false);
        insta::assert_snapshot!(result);
    }

    #[test]
    fn collection_diff_list() {
        let result = fmt_diff("[1, 2, 3]", "[1, 4, 3]", "==", false);
        insta::assert_snapshot!(result);
    }

    #[test]
    fn collection_diff_dict() {
        let result = fmt_diff("{'a': 1, 'b': 2}", "{'a': 1, 'b': 3}", "==", false);
        insta::assert_snapshot!(result);
    }

    #[test]
    fn collection_diff_set() {
        let result = fmt_diff("{1, 2, 3}", "{1, 4, 3}", "==", false);
        insta::assert_snapshot!(result);
    }

    #[test]
    fn collection_diff_nested() {
        let result = fmt_diff("[{'a': 1}, {'b': 2}]", "[{'a': 1}, {'b': 3}]", "==", false);
        insta::assert_snapshot!(result);
    }

    #[test]
    fn field_diffs_two_fields() {
        use crate::types::FieldDiff;
        let diffs = vec![
            FieldDiff {
                field: "email".to_string(),
                left: "'alice@example.com'".to_string(),
                right: "'alice@test.com'".to_string(),
            },
            FieldDiff {
                field: "age".to_string(),
                left: "30".to_string(),
                right: "31".to_string(),
            },
        ];
        let result = fmt_field_diffs(
            &diffs,
            "User(name='alice', email='alice@example.com', age=30)",
            false,
        );
        insta::assert_snapshot!(result);
    }

    #[test]
    fn field_diffs_empty_returns_empty() {
        let result = fmt_field_diffs(&[], "User()", false);
        assert!(result.is_empty());
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

#[cfg(test)]
mod collection_tests {
    use super::*;

    // --- is_collection_repr ---

    #[test]
    fn detects_list() {
        assert!(is_collection_repr("[1, 2, 3]"));
    }

    #[test]
    fn detects_dict() {
        assert!(is_collection_repr("{'a': 1, 'b': 2}"));
    }

    #[test]
    fn detects_set() {
        assert!(is_collection_repr("{1, 2, 3}"));
    }

    #[test]
    fn detects_tuple() {
        assert!(is_collection_repr("(1, 2, 3)"));
    }

    #[test]
    fn detects_frozenset() {
        assert!(is_collection_repr("frozenset({1, 2})"));
    }

    #[test]
    fn rejects_plain_string() {
        assert!(!is_collection_repr("'hello'"));
    }

    #[test]
    fn rejects_number() {
        assert!(!is_collection_repr("42"));
    }

    #[test]
    fn rejects_truncated_list() {
        assert!(!is_collection_repr("[1, 2, ...]"));
    }

    #[test]
    fn rejects_empty_string() {
        assert!(!is_collection_repr(""));
    }

    // --- pretty_print_collection ---

    #[test]
    fn list_one_per_line() {
        assert_eq!(
            pretty_print_collection("[1, 2, 3]"),
            "[\n  1,\n  2,\n  3,\n]"
        );
    }

    #[test]
    fn dict_one_per_line() {
        assert_eq!(
            pretty_print_collection("{'a': 1, 'b': 2}"),
            "{\n  'a': 1,\n  'b': 2,\n}"
        );
    }

    #[test]
    fn nested_collection_not_expanded() {
        assert_eq!(
            pretty_print_collection("[{'a': [1, 2]}, 'hello']"),
            "[\n  {'a': [1, 2]},\n  'hello',\n]"
        );
    }

    #[test]
    fn string_with_comma_not_split() {
        assert_eq!(
            pretty_print_collection("['hello, world', 'foo']"),
            "[\n  'hello, world',\n  'foo',\n]"
        );
    }

    #[test]
    fn empty_list_unchanged() {
        assert_eq!(pretty_print_collection("[]"), "[]");
    }

    #[test]
    fn single_element_list() {
        assert_eq!(pretty_print_collection("[42]"), "[\n  42,\n]");
    }

    #[test]
    fn frozenset_formatted() {
        assert_eq!(
            pretty_print_collection("frozenset({1, 2})"),
            "frozenset({\n  1,\n  2,\n})"
        );
    }

    #[test]
    fn tuple_one_per_line() {
        assert_eq!(
            pretty_print_collection("(1, 2, 3)"),
            "(\n  1,\n  2,\n  3,\n)"
        );
    }
}
