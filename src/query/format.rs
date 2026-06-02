//! Output formatters for query results.
//!
//! Three formats are provided:
//! - [`format_columnar`] — aligned columns for TTY output
//! - [`format_tab`] — tab-delimited for piped/scripted output
//! - [`format_jsonl`] — JSON lines, one object per entry

use crate::query::resource::QueryEntry;

// ── Columnar ──────────────────────────────────────────────────────────────────

/// Render entries as aligned columns suitable for TTY display.
///
/// Columns are padded to the widest value in each column plus a 2-space gap.
/// The last column is never padded. An empty result produces `"no results\n"`.
/// A footer `"\n{count} {result|results}\n"` is appended.
pub(crate) fn format_columnar(entries: &[QueryEntry], columns: &[&str]) -> String {
    if entries.is_empty() {
        return "no results\n".to_string();
    }

    // Compute per-column max widths (header name vs each value).
    let mut widths: Vec<usize> = columns.iter().map(|c| c.len()).collect();
    for entry in entries {
        for (i, col) in columns.iter().enumerate() {
            let val_len = entry.get(col).unwrap_or("").len();
            if val_len > widths[i] {
                widths[i] = val_len;
            }
        }
    }

    let mut out = String::new();
    let last = columns.len().saturating_sub(1);

    for entry in entries {
        for (i, col) in columns.iter().enumerate() {
            let val = entry.get(col).unwrap_or("");
            if i < last {
                // pad to width + 2-space gap
                let pad = widths[i] + 2;
                out.push_str(&format!("{:<width$}", val, width = pad));
            } else {
                out.push_str(val);
            }
        }
        out.push('\n');
    }

    let count = entries.len();
    let word = if count == 1 { "result" } else { "results" };
    out.push_str(&format!("\n{count} {word}\n"));

    out
}

// ── Tab-delimited ─────────────────────────────────────────────────────────────

/// Render entries as tab-delimited lines.
///
/// Each entry produces one line with column values joined by `\t`. No footer
/// is appended, making the output suitable for piping into other tools.
pub(crate) fn format_tab(entries: &[QueryEntry], columns: &[&str]) -> String {
    if entries.is_empty() {
        return "no results\n".to_string();
    }
    let mut out = String::new();
    for entry in entries {
        let line: Vec<&str> = columns
            .iter()
            .map(|col| entry.get(col).unwrap_or(""))
            .collect();
        out.push_str(&line.join("\t"));
        out.push('\n');
    }
    out
}

// ── JSON lines ────────────────────────────────────────────────────────────────

