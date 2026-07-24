//! Waivers file loader for the doctest coverage ratchet (wayfinder #1604).
//!
//! Format: plain text, one dotted name per line, `#` inline comments, ASCII
//! whitespace trimmed, empty lines skipped. Missing file = empty set.

use std::collections::{HashMap, HashSet};

use camino::{Utf8Path, Utf8PathBuf};

/// Parsed waivers plus the source path (for diagnostic file/lineno) and the
/// 1-indexed line number of each entry (for stale-entry diagnostics).
#[derive(Debug, Clone)]
pub(crate) struct WaiverSet {
    pub(crate) entries: HashSet<String>,
    pub(crate) path: Utf8PathBuf,
    pub(crate) line_by_entry: HashMap<String, u32>,
}

/// Load and parse `.oxi-doctest-waivers`. Missing file returns an empty
/// set silently (this is the terminal burn-down state, must not spam).
pub(crate) fn load_waivers(path: &Utf8Path) -> WaiverSet {
    let contents = match std::fs::read_to_string(path) {
        Ok(s) => s,
        Err(_) => {
            return WaiverSet {
                entries: HashSet::new(),
                path: path.to_owned(),
                line_by_entry: HashMap::new(),
            };
        }
    };
    let mut entries = HashSet::new();
    let mut line_by_entry = HashMap::new();
    for (idx, raw) in contents.lines().enumerate() {
        let lineno = (idx + 1) as u32;
        let cut = raw.split('#').next().unwrap_or("");
        let name = cut.trim();
        if name.is_empty() {
            continue;
        }
        entries.insert(name.to_owned());
        line_by_entry.entry(name.to_owned()).or_insert(lineno);
    }
    WaiverSet {
        entries,
        path: path.to_owned(),
        line_by_entry,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    fn write(dir: &Utf8Path, name: &str, body: &str) -> Utf8PathBuf {
        let p = dir.join(name);
        fs::write(&p, body).unwrap();
        p
    }

    #[test]
    fn missing_file_returns_empty_set_silently() {
        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        let path = root.join(".oxi-doctest-waivers");
        let set = load_waivers(&path);
        assert!(
            set.entries.is_empty(),
            "missing waivers file must not populate any entries — this is the terminal burn-down state"
        );
        assert_eq!(
            set.path, path,
            "the requested path travels with an empty set so diagnostics can still name it"
        );
    }

    #[test]
    fn parses_one_dotted_name_per_line() {
        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        let p = write(
            &root,
            ".oxi-doctest-waivers",
            "oxitest.approx\noxitest.arrange\noxitest.plugin.Plugin\n",
        );
        let set = load_waivers(&p);
        assert_eq!(
            set.entries.len(),
            3,
            "three non-empty lines ⇒ three entries; got {:?}",
            set.entries
        );
        assert!(set.entries.contains("oxitest.approx"), "first name present");
        assert!(
            set.entries.contains("oxitest.plugin.Plugin"),
            "dotted names with multiple segments preserved verbatim"
        );
    }

    #[test]
    fn inline_hash_comments_are_stripped() {
        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        let p = write(
            &root,
            ".oxi-doctest-waivers",
            "oxitest.approx  # legacy — see #999\n# whole-line comment\noxitest.arrange\n",
        );
        let set = load_waivers(&p);
        assert_eq!(
            set.entries.len(),
            2,
            "whole-line comment must be dropped; inline comment must not include the `#` in the name; got {:?}",
            set.entries
        );
        assert!(
            set.entries.contains("oxitest.approx"),
            "inline `# legacy` comment stripped, leaving the bare name"
        );
    }

    #[test]
    fn empty_and_whitespace_lines_are_skipped() {
        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        let p = write(&root, ".oxi-doctest-waivers", "\n   \noxitest.approx\n\t\n");
        let set = load_waivers(&p);
        assert_eq!(
            set.entries.len(),
            1,
            "blank and whitespace-only lines contribute nothing to the ratchet; got {:?}",
            set.entries
        );
    }

    #[test]
    fn line_numbers_are_one_indexed_and_refer_to_first_occurrence() {
        let tmp = tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        let p = write(
            &root,
            ".oxi-doctest-waivers",
            "# header\noxitest.approx\noxitest.arrange\n",
        );
        let set = load_waivers(&p);
        assert_eq!(
            set.line_by_entry.get("oxitest.approx").copied(),
            Some(2),
            "1-indexed line for stale-entry diagnostics; `oxitest.approx` is on line 2, got: {:?}",
            set.line_by_entry
        );
        assert_eq!(
            set.line_by_entry.get("oxitest.arrange").copied(),
            Some(3),
            "second entry on line 3; got: {:?}",
            set.line_by_entry
        );
    }
}