/// Render entries as JSON lines (one JSON object per line).
///
/// Each entry's full field map is serialized as a JSON object. Callers can
/// redirect this output to files or pipe it into `jq`.
pub(crate) fn format_jsonl(entries: &[QueryEntry]) -> String {
    let mut out = String::new();
    for entry in entries {
        // Serialize via serde_json. Use BTreeMap ordering for determinism.
        let obj: std::collections::BTreeMap<&str, &str> = entry
            .fields
            .iter()
            .map(|(k, v)| (k.as_str(), v.as_str()))
            .collect();
        // serde_json::to_string on a BTreeMap<&str,&str> never fails.
        let line = serde_json::to_string(&obj).unwrap_or_else(|_| "{}".to_string());
        out.push_str(&line);
        out.push('\n');
    }
    out
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn entry(pairs: &[(&str, &str)]) -> QueryEntry {
        let fields: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        QueryEntry { fields }
    }

    // ── format_columnar ────────────────────────────────────────────────────────

    #[test]
    fn columnar_basic() {
        let entries = vec![
            entry(&[("name", "test_foo"), ("source", "tests/test_a.py")]),
            entry(&[("name", "test_bar"), ("source", "tests/test_b.py")]),
        ];
        let out = format_columnar(&entries, &["name", "source"]);
        assert!(out.contains("test_foo"), "should contain test_foo");
        assert!(out.contains("test_bar"), "should contain test_bar");
    }

    #[test]
    fn columnar_aligns_columns() {
        // "short" and "a_longer_name" differ in length; both source values
        // should start at the same column offset.
        let entries = vec![
            entry(&[("name", "short"), ("source", "tests/a.py")]),
            entry(&[("name", "a_longer_name"), ("source", "tests/b.py")]),
        ];
        let out = format_columnar(&entries, &["name", "source"]);
        let lines: Vec<&str> = out.lines().collect();
        // Find the lines that contain "tests/"
        let source_lines: Vec<&&str> = lines.iter().filter(|l| l.contains("tests/")).collect();
        assert_eq!(source_lines.len(), 2, "should have two data lines");
        // The source column should start at the same byte offset on both lines
        // (max name width = len("a_longer_name") + 2-space gap = 15).
        let pos0 = source_lines[0].find("tests/").unwrap();
        let pos1 = source_lines[1].find("tests/").unwrap();
        assert_eq!(pos0, pos1, "source column should be aligned: {out:?}");
    }

    #[test]
    fn columnar_empty() {
        let out = format_columnar(&[], &["name", "source"]);
        assert_eq!(out, "no results\n");
    }

    #[test]
    fn columnar_footer_singular() {
        let entries = vec![entry(&[("name", "test_only"), ("source", "a.py")])];
        let out = format_columnar(&entries, &["name", "source"]);
        assert!(
            out.contains("1 result"),
            "footer should say '1 result', got: {out:?}"
        );
        assert!(!out.contains("1 results"), "should not say '1 results'");
    }

    #[test]
    fn columnar_footer_plural() {
        let entries = vec![
            entry(&[("name", "test_a"), ("source", "a.py")]),
            entry(&[("name", "test_b"), ("source", "b.py")]),
        ];
        let out = format_columnar(&entries, &["name", "source"]);
        assert!(
            out.contains("2 results"),
            "footer should say '2 results', got: {out:?}"
        );
    }

    // ── format_tab ─────────────────────────────────────────────────────────────

    #[test]
    fn tab_delimited() {
        let entries = vec![entry(&[("name", "test_foo"), ("source", "a.py")])];
        let out = format_tab(&entries, &["name", "source"]);
        assert_eq!(out.trim(), "test_foo\ta.py");
    }

    #[test]
    fn tab_no_footer() {
        let entries = vec![
            entry(&[("name", "test_a"), ("source", "a.py")]),
            entry(&[("name", "test_b"), ("source", "b.py")]),
        ];
        let out = format_tab(&entries, &["name", "source"]);
        // No "result" or "results" footer
        assert!(
            !out.contains("result"),
            "tab format should not have footer, got: {out:?}"
        );
    }

    // ── format_jsonl ───────────────────────────────────────────────────────────

    #[test]
    fn jsonl_format() {
        let entries = vec![entry(&[("name", "test_foo"), ("source", "a.py")])];
        let out = format_jsonl(&entries);
        // Each line must be valid JSON
        let line = out.trim();
        let parsed: serde_json::Value = serde_json::from_str(line).expect("should be valid JSON");
        assert_eq!(parsed["name"], "test_foo");
        assert_eq!(parsed["source"], "a.py");
    }

    // ── Snapshot tests ────────────────────────────────────────────────────────

    #[test]
    fn snap_columnar_tests() {
        let entries = vec![
            entry(&[("name", "test_add"), ("mark", "slow"), ("async", "false")]),
            entry(&[
                ("name", "test_network_call"),
                ("mark", "network,slow"),
                ("async", "true"),
            ]),
            entry(&[("name", "test_plain"), ("mark", ""), ("async", "false")]),
        ];
        insta::assert_snapshot!(format_columnar(&entries, &["name", "mark", "async"]));
    }

    #[test]
    fn snap_tab_tests() {
        let entries = vec![
            entry(&[("name", "test_add"), ("mark", "slow"), ("async", "false")]),
            entry(&[("name", "test_sub"), ("mark", ""), ("async", "false")]),
        ];
        insta::assert_snapshot!(format_tab(&entries, &["name", "mark", "async"]));
    }

    #[test]
    fn snap_jsonl_tests() {
        let entries = vec![
            entry(&[("name", "test_foo"), ("source", "tests/test_a.py")]),
            entry(&[("name", "test_bar"), ("source", "tests/test_b.py")]),
        ];
        insta::assert_snapshot!(format_jsonl(&entries));
    }
}
